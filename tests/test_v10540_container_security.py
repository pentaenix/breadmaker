"""Deployment-only container security regression coverage for the first release."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_production_runtime_excludes_build_os_tools_and_retains_package_evidence():
    docker=(ROOT/'deploy/Dockerfile.hosted').read_text()
    runtime=docker.split('FROM gcr.io/distroless/cc-debian13:nonroot@sha256:',1)[1]
    assert 'COPY --from=build /usr/local /usr/local' in runtime
    assert 'COPY --from=build /runtime-status /var/lib/dpkg/status.d' in runtime
    assert 'COPY --from=build / /' not in runtime
    assert 'USER 10001:10001' in runtime
    assert 'import ssl,sqlite3,bz2,lzma,ctypes,uuid' in runtime
    assert 'scripts/assert_hosted_image_stack.py' in runtime
    assert "find_spec('pip') is None" in runtime
    assert "find_spec('ensurepip') is None" in runtime


def test_hosted_container_pins_patched_transitive_dependencies():
    text=(ROOT/'deploy/constraints-hosted.txt').read_text()
    assert 'msgpack==1.2.3' in text and 'urllib3==2.8.0' in text and 'setuptools==84.2.0' in text
