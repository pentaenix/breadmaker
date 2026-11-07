"""Inactive, bounded legacy validation before manuscript-format adoption.

No runtime or route constructs this service. A page cursor is not a Firestore
snapshot: every relevant legacy writer must advance the project generation for
the cross-request proof to hold. This validates only the five manuscript
collections, never a physical erasure/recovery inventory.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import (DocumentConflict, DocumentNotFound, HostedValidationError,
    ProjectUnavailable, require_collection_page_query)
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.idempotency import stable_entity_id
from hosted.manuscript_visibility import (FORMAT_FIELD, FORMAT_VERSION, ManuscriptDocumentStore,
    _MANAGED, decode_project_root)

PAGE_SIZE = 100
_FAMILIES = ('tabs', 'chapters', 'blocks', 'scenes', 'assets')
_EXPIRY_SECONDS = 86400
_DIGEST = re.compile(r'^[0-9a-f]{64}$')
_FIELDS = frozenset({'version', 'project_id', 'actor_user_id', 'operation', 'source_generation',
    'expires_at', 'state', 'family_index', 'cursor', 'next_step', 'records', 'digest'})


class LegacyManuscriptAdoption:
    """Request-bound begin/step/finish protocol over the lifecycle write fence."""
    def __init__(self, view, *, now=time.time):
        if (not isinstance(view, ManuscriptDocumentStore) or view.include_retained
                or not isinstance(view.store, AccountLifecycleDocumentStore)):
            raise HostedValidationError('HOSTED_MANUSCRIPT_REVISION_READER_REQUIRED')
        self.store, self.now = view.store, now

    @staticmethod
    def _job(pid, uid, operation):
        return stable_entity_id('adopt_', f'{pid}_{uid}_{operation}')

    @staticmethod
    def _path(pid, job):
        return ('projects', require_project_id(pid), 'manuscript_adoptions',
            require_opaque_id(job, label='adoption_job'))

    def _scope(self, pid, uid):
        pid, uid = require_project_id(pid), require_user_id(uid)
        if self.store.actor() != uid:
            raise HostedValidationError('HOSTED_IMPORT_AUTHENTICATED_ACTOR_REQUIRED')
        return pid, uid

    def _authority(self, tx, pid, uid, *, generation, enabled=False):
        root_path, member_path = ('projects', pid), ('projects', pid, 'members', uid)
        physical, member = tx.get_many([root_path, member_path])
        root = decode_project_root(physical)
        if (root is None or member is None or root.data.get('status') != 'active'
                or root.data.get('_bm_erasure_operation') or member.data.get('role') not in {'owner', 'editor'}
                or member.data.get('user_id') != uid):
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        if type(root.data.get('generation')) is not int or root.data['generation'] != generation:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        if bool(FORMAT_FIELD in physical.data) != enabled:
            raise DocumentConflict('HOSTED_MANUSCRIPT_ADOPTION_FORMAT_CHANGED')
        if set(physical.data) & (_MANAGED - {FORMAT_FIELD}):
            raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_MARKER_REPAIR_REQUIRED')
        # The frozen model requires exactly one owner; roots do not carry owner
        # identity. Two rows suffice to reject an invalid multi-owner project.
        owners = tx.query_collection_page(('projects', pid, 'members'), limit=2, field='role', value='owner')
        if len(owners.rows) != 1 or owners.next_cursor is not None:
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        owner_member = owners.rows[0]
        owner = require_user_id(owner_member.path[-1])
        if owner_member.data.get('user_id') != owner:
            raise ProjectUnavailable('PROJECT_NOT_FOUND')
        users = sorted({uid, owner})
        paths = [(family, user) for user in users for family in ('users', 'account_erasure_locks')]
        rows = dict(zip(paths, tx.get_many(paths), strict=True))
        for user in users:
            profile = rows[('users', user)]
            if (profile is None or profile.data.get('status', 'active') != 'active'
                    or profile.data.get('_bm_erasure_operation') or rows[('account_erasure_locks', user)] is not None):
                raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
        return physical

    def _state(self, row, pid, uid, job):
        if row is None or row.data.get('actor_user_id') != uid or row.data.get('project_id') != pid:
            raise DocumentNotFound('HOSTED_MANUSCRIPT_ADOPTION_NOT_FOUND')
        data = dict(row.data)
        if (set(data) != _FIELDS or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['state'], str) or data['state'] not in {'scanning', 'ready', 'activated'}
                or any(type(data[key]) is not int or data[key] < 0
                    for key in ('family_index', 'next_step', 'records', 'source_generation'))
                or data['source_generation'] < 1 or data['family_index'] > len(_FAMILIES)
                or not isinstance(data['digest'], str) or not _DIGEST.fullmatch(data['digest'])
                or type(data['expires_at']) not in {int, float} or not math.isfinite(data['expires_at'])
                or not isinstance(data['operation'], str)
                or self._job(pid, uid, require_opaque_id(data['operation'], label='operation')) != job):
            raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_STATE_INVALID')
        done = data['family_index'] == len(_FAMILIES)
        if done != (data['state'] in {'ready', 'activated'}) or done and data['cursor'] is not None:
            raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_STATE_INVALID')
        if not done:
            require_collection_page_query(('projects', pid, _FAMILIES[data['family_index']]),
                limit=PAGE_SIZE, after=data['cursor'], field=None, value=None)
        if data['next_step'] < data['family_index']:
            raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_STATE_INVALID')
        if self.now() >= data['expires_at']:
            raise DocumentConflict('HOSTED_MANUSCRIPT_ADOPTION_EXPIRED')
        return data

    def _load(self, tx, pid, uid, job):
        data = self._state(tx.get(self._path(pid, job)), pid, uid, job)
        root = self._authority(tx, pid, uid, generation=data['source_generation'], enabled=data['state'] == 'activated')
        return data, root

    @staticmethod
    def _public(job, data):
        return {'job_id': job, 'next_step': data['next_step'], 'records_validated': data['records'],
            'family': _FAMILIES[data['family_index']] if data['family_index'] < len(_FAMILIES) else None,
            'ready': data['state'] in {'ready', 'activated'}, 'activated': data['state'] == 'activated',
            'source_generation': data['source_generation']}

    def begin(self, pid, uid, *, generation, operation):
        pid, uid = self._scope(pid, uid)
        operation = require_opaque_id(operation, label='operation')
        if type(generation) is not int or generation < 1:
            raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_GENERATION_INVALID')
        job = self._job(pid, uid, operation)
        def create(tx):
            row = tx.get(self._path(pid, job))
            if row is not None:
                data, _ = self._load(tx, pid, uid, job)
                if data['source_generation'] != generation:
                    raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
                return self._public(job, data)
            self._authority(tx, pid, uid, generation=generation)
            data = {'version': 1, 'project_id': pid, 'actor_user_id': uid, 'operation': operation,
                'source_generation': generation, 'expires_at': self.now() + _EXPIRY_SECONDS,
                'state': 'scanning', 'family_index': 0, 'cursor': None, 'next_step': 0, 'records': 0,
                'digest': hashlib.sha256(b'legacy-manuscript-adoption-v1').hexdigest()}
            tx.create(self._path(pid, job), data)
            return self._public(job, data)
        return self.store.run_transaction(create)

    def status(self, pid, uid, job):
        pid, uid = self._scope(pid, uid)
        return self.store.run_transaction(lambda tx: self._public(job, self._load(tx, pid, uid, job)[0]))

    @staticmethod
    def _parent_id(value):
        if not isinstance(value, str) or not value:
            raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_PARENT_REPAIR_REQUIRED')
        try:
            return require_opaque_id(value)
        except HostedValidationError as exc:
            raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_PARENT_REPAIR_REQUIRED') from exc

    @staticmethod
    def _unmarked(row):
        if set(row.data) & _MANAGED:
            raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_MARKER_REPAIR_REQUIRED')

    def _validate_rows(self, tx, pid, family, rows):
        for row in rows:
            self._unmarked(row)
        if family in {'tabs', 'assets'}:
            return
        chapters = {}
        if family in {'blocks', 'scenes'}:
            ids = {self._parent_id(row.data.get('chapter_id')) for row in rows
                if family == 'blocks' or row.data.get('chapter_id')}
            paths = [('projects', pid, 'chapters', cid) for cid in sorted(ids)]
            chapters = dict(zip((path[-1] for path in paths), tx.get_many(paths), strict=True)) if paths else {}
            for chapter in chapters.values():
                if chapter is None:
                    raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_PARENT_REPAIR_REQUIRED')
                self._unmarked(chapter)
        tab_ids = {self._parent_id(chapter.data.get('tab_id')) for chapter in chapters.values()}
        for row in rows:
            if family == 'chapters' or not row.data.get('chapter_id'):
                tab_ids.add(self._parent_id(row.data.get('tab_id')))
            elif row.data.get('tab_id') and row.data['tab_id'] != chapters[row.data['chapter_id']].data.get('tab_id'):
                raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_PARENT_REPAIR_REQUIRED')
        paths = [('projects', pid, 'tabs', tid) for tid in sorted(tab_ids)]
        for _, tab in zip(paths, tx.get_many(paths), strict=True) if paths else []:
            if tab is None:
                raise HostedValidationError('HOSTED_MANUSCRIPT_LEGACY_PARENT_REPAIR_REQUIRED')
            self._unmarked(tab)

    def step(self, pid, uid, job, expected_step):
        pid, uid = self._scope(pid, uid)
        if type(expected_step) is not int or expected_step < 0:
            raise HostedValidationError('HOSTED_MANUSCRIPT_ADOPTION_STEP_INVALID')
        def validate(tx):
            data, _ = self._load(tx, pid, uid, job)
            if expected_step > data['next_step']:
                raise DocumentConflict('HOSTED_MANUSCRIPT_ADOPTION_OUT_OF_ORDER')
            if expected_step < data['next_step'] or data['state'] != 'scanning':
                return self._public(job, data)
            family = _FAMILIES[data['family_index']]
            page = tx.query_collection_page(('projects', pid, family), limit=PAGE_SIZE, after=data['cursor'])
            self._validate_rows(tx, pid, family, page.rows)
            proof = json.dumps([data['digest'], family, data['cursor'], page.next_cursor,
                [[list(row.path), row.revision] for row in page.rows]], separators=(',', ':')).encode()
            data.update(cursor=page.next_cursor, next_step=data['next_step'] + 1,
                records=data['records'] + len(page.rows), digest=hashlib.sha256(proof).hexdigest())
            if page.next_cursor is None:
                data['family_index'] += 1
                if data['family_index'] == len(_FAMILIES):
                    data['state'] = 'ready'
            tx.set(self._path(pid, job), data)
            return self._public(job, data)
        return self.store.run_transaction(validate)

    def finish(self, pid, uid, job):
        pid, uid = self._scope(pid, uid)
        def activate(tx):
            data, physical = self._load(tx, pid, uid, job)
            if data['state'] == 'activated':
                return self._public(job, data)
            if data['state'] != 'ready':
                raise DocumentConflict('HOSTED_MANUSCRIPT_ADOPTION_NOT_READY')
            tx.set(('projects', pid), {**physical.data, FORMAT_FIELD: FORMAT_VERSION, 'status': 'active_manuscript_v1'})
            data['state'] = 'activated'
            tx.set(self._path(pid, job), data)
            return self._public(job, data)
        return self.store.run_transaction(activate)
