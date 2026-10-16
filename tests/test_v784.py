from __future__ import annotations

from storage import history as storage_history

import pytest
from revision_store.remote_history import RemoteHistoryCorruption, RemoteHistoryRepository
from test_v777 import MemoryTransport


def test_v784_partial_remote_authority_is_writer_agnostic_corruption(fresh_project):
    store=storage_history.local_revision_store()
    revision=store.capture(label='v784 authority fence',creation_reason='v784',force_checkpoint=True)
    workspace_id=str(store.workspace_project_id);owner='1'*32
    transport=MemoryTransport()
    repo=RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=owner,canonical_tables=store.canonical_tables)
    repo.publish(store,revision['revision_id'])
    original=dict(transport.data)

    for missing in (repo._repo_path(),repo._authority_path()):
        transport.data=dict(original)
        transport.data.pop(missing)
        before=dict(transport.data)
        for writer in (owner,'2'*32):
            with pytest.raises(RemoteHistoryCorruption,match='REMOTE_HISTORY_REPOSITORY_AUTHORITY_INCOMPLETE'):
                RemoteHistoryRepository(
                    transport,
                    workspace_project_id=workspace_id,
                    writer_id=writer,
                    canonical_tables=store.canonical_tables,
                ).claim_writer()
            assert transport.data==before
