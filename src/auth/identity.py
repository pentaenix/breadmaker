from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AuthorizationError(RuntimeError):
    pass


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'owner': frozenset({
        'project.read', 'project.edit', 'project.manage', 'project.delete', 'project.members.manage',
        'review.manage', 'review.process', 'task.create', 'checkpoint.create',
    }),
    # 9.2 intentionally keeps local membership small: Editors can work in the project
    # but cannot delete it or change who belongs to it. Read-only collaboration remains
    # a later hosted/runtime concern; Review Participants stay a separate domain.
    'editor': frozenset({
        'project.read', 'project.edit',
        'review.manage', 'review.process', 'task.create', 'checkpoint.create',
    }),
}


@dataclass(frozen=True)
class Actor:
    user_id: str
    member_id: str
    display_name: str
    kind: str
    role: str
    permissions: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            'id': self.user_id,
            'user_id': self.user_id,
            'member_id': self.member_id,
            'display_name': self.display_name,
            'kind': self.kind,
            'role': self.role,
            'permissions': list(self.permissions),
        }

    def event_snapshot(self) -> dict[str, str]:
        # Never persist installation-local usr_ ids in project content. member_id is
        # scoped to this local project relationship and display_name is a human-readable
        # snapshot for durable Review history/package portability.
        return {
            'member_id': self.member_id,
            'display_name': self.display_name,
            'role': self.role,
            'kind': 'breadmaker_user',
        }


def permissions_for_role(role: str) -> tuple[str, ...]:
    return tuple(sorted(ROLE_PERMISSIONS.get(str(role or ''), frozenset())))


def current_actor(workspace, project_id: str | None = None, *, require_membership: bool = True, user_id: str | None = None) -> Actor:
    profile = workspace.local_profile(user_id)
    pid = str(project_id or workspace.active_id() or '')
    membership = None
    if pid:
        try:membership = workspace.project_membership(pid, profile['user_id'])
        except Exception:
            if require_membership:raise AuthorizationError('PROJECT_MEMBERSHIP_REQUIRED')
    if require_membership and not membership:
        raise AuthorizationError('PROJECT_MEMBERSHIP_REQUIRED')
    role = str((membership or {}).get('role') or '')
    return Actor(
        user_id=str(profile['user_id']),
        member_id=str((membership or {}).get('member_id') or ''),
        display_name=str(profile.get('display_name') or 'Writer'),
        kind=str(profile.get('kind') or 'local'),
        role=role,
        permissions=permissions_for_role(role),
    )


def can(workspace, permission: str, project_id: str | None = None, *, user_id: str | None = None) -> bool:
    try:
        actor = current_actor(workspace, project_id, user_id=user_id)
    except Exception:
        # Keep workspace/storage failures fail-closed. Do not accidentally turn a
        # corrupt membership sidecar into authorization.
        return False
    return str(permission) in actor.permissions


def require(workspace, permission: str, project_id: str | None = None, *, user_id: str | None = None) -> Actor:
    actor = current_actor(workspace, project_id, user_id=user_id)
    if str(permission) not in actor.permissions:
        raise AuthorizationError('PROJECT_PERMISSION_DENIED')
    return actor
