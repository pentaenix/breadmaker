"""CI mirror selection preserves the runner's existing trust boundary."""
import pytest

from scripts.configure_ci_ubuntu_mirror import main, prefer_https_archive

DEFAULT = ('http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n'
           'https://archive.ubuntu.com/ubuntu/\tpriority:2\n'
           'https://security.ubuntu.com/ubuntu/\tpriority:3\n')


def test_ci_mirror_only_swaps_priorities_and_is_idempotent():
    result = prefer_https_archive(DEFAULT)
    assert result == DEFAULT.replace('priority:1', 'priority:temporary').replace(
        'priority:2', 'priority:1').replace('priority:temporary', 'priority:2')
    assert {row.split()[0] for row in result.splitlines()} == {
        row.split()[0] for row in DEFAULT.splitlines()}
    assert prefer_https_archive(result) == result


@pytest.mark.parametrize('invalid', [
    DEFAULT + DEFAULT.splitlines()[0] + '\n',
    DEFAULT.replace('https://archive.ubuntu.com/', 'https://untrusted.invalid/'),
    DEFAULT.replace('priority:3', 'priority:1'),
    '',
])
def test_ci_mirror_rejects_unknown_duplicate_or_inconsistent_configuration(invalid):
    with pytest.raises(ValueError, match='CI_APT_MIRROR_LAYOUT_CHANGED'):
        prefer_https_archive(invalid)


def test_ci_mirror_cli_refuses_non_ci_context_before_reading_system_files(monkeypatch, capsys):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert main() == 2
    assert capsys.readouterr().err.strip() == 'CI_APT_MIRROR_REQUIRES_GITHUB_LINUX_ROOT'
