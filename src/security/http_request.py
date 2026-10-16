from __future__ import annotations

"""Canonical request metadata for security decisions.

Security routing must use the ASGI scope path that the router actually dispatched,
not a URL reconstructed from Host/forwarding headers.
"""

from starlette.requests import Request


def canonical_request_path(request: Request) -> str:
    path=request.scope.get("path")
    return str(path) if isinstance(path,str) else ""
