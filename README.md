# pokerfast

Fast Texas hold'em primitives: a table-driven 7-card evaluator, a lean 6-max
no-limit engine, and runtime accelerators for [pokerkit](https://github.com/uoftcprg/pokerkit).

Three independent pieces. Take whichever you need — none of them requires the
others.

| you want | use | against pokerkit |
|---|---|---|
| compare 7-card hands | `eval7` | ~686 µs → ~14 µs |
| play out millions of hands | `FastHand` | deal 932 µs → ~25 µs |
| keep pokerkit, lose the cost | `pokerfast.patches` | ~2x on the evaluation path |
| exact multi-way equity | `pokerfast.equity` | C++ (OMPEval), batched |

## Install

```bash
pip install pokerfast                      # core: eval7 + FastHand
pip install "pokerfast[pokerkit]"          # + the pokerkit patches
```

## 1. `eval7` — 7-card evaluation

```python
from pokerfast import eval7

eval7(('As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '3d'))   # royal flush
```

A rank-histogram evaluator with two precomputed 8192-entry tables. One pass
over seven cards, a flush test, a straight lookup, a branch on the count
pattern — no enumeration of the 21 five-card combinations.

**Only the ORDER is defined.** The integer is not a hand rank anyone else's
code will recognise: compare two of them, don't interpret one. The test suite
checks the induced order against pokerkit over thousands of random hands, with
ties counted separately so an evaluator that called everything equal could not
pass, plus an explicit sweep of every category (random deals almost never
produce a steel wheel).

## 2. `FastHand` — a 6-max NLHE engine

```python
from pokerfast import FastHand

h = FastHand()                      # deal 6 players, 100/200 blinds
while not h.done:
    space = h.get_action_space()    # {'call': 200, 'fold': 0, 'min_bet': 400, ...}
    h.call()
h.state.payoffs
```

pokerkit is a general, validating framework for dozens of variants, and that
generality costs per action. This implements exactly one game — 6-max NLHE,
cash mode, fixed blinds, one runout — where the state is a few integer lists,
an action is a few additions, and a snapshot is a list copy.

**Why you can trust it:** `tests/test_engine.py` is a differential test. It
drives this engine and pokerkit through the same random action sequence and
compares the action space, the exact chip amounts, turn order, pot, board
length and the per-street action log at *every step*, over hundreds of hands,
with raises over-sampled at the extremes because the min-raise increment and
all-in-for-less rules are where engines go wrong.

### Observers

Watch the action stream without re-deriving it:

```python
class Log:
    def __init__(self):        self.events = []
    def action(self, seat, amount): self.events.append((seat, amount))
    def board(self, street, cards): self.events.append((street, cards))
    def clone(self):           ...        # called on deepcopy

h = FastHand(observer=Log())
```

`clone()` matters: a branch must not write history back into the position it
branched from. Amounts are `-1` for a fold, `0` for a check, chips otherwise.

## 3. `pokerfast.patches` — make pokerkit itself faster

When you need pokerkit's exact semantics but not its cost:

```python
from pokerfast import patches
patches.install_all()
```

Nothing in `site-packages` is modified — these are in-process monkey-patches:

- an identity `__deepcopy__` on pokerkit's frozen dataclasses (copying an
  immutable object is pure waste), applied only to classes verified to declare
  no mutable field;
- memoisation of `Lookup._get_key` and `<HandType>.from_game`;
- a tuple fast path for `Card.clean`.

**Guarded.** The dangerous failure isn't "the attribute is gone" — that raises,
and is safe. It's "the attribute is still there and means something else",
which is silent. So the version is pinned, the signatures of everything patched
are fingerprinted, and a mismatch disables the patches loudly rather than
applying one that was never verified. `POKERFAST_STRICT=0` overrides, once
you've checked it yourself.

## 4. `pokerfast.equity` — exact equity (optional)

Needs a C++ toolchain and CMake. OMPEval is a **git submodule**, not vendored
source:

```bash
git submodule update --init --recursive
bash native/build.sh
```

```python
from pokerfast.equity import EquityCalculator

with EquityCalculator() as eq:
    eq.equity('AhKd', '2c3d4h5s6c')
    eq.equity_many([('AhKd', '2c3d4h5s6c'), ('7c7d', '')])
```

The helper keeps **one** process alive and serves queries over stdin/stdout. A
single-query process spends ~19 ms of its ~26 ms building an 86,547-entry
lookup table it then throws away, so batching is most of the win. It also
enumerates exhaustively rather than sampling (with a full board the opponent
has only C(45,2) = 990 hands, so exact is both cheaper *and* exact) and runs
single-threaded, because a thread pool costs more than a 990-combination
problem.

See `NOTICE` for OMPEval's ISC licence and what it requires if you ship a
binary.

## Environment variables

| variable | default | effect |
|---|---|---|
| `POKERFAST_TABLE_EVAL` | `1` | `0` routes showdowns through pokerkit instead |
| `POKERFAST_STRICT` | `1` | `0` patches pokerkit despite a fingerprint mismatch |
| `POKERFAST_EVAL_CACHE` | `65536` | `Lookup._get_key` cache size |
| `POKERFAST_FROMGAME_CACHE` | `32768` | `from_game` cache size |
| `POKERFAST_OMPEVAL` | — | path to a prebuilt `ompeval_batch` |

## Tests

```bash
pip install "pokerfast[test]"
pytest
```

The differential tests need pokerkit and are the reason to trust any of this;
they skip without it, which makes the suite much weaker. Don't read a green run
that skipped them as a pass.

## Benchmarks, honestly

The figures above were measured on CPython 3.13, free-threaded, on one machine,
against pokerkit 0.7.3. They are the right order of magnitude and not a
promise. The engine numbers come from a workload that plays hands to completion
in bulk; if you deal one hand and inspect it, pokerkit's constant factor is
irrelevant to you and you should use pokerkit.

## Licence

MIT — see `LICENSE`. OMPEval is ISC and is not redistributed here; see `NOTICE`.
