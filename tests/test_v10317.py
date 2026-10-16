from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v10317_corrective_release_preserves_security_and_identity_invariants():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,3,17)
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'local Breadmaker remains authentication-free' in architecture
    assert 'hosted login remains disabled' in architecture
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
