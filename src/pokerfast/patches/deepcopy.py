"""Identity `__deepcopy__` for pokerkit's frozen dataclasses.

`copy.deepcopy` of a pokerkit game state walks a large object graph and rebuilds
every node. Most of those nodes are FROZEN dataclasses holding only immutable
values -- cards, enums, ints -- and copying them is pure waste: an immutable
object can be shared.

This installs `__deepcopy__ = lambda self, memo: self` on exactly those classes,
after checking each one is a frozen dataclass that declares no mutable field.
Anything that might hold a list, dict or set is rejected and left alone.

`verify()` walks a real state and asserts every shared node really is immutable,
so the optimisation is checked against the object graph rather than assumed.

    >>> from pokerfast.patches import deepcopy as dc
    >>> dc.install(); dc.installed_names()
"""
import dataclasses
import enum
import importlib
import os
import pkgutil

_INSTALLED = []
_REJECTED = []

_IMMUTABLE = (int, float, complex, str, bytes, bool, type(None), frozenset)
_MUTABLE_HINTS = ('list', 'List', 'dict', 'Dict', 'set[', 'Set[',
                  'MutableSequence', 'MutableMapping', 'deque')


def _identity_deepcopy(self, memo):
    return self


def _is_frozen_dataclass(obj):
    return (isinstance(obj, type)
            and dataclasses.is_dataclass(obj)
            and getattr(obj, '__dataclass_params__', None) is not None
            and obj.__dataclass_params__.frozen)


def _declares_mutable_field(cls):
    bad = []
    for f in dataclasses.fields(cls):
        t = str(f.type)
        if any(h in t for h in _MUTABLE_HINTS):
            bad.append(f.name)
    return bad


def _pokerkit_modules():
    import pokerkit
    mods = [pokerkit]
    for m in pkgutil.iter_modules(pokerkit.__path__):
        try:
            mods.append(importlib.import_module(f"pokerkit.{m.name}"))
        except Exception:
            pass
    return mods


def install():
    """Share every provably-immutable pokerkit value type. Idempotent."""
    if _INSTALLED or os.environ.get('POKERFAST_PATCH_DEEPCOPY', '1') != '1':
        return len(_INSTALLED)
    for mod in _pokerkit_modules():
        for obj in vars(mod).values():
            if not _is_frozen_dataclass(obj) or obj in _INSTALLED:
                continue
            bad = _declares_mutable_field(obj)
            if bad:
                _REJECTED.append((obj.__name__, bad))
                continue
            obj.__deepcopy__ = _identity_deepcopy
            _INSTALLED.append(obj)
    return len(_INSTALLED)


def installed_names():
    return sorted(t.__name__ for t in _INSTALLED)


def rejected():
    return list(_REJECTED)


def verify(root, max_nodes=200000):
    """Walk a live object graph and confirm every SHARED instance is really
    immutable -- i.e. that none of its field values is a mutable container.

    The install-time check reads annotations; this reads values. A field
    annotated `tuple` that actually holds a list at runtime would pass the
    first and fail here, which is exactly the case worth catching.

    Returns (n_checked, [violations]).
    """
    shared = set(_INSTALLED)
    seen, stack, bad, checked = set(), [root], [], 0
    while stack and len(seen) < max_nodes:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        t = type(o)
        if t in shared:
            checked += 1
            for f in dataclasses.fields(t):
                v = getattr(o, f.name, None)
                if not _value_immutable(v):
                    bad.append(f"{t.__name__}.{f.name} holds "
                               f"{type(v).__name__}")
            continue
        if isinstance(o, dict):
            stack.extend(o.keys()); stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        elif hasattr(o, '__dict__'):
            stack.extend(vars(o).values())
        elif hasattr(o, '__iter__') and not isinstance(o, (str, bytes)):
            try:
                stack.extend(list(o))
            except Exception:
                pass
    return checked, bad


def _value_immutable(v, depth=0):
    if depth > 8:
        return False
    if isinstance(v, _IMMUTABLE) or isinstance(v, enum.Enum):
        return True
    if isinstance(v, tuple):
        return all(_value_immutable(x, depth + 1) for x in v)
    t = type(v)
    if t in _INSTALLED:
        return True
    if _is_frozen_dataclass(t) and not _declares_mutable_field(t):
        return all(_value_immutable(getattr(v, f.name, None), depth + 1)
                   for f in dataclasses.fields(t))
    return False


def uninstall():
    """Remove the identity __deepcopy__ from every shared type."""
    removed = 0
    for t in _INSTALLED:
        try:
            del t.__deepcopy__
            removed += 1
        except Exception:
            pass
    _INSTALLED.clear()
    _REJECTED.clear()
    return removed
