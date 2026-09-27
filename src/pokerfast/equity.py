"""Exact hold'em equity and 7-card evaluation, via OMPEval compiled in.

OMPEval is a C++ evaluator and equity calculator (ISC licensed, see NOTICE).
pokerfast ships it as a small shared library with a plain C ABI, loaded with
ctypes, inside the platform wheels on PyPI. Because it is ctypes rather than a
CPython extension, one build per OS/CPU serves every Python version,
free-threaded ones included.

    >>> from pokerfast import equity
    >>> equity.equity_vs_random('AhKd', '2c3d4h5s6c')        # vs one random hand
    >>> equity.equity(['AhKd', 'QQ+,AKs', 'random'], board='2c3d4h')
    >>> equity.evaluate([('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d')])

Every call releases the GIL (ctypes does that) and touches no shared mutable
state, so threads calling in parallel really do run in parallel.

On a platform without a prebuilt wheel, pip installs the pure-Python wheel
instead; everything else in pokerfast works, and these functions raise
`EquityUnavailable`. Build the library from a source checkout with CMake (see
the README) and point $POKERFAST_OMPEVAL_LIB at it.
"""

import ctypes
import math
import os
import re
import sys
import threading

__all__ = [
    'equity', 'equity_vs_random', 'equity_vs_random_many', 'evaluate',
    'available', 'find_library', 'EquityUnavailable', 'EquityCalculator',
    'MAX_PLAYERS',
]

_ENV = 'POKERFAST_OMPEVAL_LIB'
_ABI_VERSION = 1
MAX_PLAYERS = 6

_OK, _ERR_ARGS, _ERR_IMPOSSIBLE, _ERR_INTERNAL = 0, -1, -2, -3

_CARDS_RE = re.compile(r'^(?:[2-9TJQKAtjqka][cdhsCDHS])*$')
# OMPEval's card index is 4 * rank + suit, suits ordered s, h, c, d.
_RANK = {c: i for i, c in enumerate('23456789TJQKA')}
_RANK.update({c.lower(): i for c, i in list(_RANK.items())})
_SUIT = {'s': 0, 'h': 1, 'c': 2, 'd': 3}


class EquityUnavailable(RuntimeError):
    """The OMPEval library is not available on this install."""


def _lib_filename():
    if sys.platform == 'win32':
        return 'pokerfast_omp.dll'
    if sys.platform == 'darwin':
        return 'pokerfast_omp.dylib'
    return 'pokerfast_omp.so'


def find_library():
    """Path to the OMPEval library, or None.

    Looks at $POKERFAST_OMPEVAL_LIB, then inside the installed package.
    """
    env = os.environ.get(_ENV)
    if env and os.path.isfile(env):
        return env
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_native',
                     _lib_filename())
    return p if os.path.isfile(p) else None


_lib = None
_lib_error = None
_load_lock = threading.Lock()


def _load():
    global _lib, _lib_error
    if _lib is not None:
        return _lib
    with _load_lock:
        if _lib is not None:
            return _lib
        if _lib_error is not None:
            raise EquityUnavailable(_lib_error)
        path = find_library()
        if path is None:
            _lib_error = (
                "pokerfast's OMPEval library is not installed: no prebuilt "
                "wheel exists for this platform. Build it from a source "
                "checkout (see the README, 'Building the native library') and "
                "set %s to the result." % _ENV)
            raise EquityUnavailable(_lib_error)
        try:
            lib = ctypes.CDLL(path)
        except OSError as e:
            _lib_error = 'could not load %s: %s' % (path, e)
            raise EquityUnavailable(_lib_error)

        lib.pf_abi_version.restype = ctypes.c_int
        lib.pf_abi_version.argtypes = []
        abi = lib.pf_abi_version()
        if abi != _ABI_VERSION:
            _lib_error = ('%s has ABI version %d, this pokerfast expects %d'
                          % (path, abi, _ABI_VERSION))
            raise EquityUnavailable(_lib_error)

        lib.pf_init.restype = ctypes.c_int
        lib.pf_init.argtypes = []
        lib.pf_equity.restype = ctypes.c_int
        lib.pf_equity.argtypes = [
            ctypes.POINTER(ctypes.c_char_p), ctypes.c_uint, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_double, ctypes.c_uint,
            ctypes.c_double, ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_uint64)]
        lib.pf_equity_vs_random.restype = ctypes.c_int
        lib.pf_equity_vs_random.argtypes = [
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_uint, ctypes.POINTER(ctypes.c_double)]
        lib.pf_evaluate.restype = ctypes.c_int
        lib.pf_evaluate.argtypes = [
            ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint16)]

        if lib.pf_init() != _OK:
            _lib_error = 'OMPEval failed to initialise its tables'
            raise EquityUnavailable(_lib_error)
        _lib = lib
        return lib


def available():
    """True if the OMPEval library loads on this install."""
    try:
        _load()
        return True
    except EquityUnavailable:
        return False


# ------------------------------------------------------------------ helpers
def _cards(s, what):
    """Validate a card string ('2c3d4h'); returns it encoded."""
    s = (s or '').strip()
    if len(s) % 2 or not _CARDS_RE.match(s):
        raise ValueError('%s must be whole cards like "2c3d4h", got %r'
                         % (what, s))
    ids = [s[i:i + 2].lower() for i in range(0, len(s), 2)]
    if len(set(ids)) != len(ids):
        raise ValueError('%s repeats a card: %r' % (what, s))
    return s.encode('ascii')


