"""Account-owned optional statistics choice; this module collects no activity.

The private field lives on the account root so existing erasure retention and
deletion apply. All writes use the normal lifecycle-fenced document store.
"""
from datetime import datetime, timezone
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.ids import require_user_id

NOTICE_VERSION = 'hosted-usage-v1'
NOTICE_SCOPE = (
    'Optional account-linked hosted app statistics to improve Breadmaker: '
    'foreground activity windows, active-minute counts, successful writing-save '
    'counts, project/item creation counts and task-status counts. '
    'No writing, names, titles, paths, keystrokes or advertising use. '
    'Activity records retained for up to 30 days. '
    'Either choice is remembered across devices and can be changed in Privacy.'
)
NOTICE_SHA256 = sha256(NOTICE_SCOPE.encode()).hexdigest()
FIELD = '_bm_usage_statistics'


class UsageConsent:
    def __init__(self, documents, clock=None):
        self.documents = documents
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))

    @staticmethod
    def _account(row):
        if row is None or row.data.get('status') != 'active' or row.data.get('_bm_erasure_operation'):
            raise HostedAuthorizationError('ACCOUNT_UNAVAILABLE')
        return dict(row.data)

    @staticmethod
    def _view(data):
        record = data.get(FIELD)
        if record is None:
            return dict(choice=None, allowed=False, needs_choice=True, revision=0,
                        notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)
        if (not isinstance(record, dict) or type(record.get('choice')) is not bool
                or type(record.get('revision')) is not int or not 1 <= record['revision'] < 2**53):
            raise HTTPException(503, 'USAGE_CHOICE_UNAVAILABLE')
        current = record.get('notice_version') == NOTICE_VERSION and record.get('notice_sha256') == NOTICE_SHA256
        return dict(choice=record['choice'] if current else None,
                    allowed=current and record['choice'], needs_choice=not current,
                    revision=record['revision'], notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)

    def get(self, uid):
        uid = require_user_id(uid)
        def read(tx):
            data = self._account(tx.get(('users', uid)))
            if tx.get(('account_erasure_locks', uid)) is not None:
                raise HostedAuthorizationError('ACCOUNT_UNAVAILABLE')
            return self._view(data)
        return self.documents.run_transaction(read)

    def save(self, uid, body):
        uid = require_user_id(uid)
        if (not isinstance(body, dict) or set(body) != {'choice', 'revision', 'notice_version', 'notice_sha256'}
                or type(body.get('choice')) is not bool or type(body.get('revision')) is not int
                or not 0 <= body['revision'] < 2**53-1
                or not isinstance(body.get('notice_version'), str) or not isinstance(body.get('notice_sha256'), str)):
            raise HTTPException(400, 'USAGE_CHOICE_INVALID')
        if body['notice_version'] != NOTICE_VERSION or body['notice_sha256'] != NOTICE_SHA256:
            raise HTTPException(409, dict(code='USAGE_NOTICE_CHANGED', message='Reload Breadmaker before choosing.'))
        stamp = self.clock()
        def write(tx):
            path = ('users', uid)
            data = self._account(tx.get(path))
            if tx.get(('account_erasure_locks', uid)) is not None:
                raise HostedAuthorizationError('ACCOUNT_UNAVAILABLE')
            current = self._view(data)
            # A network retry may repeat the immediately preceding choice. An
            # older allow can never overwrite a subsequent refusal.
            if (not current['needs_choice'] and current['choice'] == body['choice']
                    and current['revision'] == body['revision'] + 1):
                return current
            if current['revision'] != body['revision']:
                raise DocumentConflict('USAGE_CHOICE_CHANGED')
            data[FIELD] = dict(choice=body['choice'], revision=current['revision'] + 1,
                               notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256,
                               updated_at=stamp)
            data.update(generation=int(data['generation']) + 1, updated_at=stamp)
            tx.set(path, data)
            return self._view(data)
        return self.documents.run_transaction(write)


def router_for(runtime):
    router = APIRouter()
    def public(value):
        from hosted.activity.records import enabled
        return {**value, 'collection_enabled': enabled()}
    def service():
        if runtime.mode != 'hosted' or runtime.sessions.metadata().get('auth_mode') != 'identity_platform':
            raise HTTPException(404, 'NOT_FOUND')
        return UsageConsent(runtime.identity.documents)

    @router.get('/api/account/usage-consent')
    def get_choice():
        return public(service().get(runtime.identity.request_user_id()))

    @router.put('/api/account/usage-consent')
    async def save_choice(request: Request):
        # Bound the stream before parsing; never accept browser identity/time.
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 512:
                raise HTTPException(413, 'USAGE_CHOICE_TOO_LARGE')
        import json
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate')
                result[key] = value
            return result
        try:
            body = json.loads(payload, object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'USAGE_CHOICE_INVALID') from None
        owner = runtime.identity.request_user_id()
        return public(await run_in_threadpool(service().save, owner, body))

    return router
