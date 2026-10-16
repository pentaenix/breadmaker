from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def test_v8153_source_tree_is_grouped_without_root_python_runtime_files():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8,15,3)
    # 10.2.3 keeps only process entrypoints at src/ root; former loose runtime
    # modules now have explicit architectural owners.
    assert {p.name for p in SRC.glob('*.py')} == {'__init__.py','app.py','server.py'}
    for package in ('api','auth','backend','backups','formats','reliability','revision_store','runtime','security','storage','workspace'):
        assert (SRC/package).is_dir(), package
    assert (ROOT/'static').is_dir() and (ROOT/'locales').is_dir() and (ROOT/'config').is_dir()


def test_v8153_launch_and_test_tooling_resolve_src_explicitly():
    run=(ROOT/'run').read_text(encoding='utf-8')
    test=(ROOT/'test').read_text(encoding='utf-8')
    verify=(ROOT/'scripts/verify_release.py').read_text(encoding='utf-8')
    conftest=(ROOT/'tests/conftest.py').read_text(encoding='utf-8')
    assert 'PYTHONPATH="$PWD/src' in run
    assert 'exec ./verify-release "$@"' in test
    assert 'SRC = ROOT / "src"' in verify
    assert 'SRC_ROOT=APP_ROOT / "src"' in conftest


def test_v8153_project_schema_matches_current_pre_alpha_contract():
    schema=(SRC/'storage'/'schema.py').read_text(encoding='utf-8')
    workspace_constants=(SRC/'workspace'/'constants.py').read_text(encoding='utf-8')
    assert 'SCHEMA_VERSION = 42' in schema
    assert 'APP_SCHEMA_VERSION = 42' in workspace_constants
