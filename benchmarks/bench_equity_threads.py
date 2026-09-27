"""Does pokerfast.equity need a free-threaded Python to scale across threads?

ctypes releases the GIL for the duration of each native call, so the C++ work
should run in parallel on ANY Python; only the Python around each call (input
checks, building the ctypes arrays) is serialised by the GIL. This measures
how much that matters, per workload.

The A/B isolates the GIL and nothing else: the SAME free-threaded interpreter
run twice, once with PYTHON_GIL=0 and once with PYTHON_GIL=1. Rounds are
interleaved and the arm order alternates, and the reported number is each
arm's SCALING (throughput at N threads / at 1 thread, within one child
process), which a busy machine affects far less than absolute rates.

    PYTHON_GIL=0 python benchmarks/bench_equity_threads.py --threads 1,2,4 --rounds 4

Run it with a free-threaded python (3.13t/3.14t). Workloads:

  river1    one river query per call    -- per-call Python overhead dominates
  river256  256 river queries per call  -- the batched API
  flop      heads-up exact equity on a flop, one call (~ms of C++ each)
"""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time

DECK = [r + s for r in '23456789TJQKA' for s in 'cdhs']


# ------------------------------------------------------------------ child
def _queries(rng, n, nboard):
    out = []
    for _ in range(n):
        c = rng.sample(DECK, 2 + nboard)
        out.append((''.join(c[:2]), ''.join(c[2:])))
    return out


def _workloads():
    from pokerfast import equity as E
    rng = random.Random(0)
    river = _queries(rng, 4096, 5)
    batch = [river[i:i + 256] for i in range(0, len(river), 256)]
    flops = []
    for _ in range(64):
        c = rng.sample(DECK, 7)
        flops.append(([''.join(c[0:2]), ''.join(c[2:4])], ''.join(c[4:7])))

    def river1(i):
        h, b = river[i % len(river)]
        E.equity_vs_random(h, b)
        return 1

    def river256(i):
        E.equity_vs_random_many(batch[i % len(batch)])
        return 256

    def flop(i):
        r, b = flops[i % len(flops)]
        E.equity(r, board=b)
        return 1

    return {'river1': river1, 'river256': river256, 'flop': flop}


def _rate(fn, nthreads, seconds):
    """Queries/s with `nthreads` threads each calling fn for `seconds`."""
    stop = time.perf_counter() + seconds
    counts = [0] * nthreads
    start = threading.Barrier(nthreads + 1)

    def work(k):
        start.wait()
        i, n = k * 7919, 0
        while time.perf_counter() < stop:
            n += fn(i)
            i += 1
        counts[k] = n

    ts = [threading.Thread(target=work, args=(k,)) for k in range(nthreads)]
    for t in ts:
        t.start()
    t0 = time.perf_counter()
    start.wait()
    for t in ts:
        t.join()
    return sum(counts) / (time.perf_counter() - t0)


def child(threads, seconds):
    from pokerfast import equity as E
    assert E.available(), 'OMPEval library not available'
    out = {'gil': sys._is_gil_enabled(), 'rates': {}}
    for name, fn in _workloads().items():
        fn(0)                                   # warm
        out['rates'][name] = {n: _rate(fn, n, seconds) for n in threads}
    print(json.dumps(out))


# ----------------------------------------------------------------- driver
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threads', default='1,2,4')
    ap.add_argument('--rounds', type=int, default=4)
    ap.add_argument('--seconds', type=float, default=1.5)
    ap.add_argument('--child', action='store_true')
    a = ap.parse_args()
    threads = [int(x) for x in a.threads.split(',')]
    if a.child:
        return child(threads, a.seconds)

    if not hasattr(sys, '_is_gil_enabled'):
        sys.exit('run this with a free-threaded Python (3.13t / 3.14t)')
    arms = ('0', '1')
    per = {g: [] for g in arms}
    for r in range(a.rounds):
        order = arms if r % 2 == 0 else arms[::-1]
        for g in order:
            env = dict(os.environ, PYTHON_GIL=g)
            res = subprocess.run(
                [sys.executable, __file__, '--child', '--threads', a.threads,
                 '--seconds', str(a.seconds)],
                env=env, capture_output=True, text=True, check=True)
            d = json.loads(res.stdout.strip().splitlines()[-1])
            assert d['gil'] == (g == '1'), d
            per[g].append(d['rates'])
        print('round %d/%d done' % (r + 1, a.rounds), file=sys.stderr)

    label = {'0': 'free-threaded', '1': 'GIL on'}
    print('scaling = throughput at N threads / at 1 thread, median of %d '
          'interleaved rounds (per-round values in brackets)\n' % a.rounds)
    for w in per['0'][0]:
        print(w)
        for g in arms:
            cells = []
            for n in threads[1:]:
                s = [rd[w][str(n)] / rd[w]['1'] for rd in per[g]]
                cells.append('%dT %.2fx [%s]' % (
                    n, statistics.median(s), ' '.join('%.2f' % x for x in s)))
            base = statistics.median(rd[w]['1'] for rd in per[g])
            print('  %-14s 1T %9.0f q/s   %s' % (label[g], base,
                                               '   '.join(cells)))
        print()


if __name__ == '__main__':
    main()
