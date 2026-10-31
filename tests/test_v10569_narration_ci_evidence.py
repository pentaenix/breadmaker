"""Small artifact inventory: exact source, no symlinks or large binary payloads."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('narration_ci_evidence',ROOT/'tools/narration/ci_evidence.py')
owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)


@pytest.fixture
def runtime(tmp_path):
    for index in range(20):(tmp_path/f'module-{index}.mjs').write_text('export {};')
    path='dictionaries/en-us.txt';digest=hashlib.sha256(b'x').hexdigest()
    manifest=dict(v=1,assets=[dict(id=hashlib.sha256(path.encode()).hexdigest()[:24],path=path,
        size=1,sha256=digest,chunks=[dict(size=1,sha256=digest)])])
    (tmp_path/'hosted-manifest.json').write_text(json.dumps(manifest))
    return tmp_path


def test_inventory_binds_exact_source_helper_and_every_small_runtime_file(runtime):
    result=owner.evidence(runtime,'a'*40,'b'*64)
    assert result['editor_sha']=='a'*40 and result['helper_base']=='b'*64
    assert len(result['runtime'])==21 and len(result['helper_sources'])==64
    for item in result['runtime']:
        raw=(runtime/item['path']).read_bytes()
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']


def test_binary_and_symlink_in_artifact_are_forbidden(runtime):
    binary=runtime/'model.onnx';binary.write_bytes(b'not runtime code')
    with pytest.raises(ValueError,match='BINARY_FORBIDDEN'):owner.evidence(runtime,'a'*40,'b'*64)
    binary.unlink()
    (runtime/'alias.mjs').symlink_to(runtime/'module-0.mjs')
    with pytest.raises(ValueError,match='SYMLINK'):owner.evidence(runtime,'a'*40,'b'*64)


def test_mutable_or_missing_release_identity_is_not_artifact_provenance(runtime):
    for sha,base in [('main','b'*64),('a'*40,'latest')]:
        with pytest.raises(ValueError,match='PROVENANCE'):owner.evidence(runtime,sha,base)


def test_scan_requires_os_and_python_coverage_and_rejects_findings():
    report=dict(SchemaVersion=2,ArtifactType='container_image',Results=[
        dict(Class='os-pkgs',Type='debian'),dict(Class='lang-pkgs',Type='python-pkg')])
    assert owner.check_scan(report)['high_critical']==0
    with pytest.raises(ValueError,match='COVERAGE'):
        owner.check_scan({**report,'Results':report['Results'][:1]})
    for severity in ('HIGH','CRITICAL','UNKNOWN'):
        bad={**report,'Results':[report['Results'][0],{**report['Results'][1],'Vulnerabilities':[{'Severity':severity}]}]}
        with pytest.raises(ValueError,match='SCAN_BLOCKED'):owner.check_scan(bad)
