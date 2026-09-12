"""Memoisation for pokerkit's hand-evaluation path.

Three independent, individually installable patches, each with its own
equivalence check:

  `install()`             a tuple fast path for `Card.clean`, which is called
                          on every evaluation and spends most of its time
                          re-parsing already-parsed cards.
  `install_key_cache()`   memoises `Lookup._get_key` on the 5-card multiset.
  `install_from_game_cache()`
                          memoises `<HandType>.from_game` on (hole, board).

Each has a `verify*()` that compares patched against unpatched results on
random input, and each `uninstall`s cleanly. Nothing in site-packages is
modified -- these are runtime monkey-patches that live and die with the
process.

Prefer `pokerfast.patches.install_all()`, which runs the version guard first.
"""
import os

_INSTALLED = False
_ORIGINAL = None


def install():
    """Patch Card.clean with a tuple fast path. Idempotent, reversible."""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED or os.environ.get('POKERFAST_PATCH_EVAL', '1') != '1':
        return False
    from pokerkit.utilities import Card

    original = Card.clean.__func__          # unwrap the classmethod

    def clean(cls, values):
        # The only case we handle specially. Anything else -> original.
        if type(values) is tuple:
            return values
        return original(cls, values)

    _ORIGINAL = Card.clean
    Card.clean = classmethod(clean)
    _INSTALLED = True
    return True


def uninstall():
    global _INSTALLED, _ORIGINAL
    if not _INSTALLED:
        return False
    from pokerkit.utilities import Card
    Card.clean = _ORIGINAL
    _INSTALLED = False
    return True


def is_installed():
    return _INSTALLED


def verify():
    """The fast path must agree with the original on every input shape.

    Returns (n_checked, [disagreements]). Run with the patch installed.
    """
    from pokerkit.utilities import Card
    if not _INSTALLED:
        return 0, ['not installed']
    original = _ORIGINAL.__func__
    cards = tuple(Card.parse('AsKdQhJc9s2h'))
    cases = [
        cards,                       # tuple  -> fast path
        list(cards),                 # list   -> original
        cards[0],                    # Card   -> original
        'AsKd',                      # str    -> original
        (),                          # empty tuple -> fast path
        iter(cards),                 # iterator -> original
        set(cards[:2]),              # set    -> original
    ]
    bad, n = [], 0
    for c in cases:
        # iterators are single-use; rebuild for each side
        a_in = iter(cards) if hasattr(c, '__next__') else c
        b_in = iter(cards) if hasattr(c, '__next__') else c
        try:
            a = Card.clean(a_in)
        except Exception as e:
            a = f'raise:{type(e).__name__}'
        try:
            b = original(Card, b_in)
        except Exception as e:
            b = f'raise:{type(e).__name__}'
        n += 1
        # set iteration order is arbitrary; compare as multisets there
        same = (sorted(map(repr, a)) == sorted(map(repr, b))
                if isinstance(a, tuple) and isinstance(b, tuple) else a == b)
        if not same:
            bad.append(f"{type(c).__name__}: fast={a!r} orig={b!r}")
    return n, bad


# ---------------------------------------------------------------------------
# Memoise the 5-card lookup within a showdown.
#
# Measured on one 6-player showdown: Lookup._get_key is called 2,174 times for
# 121 DISTINCT 5-card sets -- 18.0x redundancy. The distinct count is exactly
# the combinatorial requirement (6 players x C(7,5) = 126), so pokerkit is not
# doing extra work, it is doing the SAME work eighteen times: once per pairwise
# comparison / side-pot pass rather than once per hand.
#
# A 5-card set maps to one key deterministically, so this is a pure function
# and safe to cache. The cache lives ON THE LOOKUP INSTANCE, not globally:
# _get_key closes over self.__hash, so two Lookup instances could in principle
# disagree, and a per-instance dict cannot mix them up. It also dies with the
# instance instead of growing forever.
#
# Bounded because C(52,5) = 2.6M sets exist, far more than we would ever want
# resident. The working set inside one showdown is ~121, so even a small cap
# captures essentially all the redundancy.
# ---------------------------------------------------------------------------
_MISS = object()
_CACHE_CAP = int(os.environ.get('POKERFAST_EVAL_CACHE', '65536'))
_KEY_INSTALLED = False
_KEY_ORIGINAL = None


