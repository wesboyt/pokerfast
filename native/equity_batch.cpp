// equity_batch.cpp -- batched, exact, single-threaded hold'em equity.
//
// OMPEval ships `parse.cpp`, which answers ONE query per process. That costs
// ~26 ms, of which ~19 ms is fixed startup: process creation for a statically
// linked binary, plus HandEvaluator::staticInit() building its 86,547-entry
// perfect-hash lookup table. Both are paid again for every query, so a caller
// issuing thousands of them spends nearly all its time on startup.
//
// Three changes, all in how the calculator is invoked:
//
//   1. BATCH. Read queries from stdin, one per line; write one result per
//      line. The process and the lookup table are built once for the whole
//      session instead of once per query.
//
//   2. enumerateAll = true. The default is false, i.e. Monte Carlo. With a
//      complete five-card board the opponent has only C(45,2) = 990 possible
//      hands, so exact enumeration is both cheaper AND exact; the sampled
//      answer drifts ~1e-4 from the true equity for no reason.
//
//   3. threadCount = 1. The default of 0 means "as many threads as the
//      hardware supports", spun up and joined for a 990-combination problem.
//      The pool costs far more than the work. Run several of these processes
//      instead if you want parallelism.
//
// Input:  "AhKd"  or  "AhKd|2c3d4h5s6c"   (hole, optionally hole|board)
// Output: one equity in [0, 1] per line, or "0" for a query that fails to
//         parse. Every line is flushed, so a caller can treat it as a
//         request/response pipe without deadlocking.

#include "omp/EquityCalculator.h"
#include <iostream>
#include <string>

int main()
{
    std::ios::sync_with_stdio(false);

    // Construct one calculator up front purely to trigger the one-time static
    // table build before the first query is served.
    { omp::EquityCalculator warm; }

    std::string line;
    while (std::getline(std::cin, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n'))
            line.pop_back();
        if (line.empty())
            continue;

        omp::EquityCalculator eq;
        uint64_t board = 0;
        if (line.size() > 4)
            board = omp::CardRange::getCardMask(line.substr(5));

        // handRanges, boardCards, deadCards, enumerateAll, stdevTarget,
        // callback, updateInterval, threadCount
        if (!eq.start({line, "random"}, board, 0, true, 0, nullptr, 0.2, 1)) {
            std::cout << "0\n" << std::flush;
            continue;
        }
        eq.wait();
        std::cout << eq.getResults().equity[0] << "\n" << std::flush;
    }
    return 0;
}
