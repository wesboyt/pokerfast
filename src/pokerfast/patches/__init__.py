"""Runtime accelerators for pokerkit itself.

Use these when you want pokerkit's exact semantics but not its cost. Nothing in
site-packages is modified -- these are monkey-patches applied in-process, and
they disappear when it exits.

    from pokerfast import patches
    patches.install_all()        # guard first, then patch; returns a report

`install_all()` refuses to patch a pokerkit whose version or API fingerprint it
does not recognise, because the dangerous failure is not "the attribute is
gone" (that raises, and is safe) but "the attribute is still there and means
something else" (that is silent, and produces wrong answers). Set
POKERFAST_STRICT=0 to override once you have verified it yourself.

Individual pieces, if you want finer control:

    from pokerfast.patches import guard, lookup_cache, deepcopy
    guard.check()
    lookup_cache.install(); lookup_cache.verify()
    deepcopy.install()
"""

from . import guard, lookup_cache, deepcopy

__all__ = ['guard', 'lookup_cache', 'deepcopy', 'install_all', 'uninstall_all']


def install_all(strict=None):
    """Guard, then install every patch. Returns a dict describing what happened.

    `strict=None` honours POKERFAST_STRICT (default on). `strict=False` patches
    even on a fingerprint mismatch.
    """
    report = {'guard_ok': False, 'installed': [], 'skipped': [], 'errors': {}}

    ok = guard.should_patch() if strict is None else (
        guard.check(verbose=False) or not strict)
    report['guard_ok'] = bool(ok)
    if not ok:
        report['skipped'] = ['lookup_cache', 'deepcopy']
        return report

    for name, fn in (('card_clean', lookup_cache.install),
                     ('key_cache', lookup_cache.install_key_cache),
                     ('from_game_cache', lookup_cache.install_from_game_cache),
                     ('deepcopy', deepcopy.install)):
        try:
            fn()
            report['installed'].append(name)
        except Exception as e:            # a patch that cannot apply is skipped,
            report['skipped'].append(name)   # never fatal -- pokerkit still works
            report['errors'][name] = repr(e)
    return report


def uninstall_all():
    """Undo everything `install_all` did."""
    try:
        lookup_cache.uninstall_all()
    finally:
        deepcopy.uninstall()
