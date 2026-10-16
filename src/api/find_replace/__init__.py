from __future__ import annotations

"""Find Replace HTTP/domain package.

The package boundary exposes only the router; implementation and payload
contracts live in explicit owners.
"""

from .routes import router

__all__ = ["router"]
