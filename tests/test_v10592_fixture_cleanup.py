"""Fixture teardown reaps only its own server and preserves the real failure."""
from types import SimpleNamespace
import pytest
from tests import readable_export_browser_fixture as fixture


@pytest.mark.parametrize('browser_close_fails',[True,False])
def test_fixture_always_reaps_server_without_suppressing_failure(tmp_path,monkeypatch,browser_close_fails):
    events=[]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):pass
        def getsockname(self):return ('127.0.0.1',12345)
    class Process:
        def poll(self):return None
        def terminate(self):events.append('terminate')
        def wait(self,**kwargs):events.append('wait')
    response=SimpleNamespace(ok=True,json=lambda:{'project':{'id':'synthetic'}})
    page=SimpleNamespace(request=SimpleNamespace(post=lambda *a,**k:response),
        goto=lambda *a,**k:None,wait_for_function=lambda *a,**k:None,close=lambda:None)
    monkeypatch.setattr(fixture.socket,'socket',lambda *a,**k:Socket())
    monkeypatch.setattr(fixture.requests,'get',lambda *a,**k:response)
    monkeypatch.setattr(fixture.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(fixture,'start_browser',lambda:(object(),SimpleNamespace(new_page=lambda:page),None))
    def close(*args):
        events.append('close')
        if browser_close_fails:raise RuntimeError('Synthetic browser close failure')
    monkeypatch.setattr(fixture,'close_browser',close)
    expected=RuntimeError if browser_close_fails else AssertionError
    with pytest.raises(expected):
        with fixture.browser(tmp_path):
            if not browser_close_fails:raise AssertionError('Synthetic assertion must survive cleanup')
    assert events==['close','terminate','wait']
