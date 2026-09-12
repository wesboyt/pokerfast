"""A purpose-built 6-max no-limit hold'em engine.

WHAT IT IS

pokerkit is a general, validating framework for dozens of poker variants, and
that generality costs a lot per action. This implements exactly one game --
6-max NLHE, cash mode, fixed blinds, one runout, automated blind posting and
chip pushing -- where the whole state is a handful of integer lists, an action
is a few additions, and a snapshot is a list copy.

Measured against pokerkit on CPython 3.13, per call:

    deal a hand          932 us  ->   ~25 us
    deepcopy a hand      361 us  ->    ~9 us
    check / call     338 / 323 us ->   ~2 us
    bet_or_raise         153 us  ->    ~2 us

WHY YOU CAN TRUST IT

`tests/test_engine.py` is a DIFFERENTIAL test, and it -- not this docstring --
is the argument for correctness. It drives this engine and pokerkit through the
same random action sequences and compares the action space, turn order, pot,
board and final payoffs at EVERY step.

THE RULES THAT ACTUALLY MATTER

The betting model below is pokerkit's, read out of `pokerkit/state.py`, because
the goal is behavioural identity with it. Three places where the textbook
version is WRONG, each found by the differential test:

  1. A raise ALWAYS puts every other live player back in the queue. pokerkit
     rebuilds its actor deque from scratch on every raise. The textbook "an
     all-in for less than a full raise does not reopen the action" is about the
     right to RAISE, not the obligation to act -- a player facing a short
     all-in must still call or fold.
  2. Whether a player may RAISE again is governed by three tracked values:
     `_cbra` (the largest raise increment so far this street), `_consec` (the
     increments of CONSECUTIVE all-in raises, cleared by any non-all-in raise),
     and `_acted` (who has acted since the last FULL raise). Raising is refused
     when the accumulated short all-ins still do not add up to a full raise and
     the player has already acted. Consecutive short all-ins ACCUMULATE, and
     once their sum reaches a full raise the action reopens.
  3. The minimum raise-to is capped by the EFFECTIVE STACK -- you cannot be
     required to raise more than the largest amount any remaining opponent can
     cover. This is what makes a min-raise against a short-stacked opponent far
     smaller than `max_bet + min_raise`.

Side pots are settled in layers by committed amount, with odd chips to the
earliest seat.

OBSERVERS

Pass `observer=` to watch the action stream without re-deriving it. The object
needs `action(seat, amount)`, `board(street_index, cards)` and `clone()`;
`clone()` is called on deepcopy so a branch cannot write history into the
position it branched from. Amounts are -1 for a fold, 0 for a check, and the
chips put in otherwise.
"""

import os
import random

# Table-driven 7-card evaluation, ~50x quicker than routing every showdown
# through pokerkit's generic lookup. Order-exact against it over 150,000
# pairwise comparisons including ties. POKERFAST_TABLE_EVAL=0 falls back.

_USE_FAST_SHOWDOWN = os.environ.get('POKERFAST_TABLE_EVAL', '1') == '1'
if _USE_FAST_SHOWDOWN:
    try:
        from .evaluator import eval_hole_board as _eval_hole_board
    except Exception as _e:
        print(f"[fast_hand] fast showdown unavailable ({_e}); using pokerkit")
        _USE_FAST_SHOWDOWN = False

_RANKS = '23456789TJQKA'
_SUITS = 'cdhs'
FULL_DECK = tuple(r + s for r in _RANKS for s in _SUITS)


class _State:
    """The `hand.state` surface, mirroring the part of pokerkit's State that
    callers actually read.

    pokerkit's State is a large object with computed properties; only six of
    them are needed to drive a hand, so this holds those six as plain
    attributes and nothing else.
    """

    __slots__ = ('turn_index', 'street_index', 'starting_stacks', 'stacks',
                 'payoffs', 'board', 'bets', 'deck_cards')

    def __init__(self):
        self.turn_index = None
        self.street_index = 0
        self.starting_stacks = []
        self.stacks = []
        self.payoffs = []
        self.board = []
        self.bets = []
        self.deck_cards = []


