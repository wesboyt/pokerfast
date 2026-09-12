"""Cash-game rake, as charged by a typical online room.

A percentage of the pot, capped by a table that depends on the stake and on how
many players were dealt in, with the usual no-flop-no-drop rule. The default
table is one real room's; replace `_RAKE_TABLE` for another.

Rake is charged against the pot BEFORE it is awarded, so it is taken from the
winners' collections in proportion to how much of the pot each one took.

    >>> from pokerfast import rake
    >>> rake.apply_rake_to_payoffs([-200, 400, -200], pot=600,
    ...                            big_blind=200, n_players_dealt=6)
"""

# (big blind in cents) -> (2-player cap, 3-4 player cap, 5+ player cap), in cents
_RAKE_TABLE = (
    (2,    25,   50,   75),    # SC 0.01/0.02
    (10,   75,  100,  150),    # SC 0.05/0.1
    (20,   75,  125,  200),    # SC 0.1/0.2
    (50,  100,  250,  300),    # SC 0.25/0.5
    (100, 100,  250,  350),    # SC 0.5/1
    (200, 100,  250,  350),    # SC 1/2      <- the generated training hands
    (500, 125,  300,  400),    # SC 2.5/5
    (1000, 150, 300,  450),    # SC 5/10
    (2000, 150, 300,  550),    # SC 10/20
)

RAKE_PCT = 0.05

# ---------------------------------------------------------------------------
# STAKE REFERENCE.
#
# The generated training hands are dealt at sb/bb = 100/200 -- SC $1/$2 -- but
# the game this agent actually plays is SC $0.05/$0.10. Rake caps are absolute
# amounts of money, so the cap expressed in BIG BLINDS differs enormously
# between the two:
#
#     SC 1/2      6 dealt   cap 350c  =  1.75 bb   binds once pot > 35bb
#     SC 0.05/0.1 6 dealt   cap 150c  = 15.00 bb   effectively never binds
#
# So at $1/$2 every large pot is heavily discounted, while at $0.05/$0.10 you
# pay the full 5% on almost everything. On a 120bb pot that is 1.75bb against
# 6bb -- 3.4x. And because no_flop_no_drop leaves preflop steals unraked, the
# whole discount lands on postflop pots, which is exactly the incentive to see
# more flops and build bigger pots.
#
# set_stake_reference(10) keeps the chip granularity of the 100/200 deal (a
# 5c/10c deal would round bets to whole cents and destroy the small size
# tokens) while charging the ECONOMICS of 5c/10c: the reference stake's cap is
# converted to big blinds and re-expressed in the chip units actually in play.
#
#   POKERFAST_RAKE_STAKE_BB=10   charge SC 0.05/0.10 economics
#   (unset)                 cap from the row matching the dealt blind
# ---------------------------------------------------------------------------
_STAKE_REF_BB = None


def set_stake_reference(bb_cents):
    """Charge the rake of the stake whose big blind is `bb_cents`, scaled into
    whatever chip units are actually being dealt. None restores the default."""
    global _STAKE_REF_BB
    _STAKE_REF_BB = int(bb_cents) if bb_cents else None


def stake_reference():
    return _STAKE_REF_BB


def rake_cap(big_blind, n_players_dealt):
    """Cap in the same chip units as `big_blind`.

    Stakes between two table rows snap DOWN to the lower row (the row whose blind
    level the game has actually reached); stakes above the top row use the top row.
    """
    bb = max(int(big_blind), 1)
    ref = _STAKE_REF_BB
    lookup = ref if ref else bb
    row = _RAKE_TABLE[0]
    for cand in _RAKE_TABLE:
        if cand[0] <= lookup:
            row = cand
        else:
            break
    if n_players_dealt <= 2:
        cap = row[1]
    elif n_players_dealt <= 4:
        cap = row[2]
    else:
        cap = row[3]
    if ref:
        # cap/ref is the cap in big blinds at the reference stake; charge that
        # same number of big blinds in the chip units actually in play.
        cap = int(round(cap * (bb / float(ref))))
    return cap


def rake_amount(pot, big_blind, n_players_dealt, saw_flop=True,
                no_flop_no_drop=True):
    """Rake taken from `pot`, in chips. Never exceeds the pot."""
    if pot <= 0:
        return 0
    if no_flop_no_drop and not saw_flop:
        return 0
    cap = rake_cap(big_blind, n_players_dealt)
    return int(min(pot * RAKE_PCT, cap, pot))


def apply_rake_to_payoffs(payoffs, pot, big_blind, n_players_dealt,
                          saw_flop=True, no_flop_no_drop=True):
    """Return a NEW payoff list with rake removed from the winners' collections.

    `payoffs[i]` is player i's net (negative for losers). Rake comes out of the
    pot before it is awarded, so it is charged against the positive payoffs in
    proportion to how much of the pot each winner collected.

    Returns a new list -- some callers mutated `state.payoffs` in place, which is
    a live mutable list on the pokerkit State object, not a computed property.
    """
    out = list(payoffs)
    total_rake = rake_amount(pot, big_blind, n_players_dealt,
                             saw_flop=saw_flop, no_flop_no_drop=no_flop_no_drop)
    if total_rake <= 0:
        return out
    winners = [i for i, p in enumerate(out) if p > 0]
    if not winners:
        return out
    won = float(sum(out[i] for i in winners))
    if won <= 0:
        return out
    for i in winners:
        share = out[i] / won
        out[i] -= total_rake * share
    return out


import os as _os
_stake_env = _os.environ.get('POKERFAST_RAKE_STAKE_BB', '').strip()
if _stake_env:
    set_stake_reference(int(_stake_env))


def saw_flop_from_board(board):
    """True once at least three board cards exist."""
    try:
        return len(board) >= 3
    except Exception:
        return False


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print(f"{'bb(cents)':>10s} {'stake':>10s} {'2P':>6s} {'3-4P':>6s} {'5+P':>6s}"
          f" {'5+ cap in bb':>14s}")
    for bb, c2, c34, c5 in _RAKE_TABLE:
        print(f"{bb:10d} {bb/100:9.2f}  {c2:6d} {c34:6d} {c5:6d} {c5/bb:13.2f}bb")
    print()
    print("worked examples at the training stake (bb = 200 cents = SC 1/2, 6 dealt):")
    for pot_bb in (3, 12, 40, 120, 400):
        pot = pot_bb * 200
        r = rake_amount(pot, 200, 6, saw_flop=True)
        print(f"  pot {pot_bb:4d}bb -> rake {r/200:6.3f}bb "
              f"({100.0*r/pot:5.2f}% of pot)")
    print("  preflop steal, no flop      -> rake "
          f"{rake_amount(3*200, 200, 6, saw_flop=False)/200:.3f}bb  (no flop, no drop)")
