"""pokerfast vs pokerkit, paired and interleaved, across thread counts.

WHY IT IS SHAPED LIKE THIS

Absolute throughput on a laptop is not reproducible: thermal decay, other
processes and the scheduler move it by more than most optimisations are worth.
So this never reports a bare number as a result. Every round runs BOTH engines
back to back on the same machine in the same state, alternates which goes
first, and reports the RATIO -- which survives a busy machine, because
contention hits both arms.

Read the per-round ratios, not just the median. If they disagree with each
other, the machine was too noisy and the median is meaningless.

THREADS

Scaling is the interesting part, and it only exists on a free-threaded build.
Run it under CPython 3.13t/3.14t with PYTHON_GIL=0; with the GIL on, every
thread count collapses to roughly the same throughput and the table says so.

    PYTHON_GIL=0 python benchmarks/benchmark.py
    PYTHON_GIL=0 python benchmarks/benchmark.py --threads 1,2,4 --rounds 3
"""

import argparse
import os
import random
import statistics
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'src'))
# The pokerkit reference driver lives with the differential tests, because that
# is what it exists for; the benchmark borrows it so both compare against the
# same thing.
sys.path.insert(0, os.path.join(_ROOT, 'tests'))

from pokerfast import FastHand, eval7           # noqa: E402
from _pokerkit_ref import RefHand               # noqa: E402
from pokerkit.hands import StandardHighHand     # noqa: E402

RANKS, SUITS = '23456789TJQKA', 'cdhs'
DECK = [r + s for r in RANKS for s in SUITS]


# --------------------------------------------------------------- workloads
#
# Every workload runs to a DEADLINE and reports how much it finished, rather
# than doing a fixed count. A fixed count has to be calibrated per engine, and
# getting that wrong gives the slow arm a fraction of a second of work, which
# is how a paired ratio ends up with 100% dispersion across rounds.

def play_fast(deadline, seed):
    rnd = random.Random(seed)
    n = 0
    while time.perf_counter() < deadline:
        h = FastHand(rng=rnd)
        guard = 0
        while not h.done and guard < 400:
            guard += 1
            sp = h.get_action_space()
            if not sp:
                break
            _act(h, sp, rnd)
        n += 1
    return n


def play_pokerkit(deadline, seed):
    rnd = random.Random(seed)
    n = 0
    while time.perf_counter() < deadline:
        stacks = [rnd.randint(4000, 20000) for _ in range(6)]
        h = RefHand(stacks)
        guard = 0
        while not h.done and guard < 400:
            guard += 1
            sp = h.get_action_space()
            if not sp:
                break
            _act(h, sp, rnd)
        n += 1
    return n


def _act(h, sp, rnd):
    """One action, weighted the same for both engines."""
    if 'min_bet' in sp and sp.get('max_bet', 0) > 0 and rnd.random() < 0.25:
        lo, hi = sp['min_bet'], sp['max_bet']
        return h.bet_or_raise(lo if rnd.random() < 0.5 else
                              (rnd.randint(lo, hi) if hi > lo else lo))
    if 'check' in sp:
        return h.check()
    if 'call' in sp and rnd.random() < 0.8:
        return h.call()
    return h.fold() if 'fold' in sp else h.check()


def eval_fast(deadline, seed):
    rnd = random.Random(seed)
    hands = [tuple(rnd.sample(DECK, 7)) for _ in range(256)]
    n = 0
    while time.perf_counter() < deadline:
        for i in range(2000):                 # amortise the clock read
            eval7(hands[i & 255])
        n += 2000
    return n


def eval_pokerkit(deadline, seed):
    rnd = random.Random(seed)
    hands = [tuple(rnd.sample(DECK, 7)) for _ in range(256)]
    n = 0
    while time.perf_counter() < deadline:
        for i in range(200):
            h = hands[i & 255]
            StandardHighHand.from_game(''.join(h[:2]), ''.join(h[2:]))
        n += 200
    return n


# ------------------------------------------------------------------ runner
def rate(fn, secs, threads):
    """Units per second, running `threads` workers for `secs` each."""
    done = [0] * threads
    deadline = time.perf_counter() + secs

    def run(i):
        done[i] = fn(deadline, 1000 + i)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(threads)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return sum(done) / (time.perf_counter() - t0)


def compare(name, fast_fn, slow_fn, secs, threads, rounds):
    """Interleaved, order alternating. Both arms get the SAME wall budget."""
    fast_rates, slow_rates = [], []
    for r in range(rounds):
        order = ('fast', 'slow') if r % 2 == 0 else ('slow', 'fast')
        for which in order:
            if which == 'fast':
                fast_rates.append(rate(fast_fn, secs, threads))
            else:
                slow_rates.append(rate(slow_fn, secs, threads))
    ratios = [f / s for f, s in zip(fast_rates, slow_rates)]
    return (statistics.median(ratios), statistics.median(fast_rates),
            statistics.median(slow_rates), ratios)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threads', default='1,2,4,8')
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--secs', type=float, default=3.0,
                    help='wall budget per arm per round')
    a = ap.parse_args()

    gil = getattr(sys, '_is_gil_enabled', lambda: True)()
    print('python %s | GIL %s | cpus %s'
          % (sys.version.split()[0], 'ON' if gil else 'OFF', os.cpu_count()))
    if gil:
        print('WARNING: the GIL is enabled, so the thread columns measure')
        print('         contention, not scaling. Re-run with PYTHON_GIL=0 on a')
        print('         free-threaded build.')
    print('paired and interleaved, order alternating, %d rounds per cell\n'
          % a.rounds)

    threads = [int(x) for x in a.threads.split(',')]

    print('%-8s %14s %14s %10s   %s'
          % ('threads', 'pokerfast/s', 'pokerkit/s', 'SPEEDUP', 'per-round'))
    print('-- full hands played to completion ' + '-' * 34)
    hand_rows = []
    for t in threads:
        ratio, f, s, rs = compare('hands', play_fast, play_pokerkit,
                                  a.secs, t, a.rounds)
        hand_rows.append((t, f, s, ratio))
        print('%-8d %14.0f %14.0f %9.1fx   %s'
              % (t, f, s, ratio, ' '.join('%.1f' % x for x in rs)))

    print('-- 7-card evaluation ' + '-' * 48)
    for t in threads:
        ratio, f, s, rs = compare('eval', eval_fast, eval_pokerkit,
                                  a.secs, t, a.rounds)
        print('%-8d %14.0f %14.0f %9.1fx   %s'
              % (t, f, s, ratio, ' '.join('%.1f' % x for x in rs)))

    print('\n-- scaling of the full-hand workload --')
    base_f = hand_rows[0][1]
    base_s = hand_rows[0][2]
    print('%-8s %16s %16s' % ('threads', 'pokerfast', 'pokerkit'))
    for t, f, s, _ in hand_rows:
        print('%-8d %15.2fx %15.2fx' % (t, f / base_f, s / base_s))
    print('\n(scaling is relative to each engine\'s OWN 1-thread rate, so a')
    print(' number below the thread count is sublinearity, not slowness.)')


if __name__ == '__main__':
    main()
