from __future__ import annotations

"""Breadmaker FastAPI platform composition.

Local mode retains the verified project/platform routers. Hosted mode installs a
separate Firestore/GCS surface so importing Cloud Run never initializes the local
Workspace/SQLite product stack.
"""

from fastapi import FastAPI

from backend import http_boundary, lifecycle
from backend.environment import runtime

app=FastAPI(title="Breadmaker Editor",docs_url=None,redoc_url=None,lifespan=lifecycle.app_lifespan)
http_boundary.install(app)

if runtime.mode=='local':
    # Keep these historical source relationships explicit for the local architecture.
    from api import project_management
    from backend import request_pipeline, runtime_routes
    app.include_router(project_management.router)
    request_pipeline.install(app)
    app.include_router(runtime_routes.router)
else:
    from hosted.http_api import install_request_boundary, platform_router
    install_request_boundary(app)
    app.include_router(platform_router)
