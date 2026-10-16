from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class ProjectRecord:
    id: str
    name: str
    path: str
    created_at: str
    updated_at: str
    last_opened_at: str

@dataclass(frozen=True)
class BackupOperation:
    project_id: str
    source_generation: int
    target_operation: str
    expected_authoritative_generation: int
    source_token: str


class WorkspaceError(RuntimeError):
    pass


BACKUP_DESTINATION_KINDS={'local','synced_folder','remote'}

def _normalize_backup_destination_kind(value):
    kind=str(value or 'local').strip().lower()
    if kind not in BACKUP_DESTINATION_KINDS:
        raise WorkspaceError('BACKUP_DESTINATION_KIND_INVALID')
    return kind

class BackupProvider:
    provider_type = ''
    def resolve(self, workspace, destination:dict[str,Any]) -> dict[str,Any]: raise NotImplementedError
    def store(self, workspace, source:Path, rec:dict[str,Any], destination:dict[str,Any], source_token:str) -> dict[str,Any]: raise NotImplementedError
    def list_backups(self, workspace, rec:dict[str,Any], destination:dict[str,Any]) -> list[dict[str,Any]]: raise NotImplementedError
    def discover(self, workspace, destination:dict[str,Any]) -> list[dict[str,Any]]: raise NotImplementedError
    def materialize(self, workspace, destination:dict[str,Any], filename:str, target_dir:Path) -> Path: raise NotImplementedError
    def erase_managed(self, workspace, destination:dict[str,Any], project_ids:set[str]) -> int: raise NotImplementedError
