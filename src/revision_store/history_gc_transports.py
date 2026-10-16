from __future__ import annotations

from backups.providers.webdav import WebDAVClient
from backups.providers.google_drive import GoogleDriveClient
from revision_store.remote_history import RemoteHistoryError


class WebDAVHistoryGCTransport:
    """Deletion capability scoped structurally to one History workspace namespace."""

    def __init__(self, client: WebDAVClient, workspace_id: str):
        self.client = client
        self.workspace_id = str(workspace_id)
        self.base = f'history/{self.workspace_id}'

    def delete_revision_publication(self, generation: int) -> int:
        self.client.delete(f'{self.base}/revisions/g{int(generation):020d}.remote.json', missing_ok=True)
        return 1

    def delete_checkpoint(self, checkpoint_id: str) -> int:
        self.client.delete(f'{self.base}/checkpoints/{str(checkpoint_id)}.checkpoint.json', missing_ok=True)
        return 1

    def delete_snapshot(self, snapshot_object_id: str) -> int:
        self.client.delete(f'{self.base}/objects/{str(snapshot_object_id)}.snapshot.zip', missing_ok=True)
        return 1


class GoogleDriveHistoryGCTransport:
    """Delete only Drive objects carrying this workspace's History appProperties.

    The adapter never accepts a provider path or arbitrary file id from its caller, so
    History GC cannot address Recovery Backup objects even if the planner is buggy.
    """

    def __init__(self, client: GoogleDriveClient, root_folder_id: str, workspace_id: str):
        self.client = client
        self.root_folder_id = str(root_folder_id)
        self.workspace_id = str(workspace_id)

    def _delete_kind(self, kind: str, key: str) -> int:
        rows = self.client.list_history_files(
            self.root_folder_id,
            self.workspace_id,
            kind=kind,
            key=str(key),
        )
        removed = 0
        for row in rows:
            file_id = str(row.get('id') or '')
            if not file_id:
                continue
            props = row.get('appProperties') if isinstance(row.get('appProperties'), dict) else {}
            if str(props.get('breadmaker_history_workspace_id') or '') != self.workspace_id:
                raise RemoteHistoryError('REMOTE_HISTORY_GC_WORKSPACE_IDENTITY_MISMATCH')
            if str(props.get('breadmaker_history_kind') or '') != kind:
                raise RemoteHistoryError('REMOTE_HISTORY_GC_KIND_IDENTITY_MISMATCH')
            if str(props.get('breadmaker_history_key') or '') != str(key):
                raise RemoteHistoryError('REMOTE_HISTORY_GC_KEY_IDENTITY_MISMATCH')
            self.client.delete_file(file_id, missing_ok=True)
            removed += 1
        return removed

    def delete_revision_publication(self, generation: int) -> int:
        return self._delete_kind('revision_publication', f'g{int(generation):020d}')

    def delete_checkpoint(self, checkpoint_id: str) -> int:
        return self._delete_kind('checkpoint', str(checkpoint_id))

    def delete_snapshot(self, snapshot_object_id: str) -> int:
        return self._delete_kind('object', str(snapshot_object_id))
