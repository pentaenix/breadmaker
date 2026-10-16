"""M10E isolated restore input-validation correction; human runs all tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]/'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.recovery_drill_cli import required_environment, RecoveryDrillConfigurationError

ENV = {
    'BREADMAKER_ENV': 'staging',
    'BREADMAKER_OPERATOR_MODE': 'isolated_recovery_drill',
    'BREADMAKER_GCP_PROJECT_ID': 'breadmaker-drill-staging-1',
    'BREADMAKER_FIRESTORE_DATABASE': '(default)',
    'BREADMAKER_RECOVERY_BUCKET': 'breadmaker-drill-staging-1-breadmaker-recovery',
}
REQUEST = {
    'project_id': 'a'*32,
    'owner_user_id': 'usr_' + 'b'*32,
    'operation': 'scheduled_20261012',
    'expected_sha256': 'c'*64,
    'expected_generation': 2,
}


def _env(request):
    return {**ENV, 'BREADMAKER_DRILL_REQUEST_JSON': json.dumps(request)}


def test_v1054_staging_restore_rejects_hosted_domain_id_and_operation_errors_as_value_errors():
    for field, value in (
        ('operation', '../another'),
        ('operation', ''),
        ('project_id', '../foreign'),
        ('owner_user_id', 'invalid'),
        ('owner_user_id', None),
    ):
        with pytest.raises(RecoveryDrillConfigurationError):
            required_environment(_env({**REQUEST, field: value}))


def test_v1054_staging_restore_accepts_existing_immutable_recovery_operation_format():
    project, bucket, selection = required_environment(_env(REQUEST))
    assert project == ENV['BREADMAKER_GCP_PROJECT_ID']
    assert bucket == ENV['BREADMAKER_RECOVERY_BUCKET']
    assert selection.operation == REQUEST['operation']
    assert selection.expected_generation == 2


def test_v1054_staging_restore_rejects_untrusted_input_before_sdk_construction():
    # Parser is pure and never instantiates the GCS driver.
    from hosted import recovery_drill_cli
    assert 'GoogleCloudObjectStore' not in required_environment.__code__.co_names
    for payload in ({**REQUEST, 'expected_generation': True},
                    {**REQUEST, 'expected_sha256': 'd'*63},
                    {**REQUEST, 'unexpected': True}):
        with pytest.raises(ValueError):
            recovery_drill_cli.required_environment(_env(payload))
