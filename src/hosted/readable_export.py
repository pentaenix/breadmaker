from __future__ import annotations

"""Authorized hosted readable downloads, without a local Workspace dependency."""

import tempfile
from pathlib import Path
from urllib.parse import quote
from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter, Request
from fastapi.responses import Response
from backend.environment import runtime
from hosted.http_api import _pid, _uid, _error
from hosted.packages import HostedProjectPackageService
from hosted.firestore.contracts import DocumentConflict
from hosted.readable_source import write_hosted_readable_archive

router=APIRouter()
_capacity=CapacityLimiter(1)


def _readable_zip(projects,pid,uid):
    # Normal hosted export enforces membership and the encrypted-mode embargo.
    # Fence the entire snapshot, then render without further provider reads.
    # Native conversion intentionally cannot represent some hosted history.
    before=projects.domain.project_generation(pid,uid)
    payload=HostedProjectPackageService(projects).export_bytes(pid,uid)
    if projects.domain.project_generation(pid,uid)!=before:
        raise DocumentConflict('HOSTED_READABLE_CONCURRENT_MUTATION')
    with tempfile.TemporaryDirectory(prefix='breadmaker-hosted-readable-') as td:
        package=Path(td)/'snapshot.breadmaker';out=Path(td)/'readable.zip';package.write_bytes(payload)
        stem=write_hosted_readable_archive(package,out)
        return stem,out.read_bytes()


@router.get('/api/export/zip')
async def hosted_export_readable_zip(request:Request):
    try:
        # One archive per instance keeps compression and snapshot memory bounded;
        # queued work does not occupy a thread or block the ASGI event loop.
        stem,data=await to_thread.run_sync(_readable_zip,runtime.projects,_pid(request),_uid(),limiter=_capacity)
    except Exception as exc:raise _error(exc)
    return Response(data,media_type='application/zip',headers={
        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(stem+'-readable.zip'),
        'Cache-Control':'private, no-store'})
