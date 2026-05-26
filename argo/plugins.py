"""Tiny plugin loader: resolve "module.path:ClassName" specs to instances.

Every pluggable component in Argo (EntitlementAdapter, AuditWriter,
LlmClient, Verifier) is selected at startup via an env var that names
a Python class. This module turns that string into a class instance.

The contract is intentionally minimal: the spec must be importable and
the named class must construct with zero arguments. If a plugin needs
configuration, it should read its own env vars in __init__.

Example:
    ENTITLEMENT_ADAPTER=mybank.argo_okta:OktaAdapter
    → import mybank.argo_okta, instantiate OktaAdapter()

The deliberate simplicity here is so plugins distribute as ordinary pip
packages — no Argo-specific build steps or entry-point registration is
required. See ADAPTERS.md for a worked example.
"""

from __future__ import annotations

import importlib
from typing import TypeVar

T = TypeVar("T")


def load_plugin(spec: str) -> object:
    """Resolve "module.path:ClassName" and return an instance.

    Raises:
        ValueError    — spec is not in "module:Class" form.
        ImportError   — the module could not be imported.
        AttributeError — the module imported but lacks the named class.
    """
    if ":" not in spec:
        raise ValueError(
            f"plugin spec {spec!r} must be in 'module.path:ClassName' form. "
            f"Example: 'argo.entitlements:DbDerivedAdapter'"
        )

    module_path, _, class_name = spec.partition(":")
    if not module_path or not class_name:
        raise ValueError(
            f"plugin spec {spec!r}: both module path and class name are required"
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"plugin spec {spec!r}: could not import module {module_path!r}. "
            f"If this is a third-party plugin, make sure it is installed in "
            f"the same environment as Argo. Underlying error: {e}"
        ) from e

    try:
        cls = getattr(module, class_name)
    except AttributeError as e:
        raise AttributeError(
            f"plugin spec {spec!r}: module {module_path!r} has no attribute "
            f"{class_name!r}. Check the spelling, or verify the class is "
            f"exported (not module-private)."
        ) from e

    return cls()


__all__ = ["load_plugin"]
