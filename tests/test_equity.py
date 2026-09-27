"""The native OMPEval library must agree with brute force.

The reference is pokerfast's own `eval7`, which test_evaluator.py already
proves order-exact against pokerkit. So:

  * `evaluate` must induce the same pairwise order as eval7, ties included;
  * exact equities must equal an enumeration of every runout scored with
    eval7, to floating-point precision, for heads-up and multiway spots.

If the library is missing these tests SKIP -- except where
POKERFAST_REQUIRE_NATIVE=1, which CI sets for every platform that ships a
wheel, so a wheel that silently lost its library cannot pass.
"""

import itertools
import os
import random
import threading

import pytest

from pokerfast import eval7, equity as E

DECK = [r + s for r in '23456789TJQKA' for s in 'cdhs']

if not E.available():
    if os.environ.get('POKERFAST_REQUIRE_NATIVE') == '1':
        raise RuntimeError('POKERFAST_REQUIRE_NATIVE=1 but the OMPEval '
                           'library did not load: %s' % E.find_library())
    pytest.skip('OMPEval library not available', allow_module_level=True)


def _split(s):
    return [s[i:i + 2] for i in range(0, len(s), 2)]


def _brute(holes, board, dead=()):
    """Exact equities of fixed hole cards, enumerating every runout."""
    used = set(board) | set(dead)
    for h in holes:
        used.update(h)
    rest = [c for c in DECK if c not in used]
    eq = [0.0] * len(holes)
    n = 0
    for runout in itertools.combinations(rest, 5 - len(board)):
        b = list(board) + list(runout)
        ranks = [eval7(tuple(h) + tuple(b)) for h in holes]
        best = max(ranks)
        winners = [i for i, r in enumerate(ranks) if r == best]
        for i in winners:
            eq[i] += 1.0 / len(winners)
        n += 1
    return [x / n for x in eq]


def _brute_vs_random(hole, board):
    used = set(hole) | set(board)
    rest = [c for c in DECK if c not in used]
    total = 0.0
    n = 0
    for villain in itertools.combinations(rest, 2):
        total += _brute([hole, list(villain)], board)[0]
        n += 1
    return total / n


# ------------------------------------------------------------- evaluator
def test_evaluate_order_matches_eval7():
    rng = random.Random(1)
    hands = [tuple(rng.sample(DECK, 7)) for _ in range(4000)]
    ours = E.evaluate(hands)
    ref = [eval7(h) for h in hands]
    ties = 0
    for i in range(0, len(hands) - 1, 2):
        a = (ours[i] > ours[i + 1]) - (ours[i] < ours[i + 1])
        b = (ref[i] > ref[i + 1]) - (ref[i] < ref[i + 1])
        assert a == b, (hands[i], hands[i + 1])
        ties += a == 0

    # Every category, rare ones included, in strictly increasing order.
    ladder = [
        ('2c', '4d', '6h', '8s', 'Tc', 'Qd', '3h'),   # high card
        ('2c', '2d', '6h', '8s', 'Tc', 'Qd', '3h'),   # pair
        ('2c', '2d', '6h', '6s', 'Tc', 'Qd', '3h'),   # two pair
        ('2c', '2d', '2h', '8s', 'Tc', 'Qd', '3h'),   # trips
        ('Ac', '2d', '3h', '4s', '5c', 'Qd', '9h'),   # wheel
        ('6c', '2d', '3h', '4s', '5c', 'Qd', '9h'),   # six-high straight
        ('2c', '4c', '6c', '8c', 'Tc', 'Qd', '3h'),   # flush
        ('2c', '2d', '2h', '8s', '8c', 'Qd', '3h'),   # full house
        ('2c', '2d', '2h', '2s', 'Tc', 'Qd', '3h'),   # quads
        ('Ac', '2c', '3c', '4c', '5c', 'Qd', '9h'),   # steel wheel
        ('Ts', 'Js', 'Qs', 'Ks', 'As', '2c', '3d'),   # royal
    ]
    vals = E.evaluate(ladder)
    assert vals == sorted(vals) and len(set(vals)) == len(vals)


