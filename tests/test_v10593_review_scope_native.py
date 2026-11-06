"""Real hosted scope shapes must retain their selection in native packages."""
import io
import json
import zipfile

import pytest

from hosted.local_portability import LocalPortabilityError, to_local_package
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10369 import _project, _create, _restore


def _review(runtime, pid, kind='manuscript', selected=None, name='Scoped Review'):
    return _create(runtime, 'review', {'project_id': pid}, {
        'name': name, 'description': 'Retain scope and feedback settings',
        'scope_type': kind, 'scope_ids': [] if selected is None else selected,
        'permission_read': True, 'permission_comment': False,
        'permission_suggest': False, 'permission_feedback': False})


def _edited_source(runtime, pid, edit):
    source = HostedProjectPackageService(runtime.projects).export_bytes(pid, OWNER)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    document = json.loads(members['project.json'])
    edit(document['entities'])
    members['project.json'] = json.dumps(document).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return out.getvalue()


def _association(pid, rid, selected, field='chapter_id'):
    return {'ids': {'project_id': pid, 'review_id': rid, field: selected},
            'data': {'id': selected, 'review_id': rid, field: selected}}


def test_manuscript_scope_empty_ui_selection_survives_real_native_import(monkeypatch, tmp_path):
    runtime, pid, _ = _project(monkeypatch)
    review = _review(runtime, pid)
    _create(runtime, 'review_participant', {'project_id': pid, 'review_id': review['id']},
            {'display_name': 'Synthetic reviewer'})
    service = HostedProjectPackageService(runtime.projects)
    source = service.export_bytes(pid, OWNER)
    portable = service.export_local_bytes(pid, OWNER)
    with _restore(tmp_path, portable) as db:
        assert db.execute('SELECT name,description,scope_type FROM reviews').fetchone() == (
            'Scoped Review', 'Retain scope and feedback settings', 'manuscript')
        assert db.execute('SELECT display_name FROM review_participants').fetchone()[0] == 'Synthetic reviewer'
        assert db.execute('SELECT COUNT(*) FROM review_scope_chapters').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM review_scope_scenes').fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    # The derived native package must never rewrite the canonical hosted copy.
    with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(io.BytesIO(service.export_bytes(pid, OWNER))) as after:
        assert {n: before.read(n) for n in before.namelist()} == {n: after.read(n) for n in after.namelist()}


@pytest.mark.parametrize('kind', ['chapters', 'scenes'])
def test_selected_scope_maps_to_local_references_and_back(monkeypatch, tmp_path, kind):
    runtime, pid, chapter = _project(monkeypatch)
    entity = chapter if kind == 'chapters' else _create(runtime, 'scene', {'project_id': pid},
        {'title': 'Selected Scene', 'tab_id': chapter['tab_id'], 'position': 0})
    _review(runtime, pid, kind, [entity['id']])
    service = HostedProjectPackageService(runtime.projects)
    portable = service.export_local_bytes(pid, OWNER)
    scope_table, target_table = ('review_scope_chapters', 'chapters') if kind == 'chapters' else ('review_scope_scenes', 'scenes')
    field = 'chapter_id' if kind == 'chapters' else 'scene_id'
    with _restore(tmp_path, portable) as db:
        assert db.execute('SELECT scope_type FROM reviews').fetchone()[0] == kind
        assert db.execute(f'SELECT t.title FROM {scope_table} s JOIN {target_table} t ON t.id=s.{field}').fetchone()[0] == entity['title']
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    restored = service.import_bytes(portable, OWNER)
    # Legacy native import uses association documents, not the UI's inline list.
    # Both representations must remain exportable without flattening the scope.
    again = service.export_local_bytes(restored['id'], OWNER)
    (tmp_path / 'again').mkdir()
    with _restore(tmp_path / 'again', again) as db:
        assert db.execute(f'SELECT t.title FROM {scope_table} s JOIN {target_table} t ON t.id=s.{field}').fetchone()[0] == entity['title']
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_multiple_selected_chapters_keep_the_complete_selection(monkeypatch, tmp_path):
    runtime, pid, chapter = _project(monkeypatch)
    other = _create(runtime, 'chapter', {'project_id': pid}, {'title': 'Other selected Chapter', 'tab_id': chapter['tab_id'], 'position': 1})
    _review(runtime, pid, 'chapters', [other['id'], chapter['id']])
    portable = HostedProjectPackageService(runtime.projects).export_local_bytes(pid, OWNER)
    with _restore(tmp_path, portable) as db:
        titles = db.execute('SELECT c.title FROM review_scope_chapters s JOIN chapters c ON c.id=s.chapter_id').fetchall()
        assert {row[0] for row in titles} == {chapter['title'], other['title']}
        assert len(titles) == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('inline', [True, False])
