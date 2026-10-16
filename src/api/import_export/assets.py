from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.dependencies import feature_call
from backend.environment import _HOSTED_LIMITS, runtime
from backend.request_context import _enforce_hosted_project_storage
from security.image_security import UnsafeImage, inspect_hosted_image_bytes
from .uploads import read_upload_limited

_store_content_addressed_image_blob = feature_call('durability.cuts', '_store_content_addressed_image_blob')
_normalize_project_image_ext = feature_call('durability.cuts', '_normalize_project_image_ext')

router=APIRouter()

@router.post("/api/assets")
async def upload_asset(file:UploadFile=File(...)):
    limit=_HOSTED_LIMITS.image_bytes if _HOSTED_LIMITS is not None else 50*1024*1024
    if file.size is not None and file.size>limit: raise HTTPException(413,f'Image is too large ({limit//(1024*1024)} MB limit)')
    ext=_normalize_project_image_ext(Path(file.filename or '').suffix,str(file.content_type or ''))
    payload=await read_upload_limited(file,limit)
    if _HOSTED_LIMITS is not None:
        try:
            info=inspect_hosted_image_bytes(payload,ext,byte_limit=limit)
        except UnsafeImage as exc:
            code=str(exc)
            if code in {'IMAGE_BYTES_TOO_LARGE','IMAGE_DIMENSIONS_TOO_LARGE','IMAGE_PIXELS_TOO_LARGE'}:
                raise HTTPException(413,'Image exceeds Breadmaker hosted safety limits') from exc
            raise HTTPException(415,'Hosted Breadmaker accepts only structurally valid PNG, JPEG, or WebP images') from exc
        ext=info.extension
    _enforce_hosted_project_storage(additional_bytes=len(payload))
    try:stored=runtime.assets.put_blob(payload,ext)
    except RuntimeError as exc:
        if str(exc)=='PROJECT_ASSET_HASH_COLLISION':raise HTTPException(409,'Project image asset conflicts with existing data') from exc
        if str(exc)=='PROJECT_ASSET_CONTEXT_CHANGED':raise HTTPException(409,'The active project changed while the image was uploading. Upload it again in the current project.') from exc
        raise
    return {"path":stored.api_path,"reused":not stored.created,"hash":stored.digest}
