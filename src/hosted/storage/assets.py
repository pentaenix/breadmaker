from __future__ import annotations

import hashlib
from typing import Callable

from hosted.firestore.ids import require_project_id
from runtime.contracts import StoredAsset
from security.image_security import HOSTED_DEFAULT_IMAGE_BYTES, UnsafeImage, inspect_hosted_image_bytes

from .contracts import ObjectNotFound, ObjectStore
from .keys import project_asset_key
from .publication import ImmutableObjectPublisher


class HostedAssetStore:
    """Cloud object implementation of the Milestone-1 logical AssetStore port.

    It is intentionally not wired into ``runtime.hosted`` until Milestone 5. The
    active project is supplied by a server-owned provider, never by an object path
    supplied by the browser.
    """

    def __init__(
        self,
        objects: ObjectStore,
        *,
        project_id: str | Callable[[], str],
        image_byte_limit: int = HOSTED_DEFAULT_IMAGE_BYTES,
    ) -> None:
        self.objects = objects
        self._project_id = project_id
        self.image_byte_limit = int(image_byte_limit)
        self.publisher = ImmutableObjectPublisher(objects)

    def _project(self) -> str:
        value = self._project_id() if callable(self._project_id) else self._project_id
        return require_project_id(str(value))

    @staticmethod
    def _asset_name(name_or_api_path: str) -> str:
        value = str(name_or_api_path or "").strip()
        if value.startswith("/api/assets/"):
            value = value.removeprefix("/api/assets/")
        if not value.startswith("img_") or "/" in value or "\\" in value or "." not in value:
            raise ValueError("ASSET_PATH_INVALID")
        stem, extension = value.rsplit(".", 1)
        digest_prefix = stem.removeprefix("img_")
        if len(digest_prefix) != 24 or any(ch not in "0123456789abcdef" for ch in digest_prefix):
            raise ValueError("ASSET_PATH_INVALID")
        if extension not in {"png", "jpg", "webp"}:
            raise ValueError("ASSET_PATH_INVALID")
        return value

    def _find(self, name_or_api_path: str):
        project_id = self._project()
        name = self._asset_name(name_or_api_path)
        stem, ext = name.rsplit(".", 1)
        digest_prefix = stem.removeprefix("img_")
        prefix = f"projects/{project_id}/assets/sha256/{digest_prefix}"
        matches = [row for row in self.objects.list_prefix(prefix) if row.name.endswith("." + ext)]
        if len(matches) != 1:
            if not matches:
                raise FileNotFoundError(name)
            raise RuntimeError("PROJECT_ASSET_PREFIX_COLLISION")
        return project_id, name, matches[0]

    def exists(self, name_or_api_path: str) -> bool:
        try:
            self._find(name_or_api_path)
            return True
        except FileNotFoundError:
            return False

    def read_blob(self, name_or_api_path: str) -> bytes:
        project_id, name, metadata = self._find(name_or_api_path)
        try:
            payload = b"".join(
                self.objects.read_chunks(
                    metadata.name,
                    max_bytes=self.image_byte_limit,
                    if_generation_match=metadata.generation,
                )
            )
        except ObjectNotFound as exc:
            raise FileNotFoundError(name) from exc
        extension = "." + name.rsplit(".", 1)[1]
        try:
            inspect_hosted_image_bytes(payload, extension, byte_limit=self.image_byte_limit)
        except UnsafeImage as exc:
            raise ValueError("ASSET_BYTES_UNSAFE") from exc
        digest = hashlib.sha256(payload).hexdigest()
        expected_name = f"img_{digest[:24]}{extension}"
        expected_object = project_asset_key(project_id, digest, extension)
        if name != expected_name or metadata.name != expected_object:
            raise ValueError("ASSET_CONTENT_IDENTITY_MISMATCH")
        return payload

    def put_blob(self, blob: bytes, ext: str) -> StoredAsset:
        data = bytes(blob)
        try:
            info = inspect_hosted_image_bytes(data, ext, byte_limit=self.image_byte_limit)
        except UnsafeImage as exc:
            raise ValueError("ASSET_BYTES_UNSAFE") from exc
        digest = hashlib.sha256(data).hexdigest()
        name = f"img_{digest[:24]}{info.extension}"
        object_name = project_asset_key(self._project(), digest, info.extension)
        publication = self.publisher.publish_bytes(
            object_name,
            data,
            max_bytes=self.image_byte_limit,
            content_type=info.media_type,
            metadata={"breadmaker-entity": "project_asset"},
        )
        return StoredAsset(
            "/api/assets/" + name,
            name,
            digest,
            publication.created,
            object_name=publication.object.name,
            object_generation=publication.object.generation,
        )

    def discard_uncommitted(self, asset: StoredAsset) -> None:
        # Hosted immutable objects are not destructively guessed away inside a
        # failed request. If Firestore publication fails they remain discoverable
        # orphan candidates and retention-aware GC owns deletion later.
        return None