def install_key_cache():
    global _KEY_INSTALLED, _KEY_ORIGINAL
    if _KEY_INSTALLED or os.environ.get('POKERFAST_PATCH_EVAL', '1') != '1':
        return False
    # Separate gate: once from_game is cached the redundancy is absorbed a
    # level up, and _get_key drops from 2,875 to ~200 calls per showdown. At
    # that volume the frozenset key may cost more than it saves, so this must
    # be independently switchable to be measurable. POKERFAST_KEY_CACHE=0 to disable.
    if os.environ.get('POKERFAST_KEY_CACHE', '1') != '1':
        return False
    from pokerkit.lookups import Lookup
    original = Lookup._get_key

    def _get_key(self, cards):
        key = frozenset(cards)
        # A frozenset collapses duplicates. Poker hands have none, but if a
        # caller ever passes a repeated card the key would be ambiguous, so
        # fall through rather than answer from a key that lost information.
        if len(key) != len(cards):
            return original(self, cards)
        cache = getattr(self, '_pokerfast_key_cache', None)
        if cache is None:
            cache = {}
            self._pokerfast_key_cache = cache
        v = cache.get(key, _MISS)
        if v is _MISS:
            v = original(self, cards)
            if len(cache) < _CACHE_CAP:
                cache[key] = v
        return v

    _KEY_ORIGINAL = original
    Lookup._get_key = _get_key
    _KEY_INSTALLED = True
    return True


def verify_key_cache(trials=300):
    """Cached and uncached _get_key must agree on random 5-card sets."""
    import itertools
    import random as _r
    from pokerkit.lookups import StandardLookup
    from pokerkit.utilities import Card
    if not _KEY_INSTALLED:
        return 0, ['key cache not installed']
    lk = StandardLookup()
    deck = list(Card.parse(
        ''.join(r + s for r in '23456789TJQKA' for s in 'shdc')))
    bad, n = [], 0
    rng = _r.Random(20250101)
    for _ in range(trials):
        cards = tuple(rng.sample(deck, 5))
        a = lk._get_key(cards)                    # cached path
        b = _KEY_ORIGINAL(lk, cards)              # original
        n += 1
        if a != b:
            bad.append(f"{[repr(c) for c in cards]}: {a} != {b}")
        # same set, different order, must give the same answer
        shuffled = tuple(rng.sample(cards, 5))
        if lk._get_key(shuffled) != b:
            bad.append(f"order-dependence on {[repr(c) for c in cards]}")
    return n, bad


# ---------------------------------------------------------------------------
# Cache one level UP: the whole best-hand-from-7-cards computation.
#
# The 5-card _get_key cache above removed the 18x repetition but paid a
# frozenset build (5 Card.__hash__ calls) on EVERY call. Re-profiling showed
# that key construction had become the single largest cost in a showdown --
# 14,473 Card.__hash__ calls each. The cache was at the wrong level.
#
# from_game(hole, board) is called 82 times per 6-player showdown for ~6
# distinct arguments, and each call enumerates C(7,5)=21 candidate hands,
# constructs a Hand for each (which itself does a has_entry lookup), and takes
# the max. Caching THERE collapses the enumeration, the Hand construction and
# every _get_key beneath it, for 82 key builds instead of 2,875.
#
# Hand objects are immutable value objects (constructed from cards, never
# mutated), so handing the same instance back to two callers is safe for the
# same reason sharing Cards is.
# ---------------------------------------------------------------------------
_FG_INSTALLED = False
_FG_ORIGINALS = {}
_FG_CACHE = {}
_FG_CAP = int(os.environ.get('POKERFAST_FROMGAME_CACHE', '32768'))
_FG_STATS = {'hit': 0, 'miss': 0}


