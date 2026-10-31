from __future__ import annotations

"""Annotations HTTP/domain package.

The package boundary exposes only the router; implementation and payload
contracts live in explicit owners.
"""

__all__ = ["router"]


def __getattr__(name):
    # Hosted Highlights shares the PURE matcher/vocabulary, not native SQL
    # routes or backend.environment. Native composition explicitly asks for
    # router; importing a matching submodule must not boot either runtime.
    if name != 'router':
        raise AttributeError(name)
    from .routes import router
    return router
