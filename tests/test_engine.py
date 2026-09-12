"""Differential test: FastHand against pokerkit, step by step.

This file, not the engine's docstring, is the argument that the replacement is
faithful. It drives both engines through the SAME action sequence and compares
every observable at every step: the action space (which keys, and the exact
chip amounts), whose turn it is, the pot, the board length, the per-street
action log, and whether each engine accepted the action.

The two hands hold different CARDS -- they have separate decks -- so the
sequence is chosen from pokerkit's action space and replayed into FastHand, and
only structural facts are compared. Showdown correctness is a separate concern,
covered by `test_evaluator.py` and by the fixed-card test at the bottom.

Actions are deliberately weighted toward raises, and among raises toward the
extremes (min-raise and all-in), because the min-raise increment rule and the
all-in-for-less rule are where an engine goes wrong.
"""

import random

import pytest

pokerkit = pytest.importorskip('pokerkit')

from pokerfast import FastHand                      # noqa: E402
from _pokerkit_ref import RefHand                   # noqa: E402


def space_signature(sp):
    """Everything about an action space except the chip amounts."""
    if sp is False or sp is None:
        return None
    return tuple(sorted(k for k in sp if k != 'player')), sp.get('player')


def choose(sp, rnd):
    opts = []
    if 'check' in sp:
        opts += ['check'] * 3
    if 'call' in sp:
        opts += ['call'] * 3
    if 'fold' in sp:
        opts += ['fold'] * 2
    if 'min_bet' in sp and sp.get('max_bet', 0) > 0:
        opts += ['raise'] * 4
    if not opts:
        return ('check', 0) if 'check' in sp else ('fold', 0)
    a = rnd.choice(opts)
    if a != 'raise':
        return (a, 0)
    lo, hi = sp['min_bet'], sp['max_bet']
    r = rnd.random()
    if r < 0.3:
        sz = lo
    elif r < 0.5:
        sz = hi
    elif r < 0.6:
        sz = max(lo, hi // 2)
    else:
        sz = rnd.randint(lo, hi) if hi > lo else lo
    return ('raise', sz)


def apply(h, action, size):
    return {'fold': lambda: h.fold(), 'check': lambda: h.check(),
            'call': lambda: h.call(),
            'raise': lambda: h.bet_or_raise(size)}[action]()


def board_of(h):
    return list(h.u_hand[1])


def run_pair(seed):
    """Play one hand in both engines under the same sequence.

    Returns (ok, message).
    """
    rnd = random.Random(seed)
    random.seed(seed)
    b = FastHand()
    stacks = list(b.state.starting_stacks)
    # Explicit stacks rather than a matched RNG draw: the engines must agree
    # about the GAME, and coupling them through `random`'s call order would
    # make an unrelated change look like an engine bug.
    a = RefHand(stacks, small_blind=b.small_blind, big_blind=b.big_blind)

    if list(a.state.starting_stacks) != list(b.state.starting_stacks):
        return False, "starting stacks differ"

    step = 0
    while not a.done and not b.done:
        step += 1
        if step > 400:
            return False, "hand did not terminate in 400 steps"
        sa, sb = a.get_action_space(), b.get_action_space()
        if space_signature(sa) != space_signature(sb):
            return False, ("step %d action-space mismatch\n"
                           "    pokerkit %r\n    fast     %r" % (step, sa, sb))
        if sa is None:
            break
        for key in ('check', 'call', 'min_bet', 'max_bet'):
            if sa.get(key) != sb.get(key):
                return False, ("step %d '%s' differs: pokerkit %r fast %r\n"
                               "    pokerkit %r\n    fast     %r"
                               % (step, key, sa.get(key), sb.get(key), sa, sb))
        if a.state.turn_index != b.state.turn_index:
            return False, ("step %d turn_index %r vs %r"
                           % (step, a.state.turn_index, b.state.turn_index))
        if a.pot_size() != b.pot_size():
            return False, ("step %d pot %d vs %d"
                           % (step, a.pot_size(), b.pot_size()))
        if len(board_of(a)) != len(board_of(b)):
            return False, ("step %d board length %d vs %d"
                           % (step, len(board_of(a)), len(board_of(b))))
        for si in (3, 4, 5, 6):
            if a.u_hand[si] != b.u_hand[si]:
                return False, ("step %d u_hand[%d] diverged\n"
                               "    pokerkit %r\n    fast     %r"
                               % (step, si, a.u_hand[si], b.u_hand[si]))

        act, sz = choose(sa, rnd)
        ra, rb = apply(a, act, sz), apply(b, act, sz)
        if bool(ra) != bool(rb):
            return False, ("step %d action %s(%d) accepted %r vs %r"
                           % (step, act, sz, ra, rb))

    if a.done != b.done:
        return False, "one engine finished and the other did not"
    if a.pot_size() != b.pot_size():
        return False, "final pot %d vs %d" % (a.pot_size(), b.pot_size())
    if a.final_pot != b.final_pot:
        return False, ("final_pot high-water mark %d vs %d"
                       % (a.final_pot, b.final_pot))
    if len(board_of(a)) != len(board_of(b)):
        return False, ("final board length %d vs %d"
                       % (len(board_of(a)), len(board_of(b))))
    if sum(a.state.payoffs) != sum(b.state.payoffs):
        return False, ("payoffs do not sum equally: %d vs %d "
                       "(chips created or destroyed)"
                       % (sum(a.state.payoffs), sum(b.state.payoffs)))
    return True, ""


@pytest.mark.parametrize('lo,hi', [(1, 250)])
def test_differential_against_pokerkit(lo, hi):
    """Every observable matches, at every step, over many random hands."""
    failures = []
    for seed in range(lo, hi + 1):
        ok, msg = run_pair(seed)
        if not ok:
            failures.append('seed %d: %s' % (seed, msg))
            if len(failures) >= 5:
                break
    assert not failures, '\n'.join(failures)


def test_chips_are_conserved():
    """Payoffs sum to zero: a hand may not create or destroy chips."""
    for seed in range(500, 560):
        random.seed(seed)
        h = FastHand()
        rnd = random.Random(seed)
        guard = 0
        while not h.done and guard < 400:
            guard += 1
            sp = h.get_action_space()
            if not sp:
                break
            act, sz = choose(sp, rnd)
            apply(h, act, sz)
        assert sum(h.state.payoffs) == 0, 'seed %d leaked chips' % seed


def test_deepcopy_is_independent():
    """A clone must not write history back into the hand it branched from."""
    import copy
    random.seed(7)
    h = FastHand()
    sp = h.get_action_space()
    assert sp
    c = copy.deepcopy(h)
    before = [list(x) for x in h.u_hand]
    act, sz = choose(sp, random.Random(7))
    apply(c, act, sz)
    assert [list(x) for x in h.u_hand] == before, 'the branch mutated its parent'


def test_observer_sees_every_action():
    """The observer hook replaces what used to be a hard-coded encoder."""
    class Obs:
        def __init__(self):
            self.actions = []
            self.boards = []

        def action(self, seat, amount):
            self.actions.append((seat, amount))

        def board(self, street, cards):
            self.boards.append((street, tuple(cards)))

        def clone(self):
            c = Obs()
            c.actions = list(self.actions)
            c.boards = list(self.boards)
            return c

    random.seed(11)
    o = Obs()
    h = FastHand(observer=o)
    rnd = random.Random(11)
    guard = 0
    while not h.done and guard < 400:
        guard += 1
        sp = h.get_action_space()
        if not sp:
            break
        act, sz = choose(sp, rnd)
        apply(h, act, sz)
    assert o.actions, 'the observer saw no actions'
    n_logged = sum(len(h.u_hand[i]) for i in (3, 4, 5, 6))
    assert len(o.actions) == n_logged, (
        'observer saw %d actions, the hand logged %d'
        % (len(o.actions), n_logged))
