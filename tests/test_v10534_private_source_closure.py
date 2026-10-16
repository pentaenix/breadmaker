"""M12 source contract; execute only through the owner's release verifier."""
from __future__ import annotations

import pytest

from test_v10524_private_live_restore import OWNER, private_store
from hosted.firestore.contracts import HostedValidationError, ModelContract
from hosted.firestore.model_registry import MODELS
from hosted.private_source_contract import (
    PROJECT_ROOT, RECREATED_AUTHORITY, DISABLED_PRIVATE_FAMILIES,
    PRIVATE_HYBRIDS, audit_private_model_coverage, require_private_entity_supported,
)


def test_v10534_registry_fully_classified_before_private_bootstrap():
    reviewed = audit_private_model_coverage()
    assert 'manuscript_block' in reviewed
    assert PRIVATE_HYBRIDS <= set(reviewed)
    assert DISABLED_PRIVATE_FAMILIES <= set(reviewed)
    assert PROJECT_ROOT | RECREATED_AUTHORITY <= set(reviewed)


def test_v10534_a_new_plaintext_family_cannot_silently_enter_private_mode(monkeypatch):
    monkeypatch.setitem(MODELS, 'future_writer_content', ModelContract(
        logical_entity='future_writer_content',
        path_template='projects/{project_id}/future/{content_id}',
        authorization_policy='project_member',
        concurrency_policy='entity_generation_cas',
        hosted_store='firestore'))
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_MODEL_UNREVIEWED'):
        audit_private_model_coverage()
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_MODEL_UNREVIEWED'):
        private_store(OWNER)


@pytest.mark.parametrize('family, ids, parents', [
    ('backup_destination_configuration', {'destination_id':'dest_1'}, {'project_id':'unused'}),
    ('checkpoint_asset_reference', {'checkpoint_id':'cp_1','asset_ref_id':'ref_1'}, {'project_id':'unused','checkpoint_id':'cp_1'}),
])
def test_v10534_unsupported_operational_family_denied_on_every_generic_crud(family, ids, parents):
    docs, _live, project, _codec = private_store(OWNER)
    pid = project.create('Writer document')['id']
    scoped = {'project_id': pid, **ids}
    collection_ids = {'project_id': pid, **{key:val for key,val in parents.items() if key != 'project_id'}}
    domain = project.domain
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        domain.create(family, collection_ids, {'secret': 'not permissible'},
                      user_id=OWNER, entity_id=list(ids.values())[-1])
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        domain.get(family, scoped, user_id=OWNER)
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        domain.list(family, collection_ids, user_id=OWNER)
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        domain.update(family, scoped, {'secret': 'no'}, user_id=OWNER)
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        domain.delete(family, scoped, user_id=OWNER)
    assert docs.get(('projects', pid, 'backup_destinations', 'dest_1')) is None
    assert docs.get(('projects', pid, 'checkpoints', 'cp_1', 'asset_refs', 'ref_1')) is None


def test_v10534_supported_content_paths_remain_usable():
    _docs, _live, project, _codec = private_store(OWNER)
    pid = project.create('Writer document')['id']
    result = project.domain.create('task', {'project_id': pid},
        {'title': 'Encrypted author text'}, user_id=OWNER, entity_id='task_closure')
    assert result['title'] == 'Encrypted author text'
    assert project.domain.get('task', {'project_id': pid, 'task_id': 'task_closure'},
                              user_id=OWNER)['title'] == 'Encrypted author text'
    assert any(row['title'] == 'Encrypted author text'
               for row in project.domain.list('task', {'project_id': pid}, user_id=OWNER))
    assert project.domain.get('manuscript_block', {'project_id': pid, 'block_id': 'b_1'},
                              user_id=OWNER)['block_id'] == 'b_1'


def test_v10534_nonproject_private_models_remain_explicitly_unportable():
    for logical in ('hosted_user_profile', 'identity_binding'):
        assert logical in MODELS
        assert not MODELS[logical].path_template.startswith('projects/{project_id}')
        assert logical not in audit_private_model_coverage()
        with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
            require_private_entity_supported(logical)


def test_v10534_review_participant_row_is_not_external_account_authority():
    from hosted.firestore.contracts import ProjectUnavailable
    from hosted.runtime_services import ensure_hosted_user
    outsider = 'usr_' + 'c' * 32
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('Private reader copy')['id']
    source.domain.create('review', {'project_id': pid}, {'name': 'Beta'},
                         user_id=OWNER, entity_id='review_beta')
    source.domain.create('review_participant',
                         {'project_id': pid, 'review_id': 'review_beta'},
                         {'display_name': 'A reviewer', 'status': 'active',
                          'external_user_id': outsider},
                         user_id=OWNER, entity_id='participant_guest')
    ensure_hosted_user(docs, user_id=outsider, display_name='Guest')
    with pytest.raises(ProjectUnavailable):
        source.domain.get('review_participant',
            {'project_id': pid, 'review_id': 'review_beta',
             'participant_id': 'participant_guest'}, user_id=outsider)