@pytest.mark.parametrize('kind', ['chapters', 'scenes'])
def test_shared_selection_across_reviews_has_distinct_local_associations(monkeypatch, tmp_path, inline, kind):
    runtime, pid, chapter = _project(monkeypatch)
    entity = chapter if kind == 'chapters' else _create(runtime, 'scene', {'project_id': pid},
        {'title': 'Shared Scene', 'tab_id': chapter['tab_id'], 'position': 0})
    field = 'chapter_id' if kind == 'chapters' else 'scene_id'
    logical = 'review_scope_chapter' if kind == 'chapters' else 'review_scope_scene'
    table = 'review_scope_chapters' if kind == 'chapters' else 'review_scope_scenes'
    reviews = [_review(runtime, pid, kind, [entity['id']], name=name) for name in ('First', 'Second')]
    def edit(entities):
        entities[logical] = [_association(pid, r['id'], entity['id'], field) for r in reviews]
        if not inline:
            for item in entities['review']:
                item['data'].pop('scope_ids')
    raw = to_local_package(_edited_source(runtime, pid, edit), project_generation=1)
    with _restore(tmp_path, raw) as db:
        assert db.execute(f'SELECT COUNT(DISTINCT id),COUNT(DISTINCT review_id),COUNT(DISTINCT {field}) FROM {table}').fetchone() == (2, 2, 1)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    service = HostedProjectPackageService(runtime.projects)
    restored = service.import_bytes(raw, OWNER)
    again = service.export_local_bytes(restored['id'], OWNER)
    (tmp_path / 'again').mkdir()
    with _restore(tmp_path / 'again', again) as db:
        assert db.execute(f'SELECT COUNT(DISTINCT id),COUNT(DISTINCT review_id),COUNT(DISTINCT {field}) FROM {table}').fetchone() == (2, 2, 1)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_conflicting_inline_and_relational_scope_is_not_discarded(monkeypatch):
    runtime, pid, chapter = _project(monkeypatch)
    other = _create(runtime, 'chapter', {'project_id': pid}, {'title': 'Other', 'tab_id': chapter['tab_id'], 'position': 1})
    review = _review(runtime, pid, 'chapters', [chapter['id']])
    source = _edited_source(runtime, pid, lambda entities: entities.update(
        review_scope_chapter=[_association(pid, review['id'], other['id'])]))
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_REVIEW_SCOPE_CONFLICT'):
        to_local_package(source, project_generation=1)


@pytest.mark.parametrize('case', ['null', 'object', 'duplicate', 'empty', 'nonstring', 'unknown_kind', 'foreign', 'manuscript_nonempty'])
def test_invalid_scope_is_explicitly_rejected(monkeypatch, case):
    runtime, pid, chapter = _project(monkeypatch)
    _review(runtime, pid, 'chapters', [chapter['id']])
    def edit(entities):
        row = entities['review'][0]['data']
        choices = {'null': None, 'object': {}, 'duplicate': [chapter['id'], chapter['id']],
                   'empty': [], 'nonstring': [True], 'foreign': ['chapter_not_in_this_project']}
        if case in choices:
            row['scope_ids'] = choices[case]
        elif case == 'unknown_kind':
            row['scope_type'] = 'unrecognized'
        else:
            row['scope_type'] = 'manuscript'
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_REVIEW_SCOPE_'):
        to_local_package(_edited_source(runtime, pid, edit), project_generation=1)


def test_unknown_association_content_is_not_swallowed_by_projection(monkeypatch):
    runtime, pid, chapter = _project(monkeypatch)
    review = _review(runtime, pid, 'chapters', [chapter['id']])
    association = _association(pid, review['id'], chapter['id'])
    association['data']['unknown_note'] = 'Must not disappear'
    source = _edited_source(runtime, pid, lambda entities: entities.update(review_scope_chapter=[association]))
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_UNREPRESENTABLE_FIELD:review_scope_chapter.unknown_note'):
        to_local_package(source, project_generation=1)


@pytest.mark.parametrize('case', ['review_alias', 'target_alias', 'duplicate_relation', 'association_identity'])
def test_scope_identity_conflicts_fail_before_local_package_creation(monkeypatch, case):
    runtime, pid, chapter = _project(monkeypatch)
    review = _review(runtime, pid, 'chapters', [chapter['id']])
    def edit(entities):
        association = _association(pid, review['id'], chapter['id'])
        entities['review_scope_chapter'] = [association]
        if case == 'review_alias':
            entities['review'][0]['data']['review_id'] = 'review_different'
        elif case == 'target_alias':
            association['data']['chapter_id'] = 'chapter_different'
        elif case == 'association_identity':
            association['data']['id'] = review['id']
        else:
            entities['review_scope_chapter'].append(association)
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_REVIEW_SCOPE_'):
        to_local_package(_edited_source(runtime, pid, edit), project_generation=1)
