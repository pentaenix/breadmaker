from __future__ import annotations

"""Immutable local Revision Store integration."""

import json
import re
import sqlite3
from pathlib import Path

from revision_store import LocalRevisionStore, RevisionStoreError
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import schema_contracts as storage_schema_contracts

def local_revision_store_for(project_root: Path, *, checkpoint_interval: int = 10) -> LocalRevisionStore:
    root=Path(project_root).resolve()
    database=root/'project.sqlite3'
    if not database.exists():
        raise RevisionStoreError('HISTORY_PROJECT_NOT_CONFIGURED')
    try:
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        workspace_project_id=str(manifest.get('project_id') or '').strip()
    except Exception as exc:
        raise RevisionStoreError('HISTORY_WORKSPACE_MANIFEST_INVALID') from exc
    if not re.fullmatch(r'[0-9a-f]{32}', workspace_project_id):
        raise RevisionStoreError('HISTORY_WORKSPACE_PROJECT_ID_INVALID')
    return LocalRevisionStore(
        root / 'revision_history' / 'v1',
        database,
        root/'assets',
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        checkpoint_interval=checkpoint_interval,
        workspace_project_id=workspace_project_id,
    )


def local_revision_store(*, checkpoint_interval: int = 10) -> LocalRevisionStore:
    root=storage_context.current_project_root()
    if root is None:
        raise RevisionStoreError('HISTORY_PROJECT_NOT_CONFIGURED')
    return local_revision_store_for(root,checkpoint_interval=checkpoint_interval)


def history_current_summary_for(project_root: Path) -> dict:
    """Read presentation-only History summary from one explicit project root."""
    database=Path(project_root).resolve()/'project.sqlite3'
    con=sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)
    try:
        text=' '.join(str(row[0] or '') for row in con.execute(
            "SELECT text_content FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id"
        ))
        words=len(re.findall(r"[\w’'-]+",text,flags=re.UNICODE))
        chapters=int(con.execute('SELECT COUNT(*) FROM chapters').fetchone()[0])
        return {'word_count':words,'chapter_count':chapters}
    finally:
        con.close()


def history_current_summary() -> dict:
    """Small writer-facing metadata for one already-committed Current state.

    This is presentation metadata only; generation remains the ordering authority.
    Keeping it beside the Revision Store wrapper lets automatic, safety, and restore
    landmarks all reset the same word-count baseline without coupling storage to UI.
    """
    with storage_mutations.tx(operation='history_summary_read', track_generation=False) as con:
        text=' '.join(str(row[0] or '') for row in con.execute(
            "SELECT text_content FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id"
        ))
        words=len(re.findall(r"[\w’'-]+",text,flags=re.UNICODE))
        chapters=int(con.execute('SELECT COUNT(*) FROM chapters').fetchone()[0])
    return {'word_count':words,'chapter_count':chapters}


def capture_local_revision(*, label: str = 'Automatic History', creation_reason: str = 'automatic', summary: dict | None = None, force_checkpoint: bool = False, expected_generation: int | None = None) -> dict:
    """Publish one local 7.7.3 History revision from a frozen canonical generation.

    This does not mutate Current and deliberately has no retention/deletion/GC path.
    """
    return local_revision_store().capture(
        label=label, creation_reason=creation_reason, summary=summary, force_checkpoint=force_checkpoint, expected_generation=expected_generation
    )


def list_local_revisions() -> list[dict]:
    return local_revision_store().list_revisions()


def verify_local_revision(revision_id: str) -> dict:
    return local_revision_store().verify_revision(revision_id)
