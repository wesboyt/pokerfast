// pokerfast_omp.cpp -- a C ABI over OMPEval, loaded from Python with ctypes.
//
// Why a plain C ABI and not a CPython extension: a ctypes library does not
// link against libpython, so ONE build per OS/CPU serves every Python version,
// free-threaded builds included. The wheels are tagged py3-none-<platform>.
//
// ctypes releases the GIL for the duration of every call, and nothing here
// holds shared mutable state (OMPEval's lookup tables are built once, under
// C++11's thread-safe static initialisation, and only read afterwards), so
// concurrent calls from several Python threads run in parallel.
//
// No C++ exception may cross the C boundary; every entry point catches and
// returns PF_ERR_INTERNAL instead.
//
// Cards, where passed as integers, use OMPEval's index: 4 * rank + suit, rank
// 0 (deuce) .. 12 (ace), suit 0..3 = s, h, c, d. The Python side translates.

#include "omp/EquityCalculator.h"
#include "omp/HandEvaluator.h"
#include "omp/CardRange.h"

#include <cstdint>
#include <limits>
#include <string>
#include <vector>

#if defined(_WIN32)
  #define PF_API extern "C" __declspec(dllexport)
#else
  #define PF_API extern "C" __attribute__((visibility("default")))
#endif

enum {
    PF_OK = 0,
    PF_ERR_ARGS = -1,       // malformed arguments (bad card, duplicate, count)
    PF_ERR_IMPOSSIBLE = -2, // OMPEval refused: e.g. every combo conflicts
    PF_ERR_INTERNAL = -3,   // a C++ exception, e.g. bad_alloc
};

// Bumped whenever a signature below changes; Python refuses a mismatch rather
// than calling through a stale prototype.
static const int PF_ABI_VERSION = 1;

namespace {

// Constructing a HandEvaluator is what initialises omp::Hand's CARDS table and
// the lookup tables. A function-local static makes that once-only and
// thread-safe.
const omp::HandEvaluator& evaluator()
{
    static const omp::HandEvaluator ev;
    return ev;
}

// Parse a board/dead-card string. Rejects anything but whole, valid, distinct
// cards: OMPEval's own parser stops silently at the first bad character, which
// would turn a typo into a smaller board instead of an error.
bool parse_cards(const char* s, uint64_t& mask)
{
    mask = 0;
    if (!s)
        return true;
    std::string text(s);
    if (text.size() % 2)
        return false;
    uint64_t m = omp::CardRange::getCardMask(text);
    unsigned n = 0;
    for (uint64_t x = m; x; x &= x - 1)
        ++n;
    if (n * 2 != text.size())
        return false;
    mask = m;
    return true;
}

} // namespace

PF_API int pf_abi_version(void)
{
    return PF_ABI_VERSION;
}

// Force the one-time table build (~20 ms) now rather than on the first query.
PF_API int pf_init(void)
{
    try {
        (void)evaluator();
        return PF_OK;
    } catch (...) {
        return PF_ERR_INTERNAL;
    }
}

