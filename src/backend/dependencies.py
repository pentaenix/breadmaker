from __future__ import annotations

import importlib
from typing import Any, Callable


class BackendDependencyError(RuntimeError):
    """Raised when an explicit cross-feature dependency cannot be resolved."""


def feature_call(owner: str, symbol: str) -> Callable[..., Any]:
    """Return a cycle-safe callable reference to a canonical feature function.

    Cross-feature dependencies are intentionally limited to function calls. Mutable
    state belongs in backend/runtime/storage services, never in another route module.
    """
    def invoke(*args: Any, **kwargs: Any) -> Any:
        module=importlib.import_module(f"api.{owner}")
        target=getattr(module,symbol,None)
        if not callable(target):
            raise BackendDependencyError(f"BREADMAKER_FEATURE_DEPENDENCY_INVALID:{owner}:{symbol}")
        return target(*args,**kwargs)
    invoke.__name__=symbol
    invoke.__qualname__=symbol
    invoke.__doc__=f"Lazy cross-feature call to api.{owner}.{symbol}."
    return invoke
