from __future__ import annotations

"""Breadmaker FastAPI composition root."""

from backend.core import app as _platform_app
from backend.environment import runtime

if not getattr(_platform_app.state,'feature_routers_installed',False):
    if runtime.mode=='local':
        from api import install_routers as _install_routers
        _install_routers(_platform_app)
    else:
        from hosted.http_api import product_router
        from hosted.http_compat import router as hosted_compat_router
        from hosted.http_auth import router as hosted_auth_router
        from hosted.http_api import _uid, _pid
        from hosted.narration.http import build_recording_router
        _platform_app.include_router(hosted_auth_router)
        _platform_app.include_router(build_recording_router(
            provider=lambda: runtime.narration.recordings if runtime.narration else None,
            model_provider=lambda: runtime.narration.models if runtime.narration else None,
            pronunciation_provider=lambda: runtime.narration.pronunciation if runtime.narration else None,
            identity=_uid,project=_pid))
        _platform_app.include_router(product_router)
        _platform_app.include_router(hosted_compat_router)
    _platform_app.state.feature_routers_installed=True

app=_platform_app
