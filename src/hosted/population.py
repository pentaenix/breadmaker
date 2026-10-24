"""Bounded create-only Populate plans; nothing is published before one commit."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

from hosted.domain_store import _ID_FIELDS, _public
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.repositories import (
    _canonical_payload, _fingerprint, _reject_managed_create_fields, render_model_path,
)
from hosted.idempotency import stable_entity_id

_ACTIVE = ContextVar('hosted_population_plan', default=None)
_PARENTS = {'canvas_node': 'canvas', 'canvas_edge': 'canvas',
            'map_line': 'story_map', 'map_item': 'map_line'}


def current_plan():
    return _ACTIVE.get()


class PopulationPlan:
    def __init__(self, domain, project_id, user_id, operation):
        if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
            raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
        self.domain, self.pid, self.uid, self.operation = domain, project_id, user_id, operation
        self.generation = domain.project_generation(project_id, user_id)
        self.stamp = domain.clock.now_iso()
        self.rows = []

    def create(self, entity, parents, payload):
        if entity not in _PARENTS or parents.get('project_id') != self.pid:
            raise HostedValidationError('HOSTED_POPULATE_ENTITY_FORBIDDEN')
        # Leave space for the root/journal bookkeeping; keep the existing journal
        # caps intact rather than turning a large template into partial writes.
        if len(self.rows) >= 95:
            raise HostedValidationError('HOSTED_POPULATE_TOO_LARGE')
        model = self.domain._model(entity)
        field, prefix = _ID_FIELDS[entity]
        operation = self.operation + '_' + str(len(self.rows))
        eid = stable_entity_id(prefix, operation)
        if any(key in payload for key in ('id', field)):
            raise HostedValidationError('HOSTED_POPULATE_ID_RESERVED')
        ids = self.domain._ids_with_entity(entity, parents, eid)
        public = _canonical_payload(self.domain._payload_with_identity(entity, payload, eid))
        _reject_managed_create_fields(public)
        data = {**public, 'generation': 1, 'created_at': self.stamp, 'updated_at': self.stamp,
                '_bm_create_operation': operation, '_bm_create_fingerprint': _fingerprint(public)}
        self.rows.append((entity, model, ids, render_model_path(model, **ids), data))
        if len(json.dumps([row[4] for row in self.rows], ensure_ascii=False).encode()) > 300_000:
            raise HostedValidationError('HOSTED_POPULATE_TOO_LARGE')
        return _public(data)

    def commit(self):
        if not self.rows:
            return
        domain = self.domain

        def mutate(tx):
            _, model, ids, _, _ = self.rows[0]
            root_path, root = domain._authorize_and_project(tx, model, ids, self.uid, mutation=True)
            planned = {row[3]: row[4] for row in self.rows}
            existing = []
            for entity, model, ids, path, data in self.rows:
                domain.entities._authorize(tx, model, ids, self.uid, mutation=True)
                parent_ids = {key:value for key,value in ids.items() if key != _ID_FIELDS[entity][0]}
                parent = render_model_path(domain._model(_PARENTS[entity]), **parent_ids)
                if parent not in planned and tx.get(parent) is None:
                    raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
                if entity == 'canvas_edge':
                    for key in ('from_node_id', 'to_node_id'):
                        node_ids = {**parent_ids, 'node_id': str(data.get(key) or '')}
                        node_path = render_model_path(domain._model('canvas_node'), **node_ids)
                        if node_path not in planned and tx.get(node_path) is None:
                            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
                prior = tx.get(path)
                if prior is not None and any(prior.data.get(key) != data[key]
                        for key in ('_bm_create_operation', '_bm_create_fingerprint')):
                    raise DocumentConflict('HOSTED_POPULATE_OPERATION_CONFLICT')
                existing.append(prior)
            if all(row is not None for row in existing):
                return
            if any(row is not None for row in existing):
                raise DocumentConflict('HOSTED_POPULATE_PARTIAL_REPLAY')
            # All reads/validation precede all writes (also required by Firestore).
            domain._advance_project(tx, root_path, root, expected_generation=self.generation, stamp=self.stamp)
            for _, _, _, path, data in self.rows:
                tx.create(path, data)

        domain.documents.run_transaction(mutate)


@contextmanager
def atomic_population(domain, project_id, user_id, operation):
    if current_plan() is not None:
        raise HostedValidationError('HOSTED_POPULATE_NESTED')
    plan = PopulationPlan(domain, project_id, user_id, operation)
    token = _ACTIVE.set(plan)
    try:
        yield plan
        plan.commit()
    finally:
        _ACTIVE.reset(token)
