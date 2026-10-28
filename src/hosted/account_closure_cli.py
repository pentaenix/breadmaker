"""Dedicated production Cloud Run Job: plan/close/status an exact account.

Default job arguments are --help; there is no scheduler or HTTP listener.
Execution requires the existing erasure SA, an explicit confirmation digest,
and the deployed application's transactional account-fence capability. This
closes authentication only; it NEVER runs the still-gated physical data purge.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from urllib.parse import urlsplit

from hosted.account_closure import StagedAccountClosure, MAX_PROJECTS
from hosted.firestore.contracts import StoredDocument
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore, _snapshot_revision
from hosted.storage.google_driver import GoogleCloudObjectStore

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('ACCOUNT_CLOSURE_REDIRECT_FORBIDDEN')


def _read_json(url, *, headers=None, limit=65536):
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=15) as response:
        raw = response.read(limit+1)
        if len(raw) > limit:
            raise ValueError('ACCOUNT_CLOSURE_RESPONSE_LIMIT')
        return raw


def required_environment(env, *, read_identity=None):
    project = env.get('BREADMAKER_GCP_PROJECT_ID', '')
    if (env.get('BREADMAKER_ENV') != 'production'
            or env.get('BREADMAKER_OPERATOR_MODE') != 'staged_account_closure'
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project)
            or env.get('BREADMAKER_FIRESTORE_DATABASE') != '(default)'
            or env.get('BREADMAKER_RECOVERY_BUCKET') != project+'-breadmaker-recovery'):
        raise ValueError('ACCOUNT_CLOSURE_OPERATOR_CONFIGURATION_REQUIRED')
    if read_identity is None:
        read_identity = lambda: _read_json(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email',
            headers={'Metadata-Flavor':'Google'}, limit=256).decode().strip()
    if read_identity() != f'bm-production-erasure@{project}.iam.gserviceaccount.com':
        raise ValueError('ACCOUNT_CLOSURE_DEDICATED_IDENTITY_REQUIRED')
    return project


def require_deployed_fence(origin, *, read=None):
    parts = urlsplit(origin)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.path not in ('', '/') or parts.query or parts.fragment or parts.port not in (None, 443)):
        raise ValueError('ACCOUNT_CLOSURE_ORIGIN_INVALID')
    raw = (read or _read_json)(origin.rstrip('/')+'/api/runtime')
    data = json.loads(raw)
    if (data.get('mode') != 'hosted' or data.get('metadata', {}).get('environment') != 'production'
            or data.get('capabilities', {}).get('account_erasure_fence') is not True):
        raise ValueError('ACCOUNT_CLOSURE_DEPLOYED_FENCE_REQUIRED')


class FirebaseClosureIdentity:
    """Only explicit SDK UserNotFound is absence; all other failures propagate."""
    def __init__(self, auth, app):
        self.auth, self.app = auth, app

    def get(self, uid):
        try:
            return self.auth.get_user(uid, app=self.app)
        except self.auth.UserNotFoundError:
            return None

    def _mutate(self, method, uid, **kwargs):
        try:
            getattr(self.auth, method)(uid, app=self.app, **kwargs)
        except self.auth.UserNotFoundError:
            pass  # Concurrent retry still must confirm absence with get().

    def disable(self, uid): self._mutate('update_user', uid, disabled=True)
    def revoke(self, uid): self._mutate('revoke_refresh_tokens', uid)
    def delete(self, uid): self._mutate('delete_user', uid)


def _rows(query, *, transaction=None):
    rows = []
    for doc in query.limit(MAX_PROJECTS+1).stream(transaction=transaction):
        rows.append(StoredDocument(tuple(doc.reference.path.split('/')), doc.to_dict() or {}, _snapshot_revision(doc)))
        if len(rows) > MAX_PROJECTS:
            raise ValueError('ACCOUNT_CLOSURE_QUERY_LIMIT')
    return rows


class _BoundedTransaction:
    def __init__(self, tx): self.tx = tx
    def __getattr__(self, name): return getattr(self.tx, name)
    def query_collection(self, path, *, field=None, value=None):
        query = self.tx._client.collection('/'.join(path))
        if field is not None:
            from google.cloud.firestore_v1.base_query import FieldFilter
            query = query.where(filter=FieldFilter(field, '==', value))
        return _rows(query, transaction=self.tx._transaction)


class _BoundedClosureStore:
    def __init__(self, store): self.store = store
    def __getattr__(self, name): return getattr(self.store, name)
    def query_collection(self, path, *, field=None, value=None):
        query = self.store._client.collection('/'.join(path))
        if field is not None:
            from google.cloud.firestore_v1.base_query import FieldFilter
            query = query.where(filter=FieldFilter(field, '==', value))
        return _rows(query)
    def query_collection_group(self, collection, *, field, value):
        from google.cloud.firestore_v1.base_query import FieldFilter
        return _rows(self.store._client.collection_group(collection).where(filter=FieldFilter(field, '==', value)))
    def run_transaction(self, fn, **kwargs):
        return self.store.run_transaction(lambda tx: fn(_BoundedTransaction(tx)), **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'close', 'status'))
    parser.add_argument('--email', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--confirm')
    args = parser.parse_args(argv)
    try:
        project = required_environment(os.environ)
        if args.action == 'close':
            if not args.confirm:
                raise ValueError('ACCOUNT_CLOSURE_CONFIRMATION_REQUIRED')
            require_deployed_fence(os.environ.get('BREADMAKER_PUBLIC_ORIGIN', ''))
        from auth.identity_platform import build_firebase_admin_auth
        admin = build_firebase_admin_auth(project_id=project)
        identities = FirebaseClosureIdentity(admin._auth, admin._app)
        documents = _BoundedClosureStore(GoogleFirestoreDocumentStore(project=project, database='(default)'))
        audit = GoogleCloudObjectStore(project=project, bucket=project+'-breadmaker-recovery')
        engine = StagedAccountClosure(documents, identities, audit, project=project)
        if args.action == 'plan':
            result = engine.plan(args.email, operation=args.operation).public()
        elif args.action == 'status':
            result = engine.status(args.email, operation=args.operation)
        else:
            result = engine.close(args.email, operation=args.operation, confirmation=args.confirm)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Never echo Firebase/SDK errors, emails, tokens, provider UIDs or paths.
        print('ACCOUNT_CLOSURE_FAILED: completion not confirmed; inspect/retry the same operation.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