class FastHand:
    """A dealt 6-max no-limit hold'em hand.

    Construct with no arguments to deal a fresh one, or use `for_cards` to build
    a specific situation. Rebuilding from a serialised `u_hand` is not
    supported.
    """

    __slots__ = ('auto_deal', 'done', 'processor', 'u_hand', 'active_players',
                 'player_count', 'big_blind', 'small_blind', 'preflop_stacks',
                 'state', 'final_pot', '_as_cache',
                 '_committed', '_folded', '_allin', '_actor', '_acted',
                 '_cbra', '_consec', '_deck', '_deck_i', '_hole', '_pot',
                 '_observer')

    # ------------------------------------------------------------------ init
    def __init__(self, u_hand=None, auto_deal=True,
                 player_count=6, small_blind=100, big_blind=200,
                 min_stack=None, max_stack=None, rng=None, observer=None):
        if u_hand is not None:
            raise NotImplementedError(
                "FastHand only deals fresh hands; rebuilding from a "
                "serialised u_hand is not supported.")
        # `rng` exists for reproducible paired evaluation and nothing else.
        # Omitted (the default) it IS the `random` module, so every deal on
        # every hot path is byte-identical to before this parameter existed --
        # `random.randint` and `random.shuffle` are bound methods of the
        # module's own hidden Random instance, so `_rng.randint is
        # random.randint`. Pass a `random.Random(seed)` and the same seed deals
        # the same stacks, the same hole cards and the same board, which is
        # what lets two separately trained policies be scored on identical
        # hands. See `measure_br_gain(seed=...)`.
        _rng = random if rng is None else rng
        self.auto_deal = auto_deal
        self.done = False
        self.processor = None
        self.player_count = player_count
        self.active_players = player_count
        self.big_blind = big_blind
        self.small_blind = small_blind
        self.final_pot = 0
        self._as_cache = None

        n = player_count
        lo = min_stack if min_stack is not None else big_blind * 20
        hi = max_stack if max_stack is not None else big_blind * 500
        # The original draws max_stack ONCE per hand and then draws each seat's
        # stack from [min, that]. Reproduced exactly: drawing per seat from
        # [min, 500bb] instead would change the stack-depth distribution the
        # policy sees, which is a training-distribution change, not a refactor.
        hi = _rng.randint(big_blind * 100, hi) if max_stack is None else hi

        stacks = [_rng.randint(lo, hi) for _ in range(n)]

        self.u_hand = [[], [], [], [], [], [], [], []]
        for i in range(n):
            self.u_hand[2].append("p%dc%d" % (i, stacks[i]))
        self.u_hand[2].append("p0c%d" % small_blind)
        self.u_hand[2].append("p1c%d" % big_blind)

        st = _State()
        st.starting_stacks = list(stacks)
        st.stacks = list(stacks)
        st.payoffs = [0] * n
        st.board = []
        st.bets = [0] * n
        st.street_index = 0
        self.state = st
        self.preflop_stacks = tuple(stacks)

        deck = list(FULL_DECK)
        _rng.shuffle(deck)
        self._deck = deck
        self._deck_i = 0

        self._hole = []
        for i in range(n):
            a = deck[self._deck_i]
            b = deck[self._deck_i + 1]
            self._deck_i += 2
            self._hole.append(a + b)
            self.u_hand[0].append(a + b)

        self._committed = [0] * n
        self._folded = [False] * n
        self._allin = [False] * n
        self._pot = 0
        self._observer = observer

        # Blinds. Posted, not acted: a posted blind is not an action and never
        # appears in u_hand.
        self._post(0, small_blind)
        self._post(1, big_blind)

        # pokerkit's opener for a POSITION street is the seat after the largest
        # posted blind, which for (sb, bb) at seats 0 and 1 is seat 2 preflop
        # and seat 0 on every later street.
        self._begin_betting(2 % n)

    @classmethod
    def for_cards(cls, stacks, hole, board, small_blind=100, big_blind=200,
                  observer=None):
        """Build a hand with EXACTLY these cards. Test hook, not a hot path.

        The differential test cannot compare showdowns otherwise: the two
        engines own separate decks and never see the same cards, so side pots
        and split pots -- the part most likely to be subtly wrong -- would go
        untested. This lets a pokerkit hand's cards be replayed here and the
        awarded payoffs compared directly.

        The deck is laid out in the order this engine consumes it: two cards
        per seat in seat order, then the board.
        """
        deck = []
        for h in hole:
            deck.append(h[:2])
            deck.append(h[2:])
        deck.extend(board)
        seen = set(deck)
        deck.extend(c for c in FULL_DECK if c not in seen)

        self = cls.__new__(cls)
        self.auto_deal = True
        self.done = False
        self.processor = None
        self.player_count = len(stacks)
        self.active_players = len(stacks)
        self.big_blind = big_blind
        self.small_blind = small_blind
        self.final_pot = 0
        self._as_cache = None
        n = self.player_count

        self.u_hand = [[], [], [], [], [], [], [], []]
        for i in range(n):
            self.u_hand[2].append("p%dc%d" % (i, stacks[i]))
        self.u_hand[2].append("p0c%d" % small_blind)
        self.u_hand[2].append("p1c%d" % big_blind)

        st = _State()
        st.starting_stacks = list(stacks)
        st.stacks = list(stacks)
        st.payoffs = [0] * n
        st.board = []
        st.bets = [0] * n
        st.street_index = 0
        self.state = st
        self.preflop_stacks = tuple(stacks)

        self._deck = deck
        self._deck_i = 0
        self._hole = []
        for i in range(n):
            a = deck[self._deck_i]
            b = deck[self._deck_i + 1]
            self._deck_i += 2
            self._hole.append(a + b)
            self.u_hand[0].append(a + b)

        self._committed = [0] * n
        self._folded = [False] * n
        self._allin = [False] * n
        self._pot = 0
        self._observer = observer
        self._post(0, small_blind)
        self._post(1, big_blind)
        self._begin_betting(2 % n)
        return self

    def _post(self, i, amount):
        st = self.state
        amt = min(amount, st.stacks[i])
        st.bets[i] += amt
        st.stacks[i] -= amt
        self._committed[i] += amt
        self._pot += amt
        if st.stacks[i] == 0:
            self._allin[i] = True

    def _begin_betting(self, opener):
        """Open a betting round: rebuild the actor queue and reset the
        per-street raise bookkeeping."""
        n = self.player_count
        self._actor = [(opener + k) % n for k in range(n)]
        self._actor = [i for i in self._actor if self._live(i)]
        self._acted = set()
        self._cbra = 0
        self._consec = []
        self.state.turn_index = self._actor[0] if self._actor else None

    def _pop_actor(self):
        i = self._actor.pop(0)
        self._acted.add(i)
        return i

    def _max_bet_now(self):
        return max(self.state.bets)

    def _effective_stack(self, i):
        """What player i "can possibly lose": their own stack, capped by the
        second-largest total any player still in the hand can put up."""
        st = self.state
        tot = sorted(st.bets[j] + st.stacks[j]
                     for j in range(self.player_count) if not self._folded[j])
        if len(tot) < 2:
            return st.stacks[i]
        return min(st.stacks[i], max(0, tot[-2] - st.bets[i]))

    def _can_raise(self, i):
        st = self.state
        if self._consec and sum(self._consec) < self._cbra and i in self._acted:
            # Accumulated short all-ins still fall short of a full raise and
            # this player has already acted since the last full raise.
            return False
        mb = self._max_bet_now()
        if st.stacks[i] <= mb - st.bets[i]:
            # Already covered by a previous bet: calling is all-in, so there is
            # no aggressive option.
            return False
        # AND there must be somebody left who could actually call a raise.
        # Without this, a player with chips behind facing opponents who are all
        # all-in or folded is offered a "raise" nobody can match -- pokerkit
        # refuses it, and the symptom when this check is missing is a min_bet
        # exactly equal to the call amount, which is not a raise at all.
        for j in range(self.player_count):
            if j != i and not self._folded[j] and st.stacks[j] + st.bets[j] > mb:
                return True
        return False

    # -------------------------------------------------------------- helpers
    def _live(self, i):
        return not self._folded[i] and not self._allin[i]

    def _in_hand(self):
        return [i for i in range(self.player_count) if not self._folded[i]]

    def investment(self, player=None):
        if player is None:
            player = self.state.turn_index
        return self._committed[player]

    def pot_size(self):
        # A running counter, not a re-summation. The obvious implementation
        # is sum(starting_stacks) - sum(stacks), two 6-element sums on every
        # call -- and this is called ~19 times per hand-step. Keeping the
        # value incrementally is exact and measurably cheaper.
        #
        # It reads ZERO once the hand is settled and the chips have been pushed
        # back, which is why `final_pot` exists as the high-water mark the rake
        # is charged on.
        return self._pot

    def track_pot(self, committing=0):
        p = self.pot_size() + int(committing or 0)
        if p > self.final_pot:
            self.final_pot = p

    def shuffle(self):
        """Reshuffle the UNDEALT remainder, matching Hand.shuffle().

        The hole cards already dealt must stay put -- CRN deck-sharing depends
        on two copies of a shuffled hand producing the SAME board, and that
        only holds if the shuffle is of the undealt tail.
        """
        self._as_cache = None
        tail = self._deck[self._deck_i:]
        random.shuffle(tail)
        self._deck[self._deck_i:] = tail

    def _draw(self, k):
        out = self._deck[self._deck_i:self._deck_i + k]
        self._deck_i += k
        return out

    # --------------------------------------------------------- action space
    def get_action_space(self):
        if self.done:
            return False
        c = self._as_cache
        if c is not None:
            return c
        st = self.state
        ti = st.turn_index
        if ti is None:
            return False
        options = {}
        bet_ti = st.bets[ti]
        mb = self._max_bet_now()
        coc = mb - bet_ti
        if coc > st.stacks[ti]:
            coc = st.stacks[ti]

        if coc == 0:
            options['check'] = 0
        else:
            options['call'] = coc
            options['fold'] = 0

        if self._can_raise(ti):
            # min raise-to = max(largest increment so far, one big blind) on
            # top of the current max bet -- then capped by the effective stack,
            # because nobody can be made to raise more than an opponent can
            # cover.
            amount = max(self._cbra, self.big_blind) + mb
            min_to = min(self._effective_stack(ti) + bet_ti, amount)
            max_to = st.stacks[ti] + bet_ti
            options['min_bet'] = min_to - bet_ti
            options['max_bet'] = max_to - bet_ti
        options['player'] = ti
        self._as_cache = options
        return options

    # -------------------------------------------------------------- actions
    def fold(self):
        st = self.state
        ti = st.turn_index
        if ti is None or self.done:
            return False
        if self._max_bet_now() - st.bets[ti] <= 0:
            # pokerkit's can_fold() is false when there is nothing to call.
            return False
        self.track_pot(0)
        self.u_hand[st.street_index + 3].append("p%df" % ti)
        if self._observer is not None:
            self._observer.action(ti, -1)
        self._pop_actor()
        self._folded[ti] = True
        self.active_players -= 1
        self.post_action()
        return True

    def check(self):
        st = self.state
        ti = st.turn_index
        if ti is None or self.done:
            return False
        if self._max_bet_now() - st.bets[ti] != 0:
            return False
        self.track_pot(0)
        self.u_hand[st.street_index + 3].append("p%dc0" % ti)
        if self._observer is not None:
            self._observer.action(ti, 0)
        self._pop_actor()
        self.post_action()
        return True

    def call(self):
        st = self.state
        ti = st.turn_index
        if ti is None or self.done:
            return False
        coc = self._max_bet_now() - st.bets[ti]
        if coc > st.stacks[ti]:
            coc = st.stacks[ti]
        if coc:
            self.track_pot(coc)
            self.u_hand[st.street_index + 3].append("p%dc%d" % (ti, coc))
            if self._observer is not None:
                self._observer.action(ti, coc)
            self._commit(ti, coc)
        self._pop_actor()
        self.post_action()
        return True

    def bet_or_raise(self, chips):
        """`chips` is the ADDITIONAL amount, not the new total."""
        st = self.state
        ti = st.turn_index
        if ti is None or self.done:
            return False
        sp = self._as_cache or self.get_action_space()
        if not sp or 'min_bet' not in sp:
            return self.call()
        minimum = sp['min_bet']
        maximum = sp['max_bet']
        if chips > maximum:
            chips = maximum
        elif chips < minimum:
            chips = minimum

        self.track_pot(chips)
        self.u_hand[st.street_index + 3].append("p%dc%d" % (ti, chips))
        if self._observer is not None:
            self._observer.action(ti, chips)

        prev_max = self._max_bet_now()
        self._pop_actor()
        self._commit(ti, chips)
        new_to = st.bets[ti]
        increment = new_to - prev_max

        # A raise puts EVERY other live player back in the queue, short all-in
        # or not. What a short all-in withholds is the right to raise again,
        # enforced in `_can_raise`, not the obligation to act.
        n = self.player_count
        self._actor = [(ti + 1 + k) % n for k in range(n)]
        self._actor = [j for j in self._actor if self._live(j) and j != ti]

        if increment >= self._cbra:
            # A full raise resets who counts as having acted.
            self._acted = {ti}
        if increment > self._cbra:
            self._cbra = increment
        if st.stacks[ti]:
            self._consec = []
        else:
            self._consec.append(increment)
        self.post_action()
        return True

    def _commit(self, i, amount):
        st = self.state
        amt = min(amount, st.stacks[i])
        st.bets[i] += amt
        st.stacks[i] -= amt
        self._committed[i] += amt
        self._pot += amt
        if st.stacks[i] == 0:
            self._allin[i] = True

    # ----------------------------------------------------------- transition
    def post_action(self):
        self._as_cache = None
        st = self.state

        if len(self._in_hand()) <= 1:
            self._settle()
            return

        if self._actor:
            st.turn_index = self._actor[0]
            return

        # Street complete: bets are collected into the pot and the next board
        # card(s) come out. If fewer than two players can still act, the rest of
        # the board simply runs out to showdown.
        while True:
            st.bets = [0] * self.player_count
            if st.street_index >= 3:
                self._settle()
                return
            st.street_index += 1
            if self.auto_deal:
                _new = self._draw(3 if st.street_index == 1 else 1)
                for c in _new:
                    st.board.append(c)
                    self.u_hand[1].append(c)
                if self._observer is not None:
                    self._observer.board(st.street_index, _new)
            if sum(1 for i in range(self.player_count) if self._live(i)) <= 1:
                continue
            self._begin_betting(0)
            if self._actor:
                return

    # -------------------------------------------------------------- settle
    def _settle(self):
        from .rake import apply_rake_to_payoffs
        st = self.state
        n = self.player_count
        self.track_pot(0)

        alive = self._in_hand()
        won = [0] * n
        if len(alive) == 1:
            won[alive[0]] = sum(self._committed)
        else:
            won = self._showdown(alive)

        for i in range(n):
            st.payoffs[i] = won[i] - self._committed[i]
            st.stacks[i] = st.starting_stacks[i] + st.payoffs[i]
        self._pot = 0          # chips are pushed back; the pot is gone

        # THE RAKE GOES IN u_hand ONLY, NOT IN state.payoffs.
        #
        # `u_hand` carries raked payoffs, because that is what a hand history
        # shows. `state.payoffs` stays UNRAKED so that callers which apply
        # their own rake policy are not charged twice. Raking in both places
        # would charge every pot twice,
        # which is invisible in the game tree and shows up only as a quietly
        # wrong reward. state.payoffs therefore sums to exactly zero.
        raked = apply_rake_to_payoffs(
            list(st.payoffs),
            pot=self.final_pot,
            big_blind=self.big_blind,
            n_players_dealt=n,
            saw_flop=len(st.board) >= 3,
        )
        for i, payoff in enumerate(raked):
            if payoff > 0:
                self.u_hand[-1].append("p%dc%d" % (i, int(payoff)))
        st.turn_index = None
        self.done = True

    def _showdown(self, alive):
        """Side pots in layers, settled by committed amount.

        Hand strength comes from `fast_showdown`, which is ORDER-EXACT against
        pokerkit rather than merely close: `_showdown` compares ranks only with
        `max` and `==`, so what has to be reproduced is the ordering, including
        ties -- a tie is a split pot. That equivalence is asserted over 150,000
        pairwise comparisons in tests/test_evaluator.py.

        It was 7.8% of a whole workload at 321 us per evaluation, because
        pokerkit enumerates all 21 five-card subsets through a generic lookup.
        """
        n = self.player_count
        board = ''.join(self.state.board)
        rank = {}
        if _USE_FAST_SHOWDOWN:
            for i in alive:
                try:
                    rank[i] = _eval_hole_board(self._hole[i], board)
                except Exception:
                    rank[i] = None
        else:
            from pokerkit import StandardHighHand
            for i in alive:
                try:
                    rank[i] = StandardHighHand.from_game(self._hole[i], board)
                except Exception:
                    rank[i] = None

        won = [0] * n
        committed = list(self._committed)

        # POT LAYERS, THEN MERGE ADJACENT LAYERS WITH THE SAME ELIGIBLE SET.
        #
        # The merge is not cosmetic. pokerkit does it (state.pots, the
        # `while pots and pots[-1].player_indices == tuple(player_indices)`
        # loop) and without it a single pot with one odd chip becomes several
        # pots each with their own odd chip. That is precisely how the
        # remaining difference showed up: 6 hands in 1500, split pots off by
        # one chip in ~86,000. Layer boundaries still come from EVERY player's
        # commitment, folded or not -- but a boundary that does not change who
        # is eligible does not start a new pot.
        layers = []
        prev = 0
        for lvl in sorted(set(committed)):
            if lvl <= 0:
                continue
            pot = 0
            for i in range(n):
                take = min(committed[i], lvl) - min(committed[i], prev)
                if take > 0:
                    pot += take
            elig = tuple(i for i in alive if committed[i] >= lvl)
            ranked = [i for i in elig if rank[i] is not None]
            if ranked:
                best = max(rank[i] for i in ranked)
                key = tuple(i for i in ranked if rank[i] == best)
            else:
                key = elig
            # Merge on the WINNER set, not the eligible set. Two adjacent
            # layers won by the same players are one pot as far as the split is
            # concerned, and dividing them separately manufactures an extra odd
            # chip per layer.
            if layers and layers[-1][1] == key:
                layers[-1][0] += pot
            else:
                layers.append([pot, key])
            prev = lvl

        for pot, key in layers:
            if not pot:
                continue
            ranked = [i for i in key if rank[i] is not None]
            if ranked:
                winners = list(ranked)
            else:
                # Nobody eligible is still in: the money could never be called,
                # so it goes back to whoever put it up.
                winners = [i for i in range(n) if committed[i] >= prev] or None
                if winners is None:
                    continue
            share, odd = divmod(pot, len(winners))
            for w in winners:
                won[w] += share
            # Odd chips to the lowest seat index among the winners, matching
            # pokerkit's push_chips (it pays the remainder to player_indices[0]).
            if odd:
                won[winners[0]] += odd
        return won

    # ----------------------------------------------------------- u_hand api
    def get_u_hand(self, player=None):
        u = self.u_hand
        if player is None:
            player = self.state.turn_index
        out = list(u)
        hc = list(u[0])
        for i in range(self.player_count):
            if i != player:
                hc[i] = ""
        out[0] = hc
        return [list(x) for x in out]

    # ------------------------------------------------------------ snapshot
    def __deepcopy__(self, memo):
        """Flat clone. This is the 361 us that copy.deepcopy was charging.

        Every field is a list of ints/strings or a scalar, so a generic
        recursive deepcopy with a memo table is doing graph bookkeeping for a
        structure that has no graph.
        """
        c = FastHand.__new__(FastHand)
        c.auto_deal = self.auto_deal
        c.done = self.done
        c.processor = None
        c.player_count = self.player_count
        c.active_players = self.active_players
        c.big_blind = self.big_blind
        c.small_blind = self.small_blind
        c.preflop_stacks = self.preflop_stacks
        c.final_pot = self.final_pot
        c._as_cache = None
        c.u_hand = [list(x) for x in self.u_hand]
        c._committed = list(self._committed)
        c._folded = list(self._folded)
        c._allin = list(self._allin)
        c._actor = list(self._actor)
        c._acted = set(self._acted)
        c._pot = self._pot
        # An observer is mutable and grows; a shared reference would let a
        # branch write history into the position it branched from.
        c._observer = (self._observer.clone()
                       if self._observer is not None else None)
        c._cbra = self._cbra
        c._consec = list(self._consec)
        c._deck = list(self._deck)
        c._deck_i = self._deck_i
        c._hole = list(self._hole)
        s = self.state
        t = _State()
        t.turn_index = s.turn_index
        t.street_index = s.street_index
        t.starting_stacks = list(s.starting_stacks)
        t.stacks = list(s.stacks)
        t.payoffs = list(s.payoffs)
        t.board = list(s.board)
        t.bets = list(s.bets)
        t.deck_cards = []
        c.state = t
        return c
