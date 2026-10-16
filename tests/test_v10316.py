from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v10316_release_and_security_dependency_pins_remain_current():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,3,16)
    req=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    assert 'fastapi==0.141.1' in req
    assert 'starlette==1.7.0' in req
    assert 'cryptography==50.0.1' in req


def test_v10316_verifier_selects_supported_python_and_rebuilds_stale_venv():
    verifier=(ROOT/'verify-release').read_text(encoding='utf-8')
    assert 'python3.14 python3.13 python3.12 python3.11 python3.10 python3' in verifier
    assert 'assert sys.version_info >= (3, 10)' in verifier
    assert 'Rebuilding stale verification environment with Python 3.10+' in verifier
    assert 'rm -rf .venv' in verifier
    assert 'export PYTHON="$SYSTEM_PYTHON"' in verifier