def test_evaluate_accepts_strings_and_rejects_junk():
    assert E.evaluate(['AsKsQsJsTs2c3d']) == E.evaluate(
        [('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d')])
    with pytest.raises(ValueError):
        E.evaluate([('As', 'As', 'Qs', 'Js', 'Ts')])
    with pytest.raises(ValueError):
        E.evaluate([('Xs', 'Ks', 'Qs', 'Js', 'Ts')])
    with pytest.raises(ValueError):
        E.evaluate([('As', 'Ks'), ('As', 'Ks', 'Qs')])


# ---------------------------------------------------------------- equity
@pytest.mark.parametrize('seed', range(6))
def test_vs_random_river_and_turn_match_brute_force(seed):
    rng = random.Random(seed)
    cards = rng.sample(DECK, 7)
    hole = cards[:2]
    for nboard in (5, 4):
        board = cards[2:2 + nboard]
        got = E.equity_vs_random(''.join(hole), ''.join(board))
        assert got == pytest.approx(_brute_vs_random(hole, board), abs=1e-12)


def test_batch_matches_single_calls():
    rng = random.Random(7)
    qs = []
    for _ in range(50):
        c = rng.sample(DECK, 7)
        qs.append((''.join(c[:2]), ''.join(c[2:2 + rng.choice((0, 3, 4, 5))])))
    batch = E.equity_vs_random_many(qs)
    assert batch == [E.equity_vs_random(h, b) for h, b in qs]


@pytest.mark.parametrize('seed', range(4))
def test_exact_multiway_matches_brute_force(seed):
    rng = random.Random(100 + seed)
    nplayers = 2 + seed % 3                   # 2, 3, 4 players
    nboard = 3 if nplayers == 2 else 4        # keep the brute force quick
    cards = rng.sample(DECK, 2 * nplayers + nboard)
    holes = [cards[2 * i:2 * i + 2] for i in range(nplayers)]
    board = cards[2 * nplayers:]
    got = E.equity([''.join(h) for h in holes], board=''.join(board))
    want = _brute(holes, board)
    assert got == pytest.approx(want, abs=1e-12)
    assert sum(got) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize('dead', ['Ks', 'KsKc', '3s', 'Qh8h'])
def test_dead_cards_match_brute_force(dead):
    holes = [['Ah', 'Ad'], ['Kh', 'Kd']]
    board = ['2c', '7d', '9h']
    got = E.equity(['AhAd', 'KhKd'], board='2c7d9h', dead=dead)
    assert got == pytest.approx(_brute(holes, board, _split(dead)), abs=1e-12)


def test_range_narrows_to_live_combos():
    # With KsKc dead, the range KK is exactly KhKd.
    b = E.equity(['AhAd', 'KK'], board='2c7d9h', dead='KsKc')
    c = E.equity(['AhAd', 'KhKd'], board='2c7d9h', dead='KsKc')
    assert b == pytest.approx(c, abs=1e-12)


def test_monte_carlo_is_close_to_exact():
    exact = E.equity(['AhKd', 'QsQc', 'random'])
    mc = E.equity(['AhKd', 'QsQc', 'random'], exact=False, stdev=2e-4,
                  threads=0)
    assert mc == pytest.approx(exact, abs=3e-3)


def test_bad_inputs_raise_value_error():
    for call in (
        lambda: E.equity_vs_random('AhK', ''),
        lambda: E.equity_vs_random('AhAh', ''),
        lambda: E.equity_vs_random('AhKd', 'Ah2c3c'),       # hole on board
        lambda: E.equity_vs_random('AhKd', '2c3c'),         # 2-card board
        lambda: E.equity(['AhKd', 'AhQd']),                 # impossible
        lambda: E.equity(['AhKd'] * 7),                     # > 6 players
        lambda: E.equity(['AhKd', 'QQ'], exact=False, stdev=0),
    ):
        with pytest.raises(ValueError):
            call()


def test_parallel_calls_agree():
    """ctypes drops the GIL; concurrent calls must not share state."""
    rng = random.Random(3)
    qs = []
    for _ in range(40):
        c = rng.sample(DECK, 7)
        qs.append((''.join(c[:2]), ''.join(c[2:7])))
    want = E.equity_vs_random_many(qs)
    results = [None] * 8
    errors = []

    def work(i):
        try:
            results[i] = E.equity_vs_random_many(qs)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert all(r == want for r in results)


def test_compat_calculator():
    with E.EquityCalculator() as eq:
        assert eq.equity('AhKd', '2c3d4h5s6c') == E.equity_vs_random(
            'AhKd', '2c3d4h5s6c')
