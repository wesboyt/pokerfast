#!/usr/bin/env bash
# Build the exact-equity helper.
#
#   bash native/build.sh            # configure + build into native/build/
#   OMPEVAL_SKIP_PATCH=1 bash ...   # do not touch the submodule working tree
#
# Leaves the binary at native/build/ompeval_batch[.exe]; point
# POKERFAST_OMPEVAL at it, or let pokerfast.equity find it there.
#
# The patch step is idempotent: `git apply --check` decides, so re-running is
# safe and a submodule that already has it is left alone.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OMP="$ROOT/vendor/OMPEval"

if [ ! -f "$OMP/omp/HandEvaluator.cpp" ]; then
  echo "==> fetching the OMPEval submodule"
  git -C "$ROOT" submodule update --init --recursive
fi

if [ "${OMPEVAL_SKIP_PATCH:-0}" != "1" ]; then
  for p in "$HERE"/patches/*.patch; do
    [ -e "$p" ] || continue
    if git -C "$OMP" apply --check "$p" >/dev/null 2>&1; then
      echo "==> applying $(basename "$p")"
      git -C "$OMP" apply "$p"
    else
      echo "==> $(basename "$p") already applied or not needed"
    fi
  done
fi

cmake -S "$HERE" -B "$HERE/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$HERE/build" --config Release -j

BIN="$HERE/build/ompeval_batch"
[ -f "$BIN" ] || BIN="$HERE/build/ompeval_batch.exe"
[ -f "$BIN" ] || BIN="$HERE/build/Release/ompeval_batch.exe"
echo
echo "built: $BIN"
echo 'AhKd|2c3d4h5s6c' | "$BIN" | head -1 | sed 's/^/self-test equity: /'
