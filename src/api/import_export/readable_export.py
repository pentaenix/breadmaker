from __future__ import annotations

"""Local readable ZIP download using the existing immutable project snapshot."""

import tempfile
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from backend.environment import runtime
from backend.request_context import _bound_project_id, _require_project_permission
from runtime.native import require_native_projects
from formats.readable_archive import write_readable_archive

router=APIRouter()

@router.get('/api/export/zip')
def export_readable_zip():
    pid=_bound_project_id();_require_project_permission('project.read',pid)
    temporary=tempfile.TemporaryDirectory(prefix='breadmaker-zip-download-')
    try:
        package=Path(temporary.name)/'snapshot.breadmaker';out=Path(temporary.name)/'readable.zip'
        require_native_projects(runtime).export_package(pid,package)
        stem=write_readable_archive(package,out)
        return FileResponse(out,filename=stem+'-readable.zip',media_type='application/zip',
            background=BackgroundTask(temporary.cleanup))
    except Exception:temporary.cleanup();raise
