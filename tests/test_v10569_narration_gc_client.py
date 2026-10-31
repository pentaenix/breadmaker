"""No credential/data clients or cloud calls: fixed metadata token + HTTPS job."""
import json

import pytest

from hosted.narration.gc_client import invoke, MAX_BATCHES
from hosted.narration.maintenance_composition import DAILY_BATCHES
from hosted.narration.maintenance_identity import PATH

ORIGIN='https://app.breadmaker.studio'
RESULT=dict(ok=True,collected=0,held=0,tracked=0,fences=0,orphans=0,partial=False)


class Reply:
    def __init__(self, payload, *, status=200, headers=None):
        self.payload,self.status_code,self.headers=payload,status,headers or {}
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def iter_content(self,**_): yield self.payload


class Session:
    def __init__(self, results): self.results,self.calls=iter(results),[]
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def get(self,url,**kwargs):
        self.calls.append(('get',url,kwargs))
        return Reply(b'header.claims.signature',headers={'Metadata-Flavor':'Google'})
    def post(self,url,**kwargs):
        self.calls.append(('post',url,kwargs)); return next(self.results)


def response(**changes): return Reply(json.dumps({**RESULT,**changes}).encode())


def test_job_mints_exact_operation_audience_no_browser_state_and_no_automatic_retry():
    session=Session([response()])
    result=invoke(ORIGIN,session_factory=lambda:session)
    assert result['ok'] and result['batches']==1 and MAX_BATCHES==DAILY_BATCHES
    get,post=session.calls
    assert get[2]['params']=={'audience':ORIGIN+PATH,'format':'full'}
    assert post[1]==ORIGIN+PATH and post[2]['data']==b''
    assert post[2]['headers']=={'Authorization':'Bearer header.claims.signature'}
    assert all(not call[2]['allow_redirects'] and call[2]['stream'] for call in session.calls)
    failed=Session([Reply(b'private exception',status=503),response()])
    with pytest.raises(ValueError,match='FAILED_RESUMABLE'):
        invoke(ORIGIN,session_factory=lambda:failed)
    assert len(failed.calls)==2


def test_job_continues_only_partial_batches_and_stops_on_unknown_objects():
    session=Session([response(partial=True),response(collected=1)])
    result=invoke(ORIGIN,session_factory=lambda:session)
    assert result['batches']==2 and result['collected']==1 and result['ok']
    session=Session([response(ok=False,orphans=1),response()])
    result=invoke(ORIGIN,session_factory=lambda:session)
    assert not result['ok'] and result['orphans']==1 and len(session.calls)==2


def test_job_work_is_bounded_even_when_server_reports_more_backlog():
    session=Session([response(partial=True)]*MAX_BATCHES)
    result=invoke(ORIGIN,session_factory=lambda:session)
    assert result['partial'] and not result['ok'] and result['batches']==MAX_BATCHES


@pytest.mark.parametrize('reply',[Reply(b'x'*8193),Reply(b'{}'),response(ok=True,orphans=1),
    response(collected=True),Reply(b'private',status=302)])
def test_oversize_corrupt_redirected_and_inconsistent_responses_fail(reply):
    with pytest.raises(ValueError):
        invoke(ORIGIN,session_factory=lambda:Session([reply]))


def test_job_rejects_urls_with_paths_credentials_queries_or_plaintext():
    for url in ('http://app.breadmaker.studio','https://user:password@app.breadmaker.studio',
            ORIGIN+'/private',ORIGIN+'?project_id=target'):
        with pytest.raises(ValueError): invoke(url)
