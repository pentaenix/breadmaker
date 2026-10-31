"""Read-only fixed public chunk origin with bounded non-retrying RPCs."""
import re

from hosted.narration.io_limits import io_timeout
from hosted.narration.sealed_parts import PART_BYTES
from hosted.storage.contracts import ObjectLimitExceeded, ObjectNotFound
from hosted.storage.google_driver import GoogleCloudObjectStore


class GoogleNarrationModels(GoogleCloudObjectStore):
    def __init__(self, *, bucket, project, client=None):
        if not isinstance(project, str) or not re.fullmatch('[a-z][a-z0-9-]{4,28}[a-z0-9]', project) or bucket != project + '-breadmaker-models':
            raise ValueError('NARRATION_MODEL_BUCKET_SCOPE_INVALID')
        super().__init__(bucket=bucket,project=project,client=client)

    @staticmethod
    def _name(name):
        if not isinstance(name,str) or not re.fullmatch(r'narration-public/v1/[a-f0-9]{64}/[0-9]{4}\.bin',name):
            raise ValueError('NARRATION_MODEL_OBJECT_SCOPE_INVALID')
        return name

    def stat(self, name):
        blob=self._bucket.blob(self._name(name))
        try:blob.reload(timeout=io_timeout(),retry=None)
        except Exception as exc:
            translated=self._translate(exc)
            if isinstance(translated,ObjectNotFound):return None
            raise translated from None
        return self._metadata(blob)

    def read_chunks(self,name,*,max_bytes,if_generation_match=None,chunk_size=256*1024):
        if (type(max_bytes) is not int or not 1 <= max_bytes <= PART_BYTES or
                type(if_generation_match) is not int or if_generation_match < 1 or
                type(chunk_size) is not int or not 1 <= chunk_size <= PART_BYTES):
            raise ValueError('NARRATION_MODEL_READ_INVALID')
        blob=self._bucket.blob(self._name(name))
        try:payload=blob.download_as_bytes(start=0,end=max_bytes,if_generation_match=if_generation_match,
                retry=None,timeout=io_timeout(),checksum=None,raw_download=True)
        except Exception as exc:raise self._translate(exc) from None
        if len(payload)>max_bytes:raise ObjectLimitExceeded('NARRATION_MODEL_SIZE_LIMIT')
        return tuple(payload[i:i+chunk_size] for i in range(0,len(payload),chunk_size))

    def write_chunks(self,*args,**kwargs):raise ValueError('NARRATION_MODEL_ORIGIN_READ_ONLY')
    def delete(self,*args,**kwargs):raise ValueError('NARRATION_MODEL_ORIGIN_READ_ONLY')
    def list_prefix(self,*args,**kwargs):raise ValueError('NARRATION_MODEL_ORIGIN_READ_ONLY')