def _check(rc, what):
    if rc == _OK:
        return
    if rc == _ERR_ARGS:
        raise ValueError('%s: malformed arguments' % what)
    if rc == _ERR_IMPOSSIBLE:
        raise ValueError('%s: no valid deal (every combination conflicts '
                         'with the board, dead cards or another range)' % what)
    raise RuntimeError('%s: OMPEval internal error (%d)' % (what, rc))


# ---------------------------------------------------------------------- api
def equity(ranges, board='', dead='', *, exact=True, stdev=5e-5, threads=1,
           time_limit=0.0):
    """Each player's equity, for 1..6 players.

    `ranges` is a list of OMPEval range strings, one per player: an exact hand
    ('AhKd'), a range ('QQ+,AKs,T9s'), or 'random'. `board` is 0..5 cards,
    `dead` any cards removed from the deck.

    exact=True enumerates every deal; exact=False is Monte Carlo, stopping
    when the standard error of player 1's equity falls below `stdev`, or after
    `time_limit` seconds if that is set. `threads=0` uses every core.

    Returns a list of floats, one per player, summing to 1.
    """
    ranges = list(ranges)
    if not 1 <= len(ranges) <= MAX_PLAYERS:
        raise ValueError('between 1 and %d ranges, got %d'
                         % (MAX_PLAYERS, len(ranges)))
    if not exact and stdev <= 0 and time_limit <= 0:
        raise ValueError('Monte Carlo needs stdev > 0 or a time_limit, '
                         'otherwise it never stops')
    b = _cards(board, 'board')
    if len(b) > 10:
        raise ValueError('board has at most 5 cards, got %r' % board)
    d = _cards(dead, 'dead')
    lib = _load()
    n = len(ranges)
    rs = (ctypes.c_char_p * n)(*[str(r).encode('ascii') for r in ranges])
    out = (ctypes.c_double * n)()
    hands = ctypes.c_uint64()
    rc = lib.pf_equity(rs, n, b, d, 1 if exact else 0, float(stdev),
                       int(threads), float(time_limit), out,
                       ctypes.byref(hands))
    _check(rc, 'equity(%r, board=%r)' % (ranges, board))
    return list(out)


def equity_vs_random(hole, board=''):
    """Hero's exact equity against ONE uniformly random opponent hand.

    `hole` is two cards ('AhKd'); `board` is 0, 3, 4 or 5 cards.
    """
    return equity_vs_random_many([(hole, board)])[0]


def equity_vs_random_many(queries):
    """`queries` is an iterable of (hole, board). Returns a list of floats.

    One native call for the whole batch, single-threaded, exact.
    """
    qs = [(_cards(h, 'hole'), _cards(b, 'board')) for h, b in queries]
    for h, b in qs:
        if len(h) != 4:
            raise ValueError('hole must be exactly two cards, got %r' % h)
        if len(b) not in (0, 6, 8, 10):
            raise ValueError('board must be 0, 3, 4 or 5 cards, got %r' % b)
    if not qs:
        return []
    lib = _load()
    n = len(qs)
    holes = (ctypes.c_char_p * n)(*[h for h, _ in qs])
    boards = (ctypes.c_char_p * n)(*[b for _, b in qs])
    out = (ctypes.c_double * n)()
    rc = lib.pf_equity_vs_random(holes, boards, n, out)
    if rc != _OK:
        bad = next(i for i in range(n) if math.isnan(out[i]))
        _check(rc, 'equity_vs_random query %d (%r|%r)'
               % (bad, qs[bad][0].decode(), qs[bad][1].decode()))
    return list(out)


def evaluate(hands):
    """Rank hands of up to 7 cards each with OMPEval's evaluator.

    `hands` is an iterable of card sequences (('As', 'Ks', ...)) or card
    strings ('AsKs...'); all of them must have the same number of cards.
    Returns a list of ints, bigger is better, equal is a split. As with
    `pokerfast.eval7`, only the ORDER is meaningful -- and the numbers are not
    the ones eval7 returns, so never compare the two.
    """
    rows = []
    k = None
    for h in hands:
        if isinstance(h, str):
            h = [h[i:i + 2] for i in range(0, len(h), 2)]
        row = []
        for c in h:
            if len(c) != 2 or c[0] not in _RANK or c[1].lower() not in _SUIT:
                raise ValueError('bad card %r' % (c,))
            row.append(4 * _RANK[c[0]] + _SUIT[c[1].lower()])
        if len(set(row)) != len(row):
            raise ValueError('hand repeats a card: %r' % (h,))
        if k is None:
            k = len(row)
            if k > 7:
                raise ValueError('at most 7 cards per hand, got %d' % k)
        elif len(row) != k:
            raise ValueError('every hand must have %d cards, got %r' % (k, h))
        rows.append(row)
    if not rows:
        return []
    lib = _load()
    n = len(rows)
    buf = bytes(c for row in rows for c in row)
    out = (ctypes.c_uint16 * n)()
    _check(lib.pf_evaluate(buf, n, k, out), 'evaluate')
    return list(out)


class EquityCalculator:
    """The 0.1 interface, kept for compatibility.

    It used to own a subprocess; the library needs no per-instance state, so
    this is now a thin wrapper and `close()` does nothing. Prefer the module
    functions.
    """

    def __init__(self, binary=None):
        # `binary` named the 0.1 subprocess; accepted and ignored.
        _load()

    def equity(self, hole, board=''):
        return equity_vs_random(hole, board)

    def equity_many(self, queries):
        return equity_vs_random_many(queries)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
