from __future__ import annotations

"""Local manuscript DOCX download. Rendering has no runtime dependencies."""

import os
import tempfile
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from backend.environment import _HOSTED_LIMITS, runtime
from backend.data_access import allrows, project
from storage import mutations as storage_mutations
from formats.manuscript_docx import docx_bytes
from formats.readable_names import file_stem

router=APIRouter()

@router.get("/api/export/docx")
def export_docx():
    with storage_mutations.tx() as con:
        p=project(con)
        tabs=allrows(con,"SELECT * FROM document_tabs WHERE project_id=? ORDER BY position,id",(p['id'],))
        chapters=allrows(con,"SELECT * FROM chapters WHERE project_id=? ORDER BY tab_id,position,id",(p['id'],))
        blocks=allrows(con,"SELECT * FROM manuscript_blocks WHERE project_id=? ORDER BY tab_id,chapter_id,position,id",(p['id'],))
    # Preserve the native route's defense if installed under a guarded runtime.
    # Normal hosted requests use their independent authorized document API.
    read_asset=(lambda path:None) if _HOSTED_LIMITS is not None else runtime.assets.read_blob
    payload=docx_bytes(p['title'],tabs,chapters,blocks,read_asset,image_placeholder='[Image]')
    fd,name=tempfile.mkstemp(prefix='breadmaker-docx-',suffix='.docx');os.close(fd);out=Path(name)
    try:out.write_bytes(payload)
    except Exception:out.unlink(missing_ok=True);raise
    return FileResponse(out,filename=file_stem(p['title'])+'.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        background=BackgroundTask(out.unlink,missing_ok=True))
