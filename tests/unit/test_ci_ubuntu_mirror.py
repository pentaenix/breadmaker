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


def _github_linux_root(monkeypatch):
    import scripts.configure_ci_ubuntu_mirror as module
    monkeypatch.setattr(module.sys,'platform','linux')
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_OS','Linux')
    monkeypatch.setattr(module.os,'geteuid',lambda:0)
    monkeypatch.setattr(module.platform,'freedesktop_os_release',lambda:{'ID':'ubuntu','VERSION_ID':'24.04'})
    return module


def test_ci_mirror_missing_optional_list_preserves_vendor_sources(monkeypatch,capsys):
    module=_github_linux_root(monkeypatch)
    class MissingList:
        def read_text(self):raise FileNotFoundError('optional mirror list absent')
        def write_text(self,value):raise AssertionError('must never create mirror configuration')
    monkeypatch.setattr(module,'Path',lambda _:MissingList())
    assert module.main()==0
    assert 'repositories left unchanged' in capsys.readouterr().out


@pytest.mark.parametrize('error',[PermissionError('denied'),ValueError('malformed')])
def test_ci_mirror_other_errors_still_block_without_writes(monkeypatch,capsys,error):
    module=_github_linux_root(monkeypatch)
    class UnreadableList:
        def read_text(self):raise error
        def write_text(self,value):raise AssertionError('must not write on failure')
    monkeypatch.setattr(module,'Path',lambda _:UnreadableList())
    assert module.main()==2
    assert 'CI_APT_MIRROR_CONFIGURATION_BLOCKED' in capsys.readouterr().err


def test_ci_mirror_existing_malformed_list_still_blocks(monkeypatch,capsys):
    module=_github_linux_root(monkeypatch)
    class MalformedList:
        def read_text(self):return DEFAULT+'https://untrusted.invalid/ priority:4\n'
        def write_text(self,value):raise AssertionError('must not modify unknown mirror configuration')
    monkeypatch.setattr(module,'Path',lambda _:MalformedList())
    assert module.main()==2
    assert 'ValueError' in capsys.readouterr().err
