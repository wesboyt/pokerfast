"""pokerfast -- fast hold'em hand evaluation, a lean NLHE engine, and
runtime accelerators for pokerkit.

Three independent pieces; take whichever you need.

1. `eval7` -- table-driven 7-card evaluation, ~50x quicker than pushing all 21
   five-card combinations through a generic lookup. Order-exact against
   pokerkit, ties included.

       from pokerfast import eval7
       eval7(('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d'))

2. `FastHand` -- a 6-max no-limit hold'em engine that plays one game well
   instead of every game generally. Dealing a hand costs ~25 us against
   pokerkit's ~932 us; it is differentially tested against pokerkit step by
   step, which is the actual argument for trusting it.

       from pokerfast import FastHand
       h = FastHand()
       h.get_action_space()

3. `pokerfast.patches` -- monkey-patches that make *pokerkit itself* faster,
   for when you want to keep pokerkit's semantics exactly. Guarded by a version
   and API fingerprint, so an unrecognised pokerkit disables them loudly rather
   than silently applying a patch it was never verified against.

       from pokerfast import patches
       patches.install_all()

Optional extra: `pokerfast.equity` drives OMPEval (a git submodule, built by
`native/build.sh`) for exact multi-way equity.
"""

from .evaluator import eval7, eval_hole_board, RANKS, SUITS
from .engine import FastHand, FULL_DECK

__all__ = [
    'eval7', 'eval_hole_board', 'RANKS', 'SUITS',
    'FastHand', 'FULL_DECK',
    'patches', 'rake', 'equity',
    '__version__',
]

__version__ = '0.1.0'
