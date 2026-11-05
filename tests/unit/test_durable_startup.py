"""A failed durable server must retain its cause and remain a failed gate."""
from types import SimpleNamespace

import pytest

from tests import test_e2e as durable


def prepare(monkeypatch,tmp_path,*,exit_code=None,health=503,output=b'synthetic startup diagnostic'):
    calls={'health':0,'sleep':0,'terminate':0,'wait':0}
    class Process:
        code=exit_code
        def poll(self):return self.code
        def terminate(self):calls['terminate']+=1;self.code=-15
        def wait(self,timeout):calls['wait']+=1;return self.code
        def kill(self):self.code=-9
    process=Process()
    def launch(*args,**kwargs):
        assert kwargs['stderr']==durable.subprocess.STDOUT
        kwargs['stdout'].write(output);kwargs['stdout'].flush()
        return process
    def request(*args,**kwargs):
        calls['health']+=1
        assert kwargs['timeout']==.25
        if isinstance(health,Exception):raise health
        return SimpleNamespace(ok=health==200,status_code=health)
    monkeypatch.setenv('BREADMAKER_DURABLE_E2E_HOME',str(tmp_path))
    monkeypatch.setattr(durable,'_free_port',lambda:43210)
    monkeypatch.setattr(durable.subprocess,'Popen',launch)
    monkeypatch.setattr(durable.requests,'get',request)
    monkeypatch.setattr(durable.time,'sleep',lambda delay:calls.__setitem__('sleep',calls['sleep']+1))
    return calls,durable.live_server.__wrapped__()


def test_crashed_server_reports_exit_and_bounded_stderr(monkeypatch,tmp_path):
    calls,fixture=prepare(monkeypatch,tmp_path,exit_code=1,output=b'x'*9000+b'Address already in use')
    with pytest.raises(AssertionError) as error:next(fixture)
    message=str(error.value)
    assert 'server did not start; child_exit=1' in message
    assert message.endswith('Address already in use') and len(message)<8400
    assert calls['health']==0 and calls['terminate']==1 and calls['wait']==1


@pytest.mark.parametrize('health,reason',[(503,'HTTP 503'),(ConnectionError('synthetic'),'ConnectionError')])
def test_unhealthy_server_keeps_original_retry_bound_and_fails(monkeypatch,tmp_path,health,reason):
    calls,fixture=prepare(monkeypatch,tmp_path,health=health)
    with pytest.raises(AssertionError) as error:next(fixture)
    assert f'child_exit=None; last_health={reason}' in str(error.value)
    assert 'synthetic startup diagnostic' in str(error.value)
    assert calls['health']==100 and calls['sleep']==100
    assert calls['terminate']==2 and calls['wait']==2


def test_healthy_server_yields_and_always_cleans_up(monkeypatch,tmp_path):
    calls,fixture=prepare(monkeypatch,tmp_path,health=200)
    assert next(fixture)=='http://127.0.0.1:43210'
    fixture.close()
    assert calls['health']==1 and calls['sleep']==0
    assert calls['terminate']==1 and calls['wait']==1
