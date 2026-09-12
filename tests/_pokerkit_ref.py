"""A minimal pokerkit driver, used only as the REFERENCE in the differential
tests.

It exposes the same six-method surface `FastHand` does -- `get_action_space`,
`fold`, `check`, `call`, `bet_or_raise`, `pot_size` -- plus `state`, `u_hand`,
`done` and `final_pot`, driving pokerkit's `NoLimitTexasHoldem` underneath.

Deliberately dumb. Every accessor recomputes from pokerkit rather than caching,
because the entire point is to be obviously-correct rather than fast: if this
file and `FastHand` disagree, the test should be telling us about `FastHand`.

Stacks are passed in explicitly rather than drawn, so the two engines start
from an identical position without depending on them consuming `random` in the
same order.
"""

from pokerkit import Automation, NoLimitTexasHoldem

_AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
    Automation.RUNOUT_COUNT_SELECTION,
)


def _card_text(card):
    return f"{card.rank}{card.suit}"


class RefHand:
    """pokerkit, wearing FastHand's interface."""

    def __init__(self, stacks, small_blind=100, big_blind=200):
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.player_count = len(stacks)
        self.active_players = self.player_count
        self.done = False
        self.final_pot = 0
        # u_hand[0] holes, [1] board, [2] stacks+blinds, [3..6] streets
        self.u_hand = [[], [], [], [], [], [], [], []]

        for i, s in enumerate(stacks):
            self.u_hand[2].append("p%dc%d" % (i, s))
        self.u_hand[2].append("p0c%d" % small_blind)
        self.u_hand[2].append("p1c%d" % big_blind)

        self.state = NoLimitTexasHoldem.create_state(
            _AUTOMATIONS, True, 0, (small_blind, big_blind), big_blind,
            tuple(stacks), self.player_count,
        )
        self.preflop_stacks = self.state.starting_stacks
        for _ in range(self.player_count):
            self.u_hand[0].append(
                "".join(map(_card_text, self.state.deal_hole(2).cards)))

    # ----------------------------------------------------------------- reads
    def pot_size(self):
        return sum(self.state.starting_stacks) - sum(self.state.stacks)

    def track_pot(self, committing=0):
        p = self.pot_size() + int(committing or 0)
        if p > self.final_pot:
            self.final_pot = p

    def get_action_space(self):
        if self.done:
            return False
        st = self.state
        ti = st.turn_index
        if ti is None:
            return None
        options = {}
        coc = st.checking_or_calling_amount
        if st.can_check_or_call():
            options['check' if coc == 0 else 'call'] = coc
        if coc != 0:
            options['fold'] = 0
        bet_ti = st.bets[ti]
        mn = st.min_completion_betting_or_raising_to_amount
        if mn is not None:
            options['min_bet'] = mn - bet_ti
        mx = st.max_completion_betting_or_raising_to_amount
        if mx is not None:
            options['max_bet'] = mx - bet_ti
        options['player'] = ti
        return options

    # --------------------------------------------------------------- actions
    def fold(self):
        if not self.state.can_fold():
            return False
        i = self.state.turn_index
        self.track_pot(0)
        self.u_hand[self.state.street_index + 3].append("p%df" % i)
        self.state.fold()
        self.active_players -= 1
        self._post_action()
        return True

    def check(self):
        st = self.state
        if not (st.can_check_or_call() and st.checking_or_calling_amount == 0):
            return False
        i = st.turn_index
        self.track_pot(0)
        self.u_hand[st.street_index + 3].append("p%dc0" % i)
        st.check_or_call()
        self._post_action()
        return True

    def call(self):
        st = self.state
        chips = st.checking_or_calling_amount
        if st.can_check_or_call() and chips:
            i = st.turn_index
            self.track_pot(chips)
            self.u_hand[st.street_index + 3].append("p%dc%d" % (i, chips))
            st.check_or_call()
        self._post_action()
        return True

    def bet_or_raise(self, chips):
        """`chips` is the ADDITIONAL amount, not the new total. Clamped into
        [min, max] exactly as FastHand clamps it."""
        st = self.state
        i = st.turn_index
        bet_ti = st.bets[i]
        minimum = st.min_completion_betting_or_raising_to_amount - bet_ti
        mx = st.max_completion_betting_or_raising_to_amount
        maximum = (st.checking_or_calling_amount - bet_ti if mx is None
                   else mx - bet_ti)
        chips = max(minimum, min(chips, maximum))
        self.track_pot(chips)
        self.u_hand[st.street_index + 3].append("p%dc%d" % (i, chips))
        st.complete_bet_or_raise_to(chips + bet_ti)
        self._post_action()
        return True

    # -------------------------------------------------------------- internals
    def _post_action(self):
        st = self.state
        if st.can_select_runout_count():
            st.select_runout_count(1)
        if self.active_players > 1:
            while st.can_burn_card():
                st.burn_card('??')
                while st.can_deal_board():
                    for c in st.deal_board().cards:
                        self.u_hand[1].append(_card_text(c))
        if st.street is None:
            self.track_pot(0)
            self.done = True
