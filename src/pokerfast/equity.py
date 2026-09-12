"""Exact hold'em equity, via a long-lived OMPEval subprocess.

OMPEval is a C++ evaluator and equity calculator (ISC licensed, see NOTICE).
It is a git submodule rather than vendored source; `native/build.sh` fetches
it, applies `native/patches/`, and builds `ompeval_batch`, a small driver that
serves queries over stdin/stdout instead of one per process.

That framing is the whole point. A single-query process spends ~19 ms of its
~26 ms building an 86,547-entry lookup table it then throws away; keeping one
process alive amortises that over the whole session.

    >>> from pokerfast.equity import EquityCalculator
    >>> with EquityCalculator() as eq:
    ...     eq.equity('AhKd', '2c3d4h5s6c')     # hero vs one random hand
    ...     eq.equity_many([('AhKd', '2c3d4h5s6c'), ('7c7d', '')])

If the binary has not been built, constructing an `EquityCalculator` raises
`EquityUnavailable` with the build command in the message. Nothing else in
pokerfast needs it -- this is an optional extra.
"""

import os
import shutil
import subprocess
import threading

__all__ = ['EquityCalculator', 'EquityUnavailable', 'find_binary']

_ENV = 'POKERFAST_OMPEVAL'


class EquityUnavailable(RuntimeError):
    """The ompeval_batch binary was not found, or died."""


def find_binary():
    """Path to `ompeval_batch`, or None.

    Looks at $POKERFAST_OMPEVAL, then the default build directory, then PATH.
    """
    env = os.environ.get(_ENV)
    if env and os.path.isfile(env):
        return env

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..'))
    for rel in ('native/build/ompeval_batch',
                'native/build/ompeval_batch.exe',
                'native/build/Release/ompeval_batch.exe'):
        p = os.path.join(root, *rel.split('/'))
        if os.path.isfile(p):
            return p

    return shutil.which('ompeval_batch')


class EquityCalculator:
    """A persistent `ompeval_batch` process.

    Not thread-safe by accident -- it is made so on purpose, with a lock around
    the write/read pair, because the protocol is strictly one response per
    request and interleaving two callers would hand each the other's answer.
    For real parallelism run several instances; the helper is single-threaded
    by design.
    """

    def __init__(self, binary=None):
        self._bin = binary or find_binary()
        if not self._bin:
            raise EquityUnavailable(
                "ompeval_batch not found. Build it with:\n"
                "    git submodule update --init --recursive\n"
                "    bash native/build.sh\n"
                "or set %s to an existing binary." % _ENV)
        self._lock = threading.Lock()
        self._p = subprocess.Popen(
            [self._bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    # ------------------------------------------------------------------ api
    def equity(self, hole, board=''):
        """Hero's equity against ONE uniformly random opponent hand.

        `hole` is four characters ('AhKd'); `board` is 0, 3, 4 or 5 cards
        ('2c3d4h5s6c'). Returns a float in [0, 1].
        """
        return self.equity_many([(hole, board)])[0]

    def equity_many(self, queries):
        """`queries` is an iterable of (hole, board). Returns a list of floats.

        One round trip for the whole batch, which is the reason this module
        exists -- see the module docstring.
        """
        qs = [self._format(h, b) for h, b in queries]
        if not qs:
            return []
        with self._lock:
            self._check_alive()
            try:
                self._p.stdin.write(''.join(q + '\n' for q in qs))
                self._p.stdin.flush()
                out = []
                for _ in qs:
                    line = self._p.stdout.readline()
                    if not line:
                        raise EquityUnavailable(
                            'ompeval_batch closed its output early')
                    out.append(float(line.strip()))
            except (BrokenPipeError, OSError) as e:
                raise EquityUnavailable('ompeval_batch died: %s' % e)
        return out

    # -------------------------------------------------------------- internals
    @staticmethod
    def _format(hole, board):
        hole = (hole or '').strip()
        board = (board or '').strip()
        if len(hole) != 4:
            raise ValueError('hole must be exactly two cards, got %r' % hole)
        if board and len(board) % 2:
            raise ValueError('board must be whole cards, got %r' % board)
        return '%s|%s' % (hole, board) if board else hole

    def _check_alive(self):
        if self._p.poll() is not None:
            raise EquityUnavailable(
                'ompeval_batch exited with code %s' % self._p.returncode)

    def close(self):
        if getattr(self, '_p', None) is None:
            return
        try:
            if self._p.poll() is None:
                self._p.stdin.close()
                self._p.wait(timeout=5)
        except Exception:
            self._p.kill()
        finally:
            self._p = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
