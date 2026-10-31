"""Read-only transaction authority for explicitly composed hosted narration.

The HTTP boundary still verifies the provider session/revocation on every request.
This fence binds admission, account lifecycle and project access to publication
and quota transactions; caller-supplied identity never establishes authority.
"""
from auth.identity_platform import IdentityPlatformAdmissionStore
from hosted.firestore.contracts import HostedAuthorizationError, ProjectUnavailable
from hosted.firestore.ids import identity_binding_id, require_project_id, require_user_id


def require_authority(authority, tx, uid, pid, kind):
    if authority(tx, uid, pid, kind) is not True:
        raise HostedAuthorizationError('NARRATION_ACCESS_DENIED')


class NarrationAuthority:
    def __init__(self, domain, *, issuer: str, now, development=False):
        if not development and not str(issuer).startswith('identity-platform://'):
            raise ValueError('NARRATION_IDENTITY_PLATFORM_REQUIRED')
        self.domain, self.issuer, self.now = domain, issuer, now
        self.development = development

    @staticmethod
    def owner(tx, pid):
        owners = tx.query_collection(('projects', pid, 'members'), field='role', value='owner')
        if len(owners) != 1:
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        owner = require_user_id(owners[0].data.get('user_id'))
        if owners[0].path != ('projects', pid, 'members', owner):
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        return owner

    def __call__(self, tx, account_id, project_id, kind):
        uid, pid = require_user_id(account_id), require_project_id(project_id)
        if kind not in {'audio', 'model', 'read', 'write'}:
            raise ValueError('NARRATION_AUTHORITY_KIND_INVALID')
        user = tx.get(('users', uid))
        lock = tx.get(('account_erasure_locks', uid))
        if (user is None or user.data.get('status', 'active') != 'active'
                or user.data.get('_bm_erasure_operation') or lock is not None):
            raise HostedAuthorizationError('NARRATION_ACCESS_DENIED')
        root, member = self.domain.projects._require_member(tx, pid, uid)
        owner = self.owner(tx, pid)
        owner_row = tx.get(('users', owner))
        owner_lock = tx.get(('account_erasure_locks', owner))
        if (root.get('_bm_erasure_operation') or owner_row is None or owner_lock is not None
                or owner_row.data.get('status', 'active') != 'active'
                or owner_row.data.get('_bm_erasure_operation')):
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        # These are the current canonical collaborator roles. Do not invent a
        # new read role here that the project repository does not support.
        if member.get('role') not in {'owner', 'editor'}:
            raise HostedAuthorizationError('NARRATION_ACCESS_DENIED')
        if not self.development:
            bindings = tx.query_collection(('identity_bindings',), field='user_id', value=uid)
            admission = IdentityPlatformAdmissionStore(tx, now=self.now)
            if not any(row.data.get('status') == 'active' and row.data.get('issuer') == self.issuer
                       and row.data.get('user_id') == uid
                       and isinstance(row.data.get('subject'), str) and bool(row.data['subject'])
                       and row.path == ('identity_bindings', identity_binding_id(self.issuer + '\n' + row.data['subject']))
                       and admission.is_admitted(str(row.data.get('subject') or ''))
                       for row in bindings):
                raise HostedAuthorizationError('NARRATION_ACCESS_DENIED')
        return True
