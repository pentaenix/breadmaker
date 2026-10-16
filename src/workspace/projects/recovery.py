from __future__ import annotations
import hashlib, json, os, shutil, sqlite3, tempfile, uuid, zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backups.frozen_snapshot import capture_frozen_snapshot, verify_frozen_package_payload, FrozenSnapshotError
from revision_store.local_store import canonical_state_hash
from storage import schema_contracts as storage_schema_contracts
from security import content_security
from workspace.constants import APP_SCHEMA_VERSION, PACKAGE_VERSION, MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, MAX_MANIFEST_BYTES, HASH_CHUNK_BYTES, PORTABLE_PROJECT_MANIFEST_KEYS
from workspace.models import ProjectRecord, WorkspaceError
from workspace.filesystem import _database_summary, _durable_replace, _recovery_metadata, _sha256_zip_entry, now, safe_name

class ProjectRecoveryMixin:
    def recovery_candidates(self) -> list[dict[str,Any]]:
        """Folders that exist but cannot safely join the normal registry."""
        with self._lock:registered={str(p.get('id')) for p in self._registry.get('projects',[]) if p.get('id')}
        out=[]
        if not self.projects_dir.exists(): return out
        for root in sorted(self.projects_dir.iterdir()):
            if not root.is_dir() or root.name in registered: continue
            db=root/'project.sqlite3'
            out.append({'id':root.name,'path':str(root),'database_exists':db.is_file(),'readable':_database_summary(db) is not None})
        return out

    def _write_initial_recovery_state(self, root:Path, project_id:str, *, pristine_new_project:bool) -> None:
        """Create scheduler safety state without claiming a Recovery package exists.

        A truly new blank project may adopt its initial Current generation as a
        baseline. Imports/recovered projects must begin unprotected so the scheduler
        creates a verified local Recovery point instead of trusting the shell used to
        materialize them.
        """
        root=Path(root).resolve();recovery_state=root/'backups'/'recovery_state.json'
        recovery_state.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp_name=tempfile.mkstemp(prefix='recovery-state-',suffix='.json',dir=recovery_state.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                payload={'version':2,'workspace_project_id':str(project_id),'tracking_initialized':False,'baseline_generation':0}
                if pristine_new_project:payload['created_with_project']=True
                json.dump(payload,fh,sort_keys=True);fh.flush();os.fsync(fh.fileno())
            _durable_replace(Path(tmp_name),recovery_state)
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass
