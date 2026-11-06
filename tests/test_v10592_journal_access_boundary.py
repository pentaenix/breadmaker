"""Participant fixes must not make access-related records restorable by Undo."""
import pytest
from hosted.action_journal import _require_product_path
from hosted.firestore.contracts import HostedValidationError


def test_review_participants_and_identity_state_remain_outside_product_undo():
    pid='synthetic_project';uid='synthetic_owner'
    for path in [('projects',pid,'reviews','review_one','participants','person_one'),
                 ('projects',pid,'members','another_user'),
                 ('projects',pid,'invitations','invitation_one')]:
        with pytest.raises(HostedValidationError,match='HOSTED_ACTION_ENTITY_UNSUPPORTED'):
            _require_product_path(path,pid,uid)
    _require_product_path(('projects',pid,'tasks','task_one'),pid,uid)