// General equity, the full OMPEval interface.
//
//   ranges      n range strings in OMPEval syntax ("AhKd", "QQ+,AKs", "random")
//   board, dead card strings, may be NULL or ""
//   enumerate   1 = exact enumeration, 0 = Monte Carlo
//   stdev       Monte Carlo stopping target (ignored when enumerating)
//   threads     worker threads; 0 = hardware concurrency
//   time_limit  seconds, 0 = none (Monte Carlo is otherwise unbounded if
//               stdev is 0)
//   out_equity  n doubles
//   out_hands   hands evaluated (may be NULL)
PF_API int pf_equity(const char* const* ranges, unsigned n, const char* board,
                     const char* dead, int enumerate, double stdev,
                     unsigned threads, double time_limit, double* out_equity,
                     uint64_t* out_hands)
{
    try {
        if (!ranges || !out_equity || n < 1 || n > omp::MAX_PLAYERS)
            return PF_ERR_ARGS;
        uint64_t board_mask, dead_mask;
        if (!parse_cards(board, board_mask) || !parse_cards(dead, dead_mask))
            return PF_ERR_ARGS;
        if (board_mask & dead_mask)
            return PF_ERR_ARGS;

        std::vector<omp::CardRange> rs;
        rs.reserve(n);
        for (unsigned i = 0; i < n; ++i) {
            if (!ranges[i])
                return PF_ERR_ARGS;
            omp::CardRange r(ranges[i]);
            if (r.combinations().empty())
                return PF_ERR_ARGS;
            rs.push_back(r);
        }

        (void)evaluator();
        omp::EquityCalculator eq;
        if (time_limit > 0)
            eq.setTimeLimit(time_limit);
        if (!eq.start(rs, board_mask, dead_mask, enumerate != 0, stdev,
                      nullptr, 0.2, threads))
            return PF_ERR_IMPOSSIBLE;
        eq.wait();
        omp::EquityCalculator::Results res = eq.getResults();
        if (res.hands == 0)
            return PF_ERR_IMPOSSIBLE;
        // Not res.equity: OMPEval divides by (hands + 1e-9), which biases
        // every exact answer low by ~1e-9/hands. Divide the exact counts.
        for (unsigned i = 0; i < n; ++i)
            out_equity[i] = (res.wins[i] + res.ties[i]) / (double)res.hands;
        if (out_hands)
            *out_hands = res.hands;
        return PF_OK;
    } catch (...) {
        return PF_ERR_INTERNAL;
    }
}

// The hot path: hero's exact equity against ONE uniformly random hand, for a
// batch of (hole, board) pairs, single-threaded per query. One call for the
// whole batch keeps the ctypes overhead per query negligible.
//
// Returns PF_OK, or the error of the FIRST failing query; out[i] is NaN for
// every query that failed, so the caller can say which.
PF_API int pf_equity_vs_random(const char* const* holes,
                               const char* const* boards, unsigned n,
                               double* out)
{
    try {
        if (!holes || !out)
            return PF_ERR_ARGS;
        (void)evaluator();
        int status = PF_OK;
        const double nan = std::numeric_limits<double>::quiet_NaN();
        for (unsigned i = 0; i < n; ++i) {
            out[i] = nan;
            uint64_t hole_mask, board_mask;
            if (!holes[i] || !parse_cards(holes[i], hole_mask) ||
                std::string(holes[i]).size() != 4 ||
                !parse_cards(boards ? boards[i] : nullptr, board_mask) ||
                (hole_mask & board_mask)) {
                if (status == PF_OK)
                    status = PF_ERR_ARGS;
                continue;
            }
            omp::EquityCalculator eq;
            if (!eq.start({omp::CardRange(holes[i]), omp::CardRange("random")},
                          board_mask, 0, true, 0, nullptr, 0.2, 1)) {
                if (status == PF_OK)
                    status = PF_ERR_IMPOSSIBLE;
                continue;
            }
            eq.wait();
            omp::EquityCalculator::Results res = eq.getResults();
            // Exact counts; see pf_equity for why not res.equity.
            out[i] = (res.wins[0] + res.ties[0]) / (double)res.hands;
        }
        return status;
    } catch (...) {
        return PF_ERR_INTERNAL;
    }
}

// Rank n hands of k cards each (0 <= k <= 7), `cards` is n*k card indices.
// Bigger is better; equal means a split. Like pokerfast.eval7, only the ORDER
// is meaningful, and it is not the same number eval7 returns.
PF_API int pf_evaluate(const uint8_t* cards, unsigned n, unsigned k,
                       uint16_t* out)
{
    try {
        if (!cards || !out || k > 7)
            return PF_ERR_ARGS;
        const omp::HandEvaluator& ev = evaluator();
        for (unsigned i = 0; i < n; ++i) {
            const uint8_t* c = cards + (size_t)i * k;
            uint64_t seen = 0;
            omp::Hand h = omp::Hand::empty();
            for (unsigned j = 0; j < k; ++j) {
                if (c[j] >= omp::CARD_COUNT || (seen >> c[j] & 1))
                    return PF_ERR_ARGS;
                seen |= 1ull << c[j];
                h += omp::Hand(c[j]);
            }
            out[i] = ev.evaluate(h);
        }
        return PF_OK;
    } catch (...) {
        return PF_ERR_INTERNAL;
    }
}
