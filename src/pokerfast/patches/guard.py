"""Version and API guard for the pokerkit runtime patches.

The patches reach into pokerkit internals:

    Card.clean                 (lookup_cache: tuple fast path)
    Lookup._get_key            (lookup_cache: 5-card memoisation)
    <5 classes>.from_game      (lookup_cache: best-hand memoisation)
    21 frozen dataclasses      (deepcopy: identity __deepcopy__)

If the installed pokerkit differs from the one these were written against,
three things can happen:

  1. the attribute is GONE      -> the patch raises and is skipped. Safe.
  2. the attribute is RENAMED   -> same as (1). Safe.
  3. the attribute EXISTS but its semantics changed -> the patch applies to
     something it was never verified against, and the results are silently
     wrong. THIS is the one worth guarding.

So: pin the version, fingerprint the signatures of everything patched, and
re-run the equivalence checks. Any mismatch disables the patches and says so,
rather than proceeding on an unverified assumption.

    POKERFAST_STRICT=0   downgrade a version/fingerprint mismatch to a warning
                         and patch anyway (only if you have verified it)
"""
import hashlib
import inspect
import os

# The environment these patches were written against and verified on.
VERIFIED_VERSION = '0.7.3'
VERIFIED_FINGERPRINT = 'd8fa441e0cd43814'


def _installed_version():
    try:
        import importlib.metadata as md
        return md.version('pokerkit')
    except Exception:
        return None


_FP_CACHE = []


def api_fingerprint():
    """Hash the signatures of every pokerkit callable the patches replace.

    Computed ONCE and cached. The patches replace these very callables, so a
    call made after install would fingerprint the patched signatures and report
    a spurious mismatch -- which is exactly what happened the first time this
    ran. The first call happens in should_patch(), before anything is patched,
    so the cached value is always the pristine one.
    """
    if _FP_CACHE:
        return _FP_CACHE[0]
    try:
        from pokerkit.utilities import Card
        from pokerkit.lookups import Lookup
        import pokerkit.hands as H
        sigs = [('Card.clean', inspect.signature(Card.clean.__func__)),
                ('Lookup._get_key', inspect.signature(Lookup._get_key))]
        for n, c in vars(H).items():
            if isinstance(c, type) and 'from_game' in vars(c):
                raw = vars(c)['from_game']
                fn = raw.__func__ if isinstance(raw, classmethod) else raw
                sigs.append((n + '.from_game', inspect.signature(fn)))
        sigs.sort()
        blob = ''.join(f'{n}{s}' for n, s in sigs)
        fp = hashlib.sha256(blob.encode()).hexdigest()[:16]
        _FP_CACHE.append(fp)
        return fp
    except Exception:
        return None


def check(verbose=True):
    """Return (ok, reasons). ok=False means DO NOT patch."""
    reasons = []
    ver = _installed_version()
    fp = api_fingerprint()
    if ver != VERIFIED_VERSION:
        reasons.append(f"pokerkit {ver} installed, patches verified against "
                       f"{VERIFIED_VERSION}")
    if fp != VERIFIED_FINGERPRINT:
        reasons.append(f"patched-API fingerprint {fp} != verified "
                       f"{VERIFIED_FINGERPRINT}")
    ok = not reasons
    if verbose and not ok:
        strict = os.environ.get('POKERFAST_STRICT', '1') == '1'
        print("=" * 72)
        print("[fast_guard] pokerkit does not match the verified environment:")
        for r in reasons:
            print(f"  - {r}")
        if strict:
            print("  -> runtime patches DISABLED. Results stay correct, but")
            print("     ~2x slower. Re-run the test suite against your")
            print("     pokerkit and update VERIFIED_* in guard.py, or set")
            print("     POKERFAST_STRICT=0 to patch anyway.")
        else:
            print("  -> POKERFAST_STRICT=0: patching anyway, UNVERIFIED.")
        print("=" * 72)
    return ok, reasons


def should_patch():
    ok, _ = check()
    if ok:
        return True
    return os.environ.get('POKERFAST_STRICT', '1') != '1'


def self_test(verbose=True):
    """Re-run the patches' own equivalence checks after installing them.

    Cheap (a few hundred card sets) and runs once at import, so a server that
    differs in some way the fingerprint did not catch still fails loudly at
    startup instead of quietly producing wrong results.
    """
    problems = []
    try:
        from . import lookup_cache as fast_eval
        # Only check patches that are actually installed. A layer switched off
        # deliberately (POKERFAST_PATCH_EVAL=0) reports 'not installed', which is a
        # configuration choice, not a correctness failure -- treating it as one
        # disabled the OTHER layers too.
        if fast_eval.is_installed():
            n, bad = fast_eval.verify()
            if bad:
                problems.append(f"Card.clean: {bad[:1]}")
        if fast_eval.is_key_cache_installed():
            n, bad = fast_eval.verify_key_cache(120)
            if bad:
                problems.append(f"_get_key cache: {bad[:1]}")
        if fast_eval.is_from_game_installed():
            n, bad = fast_eval.verify_from_game(120)
            if bad:
                problems.append(f"from_game cache: {bad[:1]}")
    except Exception as e:
        problems.append(f"self-test could not run: {e}")
    if problems and verbose:
        print("=" * 72)
        print("[fast_guard] STARTUP SELF-TEST FAILED -- disabling patches:")
        for p in problems:
            print(f"  - {p}")
        print("=" * 72)
    return not problems, problems
