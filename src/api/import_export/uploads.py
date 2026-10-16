from __future__ import annotations

import os
import uuid
import zipfile
from pathlib import Path
from fastapi import HTTPException, UploadFile

from backend.environment import _HOSTED_LIMITS


async def read_upload_limited(file:UploadFile,limit:int) -> bytes:
    """Read one bounded upload without coupling the caller to a filesystem path."""
    total=0;chunks=[]
    while True:
        chunk=await file.read(1024*1024)
        if not chunk:break
        total+=len(chunk)
        if total>limit:raise HTTPException(413,'Uploaded file exceeds the safe size limit')
        chunks.append(chunk)
    return b''.join(chunks)

async def save_upload_limited(file:UploadFile,path:Path,limit:int):
    """Stage uploads beside the destination and publish with one atomic rename."""
    total=0;path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.upload-tmp')
    try:
        with tmp.open('xb') as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>limit:
                    raise HTTPException(413,'Uploaded file exceeds the safe size limit')
                out.write(chunk)
            out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
        try:
            fd=os.open(str(path.parent),os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        except (OSError,AttributeError):pass
        return total
    finally:
        tmp.unlink(missing_ok=True)

def validate_docx_archive(path:Path):
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist(); expanded=sum(i.file_size for i in infos)
            # python-docx materializes XML trees in memory. A highly-compressed DOCX
            # can therefore be far more dangerous than its upload size suggests.
            expanded_limit=_HOSTED_LIMITS.document_expanded_bytes if _HOSTED_LIMITS is not None else 1024*1024*1024
            if len(infos)>20000 or expanded>expanded_limit:
                raise HTTPException(413,'DOCX expands beyond Breadmaker’s safe import limit')
            main=next((i for i in infos if i.filename=='word/document.xml'),None)
            if main and main.file_size>256*1024*1024:
                raise HTTPException(413,'The DOCX main document is too large to parse safely')
            if any(i.file_size>512*1024*1024 for i in infos):
                raise HTTPException(413,'The DOCX contains an individual part that is too large to import safely')
    except zipfile.BadZipFile:
        raise HTTPException(400,'This file is not a valid DOCX archive')