def install_from_game_cache():
    """Patch EVERY class in pokerkit.hands that defines from_game.

    The first attempt patched only the base Hand. StandardHighHand resolves
    from_game to CombinationHand.from_game (MRO: StandardHighHand ->
    StandardHand -> CombinationHand -> Hand), so the base patch was shadowed
    and the cache recorded literally zero hits and zero misses. Five classes
    define their own from_game; each needs its own wrapper closing over its
    own original.
    """
    global _FG_INSTALLED
    if _FG_INSTALLED or os.environ.get('POKERFAST_PATCH_EVAL', '1') != '1':
        return 0
    if os.environ.get('POKERFAST_FROMGAME', '1') != '1':
        return 0
    import pokerkit.hands as H
    from pokerkit.utilities import Card
    n = 0
    for cls in list(vars(H).values()):
        if not (isinstance(cls, type) and 'from_game' in vars(cls)):
            continue
        raw = vars(cls)['from_game']
        if not isinstance(raw, classmethod):
            continue
        original = raw.__func__
        _FG_ORIGINALS[cls] = original

        def make(original):
            def from_game(c, hole_cards, board_cards=()):
                # Card.clean CONSUMES an iterator. Building the cache key from
                # the raw arguments therefore exhausted any generator pokerkit
                # passed, and the original then saw an empty hand -- which is
                # why the first version of this cache silently changed play
                # while a tuple-only unit test still passed. Materialise once
                # and hand the SAME tuples to the original.
                try:
                    hole_t = Card.clean(hole_cards)
                    board_t = Card.clean(board_cards)
                    key = (c, hole_t, board_t)
                except Exception:
                    return original(c, hole_cards, board_cards)
                v = _FG_CACHE.get(key, _MISS)
                if v is _MISS:
                    _FG_STATS['miss'] += 1
                    v = original(c, hole_t, board_t)
                    if len(_FG_CACHE) >= _FG_CAP:
                        _FG_CACHE.clear()
                    _FG_CACHE[key] = v
                else:
                    _FG_STATS['hit'] += 1
                return v
            return from_game

        setattr(cls, 'from_game', classmethod(make(original)))
        n += 1
    _FG_INSTALLED = n > 0
    return n


def from_game_stats():
    t = _FG_STATS['hit'] + _FG_STATS['miss']
    return dict(_FG_STATS, total=t,
                hit_rate=(_FG_STATS['hit'] / t if t else 0.0))


def verify_from_game(trials=200):
    """Cached from_game must equal the uncached one on random hole/board pairs."""
    import random as _r
    from pokerkit.hands import StandardHighHand
    from pokerkit.utilities import Card
    if not _FG_INSTALLED:
        return 0, ['from_game cache not installed']
    # StandardHighHand resolves to CombinationHand.from_game -- compare against
    # THAT original, not the base Hand one (which returns None for it).
    original = None
    for cls in StandardHighHand.__mro__:
        if cls in _FG_ORIGINALS:
            original = _FG_ORIGINALS[cls]
            break
    if original is None:
        return 0, ['no original recorded for StandardHighHand']
    deck = list(Card.parse(
        ''.join(r + s for r in '23456789TJQKA' for s in 'shdc')))
    rng = _r.Random(777)
    bad, n = [], 0
    for _ in range(trials):
        cs = rng.sample(deck, 7)
        hole, board = tuple(cs[:2]), tuple(cs[2:])
        a = StandardHighHand.from_game(hole, board)
        b = original(StandardHighHand, hole, board)
        n += 1
        if a != b or repr(a) != repr(b):
            bad.append(f"{[repr(c) for c in cs]}: {a!r} != {b!r}")
        if StandardHighHand.from_game(hole, board) != b:
            bad.append(f"second call differs for {[repr(c) for c in cs]}")
    return n, bad


def is_key_cache_installed():
    return _KEY_INSTALLED


def is_from_game_installed():
    return _FG_INSTALLED


def uninstall_all():
    """Restore pokerkit exactly. Used when the startup self-test fails.

    Correctness beats throughput: an unpatched run is ~2x slower and right,
    a wrongly-patched one is fast and produces corrupted training data.
    """
    global _KEY_INSTALLED, _FG_INSTALLED
    restored = []
    try:
        uninstall()
        restored.append('Card.clean')
    except Exception:
        pass
    if _KEY_INSTALLED and _KEY_ORIGINAL is not None:
        from pokerkit.lookups import Lookup
        Lookup._get_key = _KEY_ORIGINAL
        _KEY_INSTALLED = False
        restored.append('Lookup._get_key')
    if _FG_INSTALLED:
        for cls, original in _FG_ORIGINALS.items():
            setattr(cls, 'from_game', classmethod(original))
        _FG_ORIGINALS.clear()
        _FG_CACHE.clear()
        _FG_INSTALLED = False
        restored.append('from_game')
    return restored
