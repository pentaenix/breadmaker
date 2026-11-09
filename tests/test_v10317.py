from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v10317_corrective_release_preserves_security_and_identity_invariants(tmp_path, monkeypatch):
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,3,17)
    from runtime import build_runtime
    from workspace import Workspace
    from storage import database as storage_database

    # Local mode never requires an account, even when hosted test auth is configured.
    monkeypatch.setenv('BREADMAKER_RUNTIME', 'local')
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'test')
    monkeypatch.setenv('BREADMAKER_ENV', 'test')
    monkeypatch.setenv('BREADMAKER_TEST_AUTH_SECRET', 'x' * 40)
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE', '0')
    try:
        local = build_runtime(Workspace(tmp_path / 'local'))
        assert local.sessions.requires_authentication is False
        assert local.accounts.enabled is False
        assert local.capabilities.supports('account_authentication') is False
        # Hosted test sessions do not enable a production login provider.
        monkeypatch.setenv('BREADMAKER_RUNTIME', 'hosted_dev')
        hosted = build_runtime(Workspace(tmp_path / 'hosted'))
        assert hosted.sessions.requires_authentication is True
        assert hosted.capabilities.supports('account_authentication') is False
    finally:
        storage_database.release_all_instance_locks()
    requirements=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    assert 'cryptography==50.0.1' in requirements
    assert 'starlette==1.7.0' in requirements


def test_v10317_route_contract_uses_public_effective_route_surface():
    source=(ROOT/'tests/test_v1020.py').read_text(encoding='utf-8')
    assert "app_module_fixture.app.openapi()['paths']" in source
    assert 'route.path' not in source


def test_v10317_local_and_hosted_image_picker_contracts_are_separate():
    index=(ROOT/'static/index.html').read_text(encoding='utf-8')
    boot=(ROOT/'static/js/core/boot.js').read_text(encoding='utf-8')
    assert 'image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp' in index
    assert "HOSTED_SAFE_IMAGE_ACCEPT='image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp'" in boot
    assert "runtimeSupports('hosted_projects')" in boot
