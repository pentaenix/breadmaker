"""Lossless current Review scope projection for desktop-native exports.

Hosted UI records use an inline selection; imported projects can instead carry
association documents. Local scopes are unordered chapter/scene associations.
Translate either representation, and refuse contradictory representations.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


class ReviewScopeShapeError(ValueError):
    pass


def _reject(reason: str) -> None:
    raise ReviewScopeShapeError('HOSTED_PORTABLE_REVIEW_SCOPE_' + reason)


def _parts(item, project_id):
    if not isinstance(item, dict) or not isinstance(item.get('ids'), dict) or not isinstance(item.get('data'), dict):
        _reject('INVALID')
    identity, row = dict(item['ids']), dict(item['data'])
    if identity.get('project_id') != project_id:
        _reject('REFERENCE_INVALID')
    return identity, row


def prepare_review_scopes(records: Mapping[str, Any], project_id: str) -> dict[str, Any]:
    result = dict(records)
    kinds = {'chapters': ('review_scope_chapter', 'chapter_id', 'chapter'),
             'scenes': ('review_scope_scene', 'scene_id', 'scene')}
    reviews = {}
    review_items = records.get('review', [])
    if not isinstance(review_items, list):
        _reject('INVALID')
    for item in review_items:
        identity, row = _parts(item, project_id)
        rid = row.get('id')
        if not isinstance(rid, str) or not rid or identity.get('review_id') != rid or rid in reviews:
            _reject('REFERENCE_INVALID')
        if row.get('review_id', rid) != rid:
            _reject('REFERENCE_INVALID')
        kind = row.get('scope_type', 'manuscript')
        if kind not in ('manuscript', 'chapters', 'scenes'):
            _reject('INVALID')
        selection = row.pop('scope_ids', None)
        if 'scope_ids' in item['data']:
            if not isinstance(selection, list) or any(not isinstance(value, str) or not value for value in selection):
                _reject('INVALID')
            if len(selection) != len(set(selection)) or bool(selection) != (kind != 'manuscript'):
                _reject('INVALID')
        reviews[rid] = {'item': {'ids': identity, 'data': row}, 'kind': kind, 'inline': selection}

    for kind, (logical, field, target) in kinds.items():
        rows = records.get(logical, [])
        if not isinstance(rows, list):
            _reject('INVALID')
        available = set()
        targets = records.get(target, [])
        if not isinstance(targets, list):
            _reject('INVALID')
        for item in targets:
            _, row = _parts(item, project_id)
            if not isinstance(row.get('id'), str) or not row['id']:
                _reject('REFERENCE_INVALID')
            available.add(row['id'])
        associations = {}
        for item in rows:
            identity, row = _parts(item, project_id)
            rid, selected = identity.get('review_id'), identity.get(field)
            if not isinstance(rid, str) or not isinstance(selected, str) or rid not in reviews or selected not in available:
                _reject('REFERENCE_INVALID')
            if 'scope_link_id' in identity or 'scope_link_id' in row:
                _reject('INVALID')
            if row.get('id') != selected:
                _reject('REFERENCE_INVALID')
            if row.get('review_id', rid) != rid or row.get(field, selected) != selected:
                _reject('REFERENCE_INVALID')
            if reviews[rid]['kind'] != kind:
                _reject('CONFLICT')
            key = (rid, selected)
            if key in associations:
                _reject('CONFLICT')
            associations[key] = (identity, row)
        for rid, review in reviews.items():
            if review['kind'] != kind:
                continue
            relational = {selected for owner, selected in associations if owner == rid}
            inline = review['inline']
            if inline is not None:
                if not set(inline) <= available:
                    _reject('REFERENCE_INVALID')
                if relational and relational != set(inline):
                    _reject('CONFLICT')
                for selected in inline:
                    associations.setdefault((rid, selected), (
                        {'project_id': project_id, 'review_id': rid, field: selected},
                        {'review_id': rid, field: selected}))
            if not any(owner == rid for owner, _ in associations):
                _reject('INVALID')
        projected = []
        for (rid, selected), (identity, row) in associations.items():
            # Hosted association identity is composite (Review + target). The
            # native table has an independent integer PK; using target alone
            # would collide when two Reviews select the same Chapter or Scene.
            digest = hashlib.sha256(json.dumps([logical, rid, selected]).encode()).hexdigest()
            identity = {**identity, 'scope_link_id': 'scope_' + digest}
            row = {**row, 'id': identity['scope_link_id'], 'review_id': rid, field: selected}
            projected.append({'ids': identity, 'data': row})
        if projected or logical in records:
            result[logical] = projected
    if 'review' in records:
        result['review'] = [review['item'] for review in reviews.values()]
    return result
