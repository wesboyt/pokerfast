"""The fast evaluator must ORDER hands exactly as pokerkit does.

Callers only ever compare ranks (`max`, `==`) and never inspect the value, so
the evaluator does not have to reproduce pokerkit's numbers -- it has to
reproduce its ORDER, including ties, because a tie is a split pot and a
spurious tie or a missed one silently misallocates money.

So this compares the induced pairwise order on random 7-card hands:

    sign(fast(a) - fast(b))  ==  sign(pokerkit(a) - pokerkit(b))

with ties counted separately, so "passes" cannot be achieved by an evaluator
that calls everything equal. Every hand CATEGORY is also swept explicitly --
straight flush down to high card, plus the wheel and the steel wheel -- because
random 7-card deals almost never produce the rare ones.
"""

import itertools
import random

import pytest

from pokerfast import eval7, RANKS, SUITS

DECK = [r + s for r in RANKS for s in SUITS]


def _pokerkit_rank():
    pytest.importorskip('pokerkit')
    from pokerkit.hands import StandardHighHand

    def rank(cards):
        # StandardHighHand orders by its own comparison, not by a number we can
        # subtract, so compare the objects themselves.
        return StandardHighHand.from_game(''.join(cards[:2]),
                                          ''.join(cards[2:]))
    return rank


def test_order_matches_pokerkit_on_random_hands():
    rank = _pokerkit_rank()
    rnd = random.Random(20260912)
    hands = []
    for _ in range(1500):
        hands.append(tuple(rnd.sample(DECK, 7)))

    fast = [eval7(h) for h in hands]
    slow = [rank(h) for h in hands]

    ties_fast = ties_slow = 0
    mismatches = []
    for i in range(0, len(hands) - 1, 2):
        j = i + 1
        f = (fast[i] > fast[j]) - (fast[i] < fast[j])
        s = (slow[i] > slow[j]) - (slow[i] < slow[j])
        if f == 0:
            ties_fast += 1
        if s == 0:
            ties_slow += 1
        if f != s:
            mismatches.append((hands[i], hands[j], f, s))

    assert not mismatches, (
        '%d pairwise order mismatches, first: %r vs %r (fast %d, pokerkit %d)'
        % (len(mismatches), mismatches[0][0], mismatches[0][1],
           mismatches[0][2], mismatches[0][3]))
    # An evaluator that returned a constant would pass the comparison above.
    assert ties_fast == ties_slow, (
        'tie counts differ: fast %d, pokerkit %d' % (ties_fast, ties_slow))


CATEGORY_CASES = {
    'straight flush': ('9s', '8s', '7s', '6s', '5s', '2c', '3d'),
    'steel wheel':    ('As', '2s', '3s', '4s', '5s', 'Kc', 'Qd'),
    'royal flush':    ('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d'),
    'quads':          ('9s', '9h', '9d', '9c', '5s', '2c', '3d'),
    'full house':     ('9s', '9h', '9d', '5c', '5s', '2c', '3d'),
    'flush':          ('As', 'Js', '9s', '6s', '3s', '2c', '4d'),
    'straight':       ('9s', '8h', '7d', '6c', '5s', '2c', '3d'),
    'wheel':          ('As', '2h', '3d', '4c', '5s', 'Kc', 'Qd'),
    'trips':          ('9s', '9h', '9d', '6c', '5s', '2c', '3d'),
    'two pair':       ('9s', '9h', '6d', '6c', '5s', '2c', '3d'),
    'pair':           ('9s', '9h', 'Kd', '6c', '5s', '2c', '3d'),
    'high card':      ('As', 'Jh', '9d', '6c', '5s', '2c', '3d'),
}


def test_every_category_orders_like_pokerkit():
    """Random deals almost never produce the rare categories, so sweep them."""
    rank = _pokerkit_rank()
    names = list(CATEGORY_CASES)
    for a, b in itertools.combinations(names, 2):
        ha, hb = CATEGORY_CASES[a], CATEGORY_CASES[b]
        fa, fb = eval7(ha), eval7(hb)
        sa, sb = rank(ha), rank(hb)
        f = (fa > fb) - (fa < fb)
        s = (sa > sb) - (sa < sb)
        assert f == s, ('%s vs %s: fast says %d, pokerkit says %d' % (a, b, f, s))


def test_known_orderings():
    """A handful of orderings that hold regardless of any implementation."""
    r = {k: eval7(v) for k, v in CATEGORY_CASES.items()}
    assert r['royal flush'] > r['straight flush'] > r['quads']
    assert r['quads'] > r['full house'] > r['flush'] > r['straight']
    assert r['straight'] > r['trips'] > r['two pair'] > r['pair'] > r['high card']
    # The wheel is the LOWEST straight: 5-high, below 9-high.
    assert r['wheel'] < r['straight']
    assert r['steel wheel'] < r['straight flush']


def test_case_and_order_insensitive():
    h = ('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d')
    base = eval7(h)
    assert eval7(tuple(reversed(h))) == base
    assert eval7(tuple(c[0] + c[1].upper() for c in h)) == base
