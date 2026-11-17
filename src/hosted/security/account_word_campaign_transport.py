"""Reuse the injected transport/credentials; account bytes before SDK decode."""
from google.api_core.exceptions import GoogleAPICallError
from .account_word_backfill_store import BackfillUnavailable


class _API:
    supports_before_dispatch=True
    def __init__(self,api,budget):self.api,self.budget=api,budget
    def _observe(self,row):
        # Native GAPIC responses have protobuf framing. Minimal non-document
        # synthetic acknowledgments are conservatively reserved as4KiB.
        self.budget.observe_response(row._pb.ByteSize() if hasattr(row,'_pb') else 4096)
    def _stream(self,method,request,kw):
        self.budget.reserve();stream=None
        if kw.get('retry') is not None or not 0<kw.get('timeout',0)<=4:raise BackfillUnavailable('WORD_CAMPAIGN_TRANSPORT_INVALID')
        try:
            stream=getattr(self.api,method)(request=request,**kw)
            for row in stream:self._observe(row);yield row
        except GoogleAPICallError:raise BackfillUnavailable('WORD_CAMPAIGN_TRANSPORT_FAILED') from None
        finally:
            if stream is not None:
                close=getattr(stream,'close',None) or getattr(stream,'cancel',None)
                if close:close()
    def _unary(self,method,request,kw,before_dispatch=None):
        self.budget.reserve()
        if kw.get('retry') is not None or not 0<kw.get('timeout',0)<=4:raise BackfillUnavailable('WORD_CAMPAIGN_TRANSPORT_INVALID')
        if before_dispatch is not None:before_dispatch()
        row=getattr(self.api,method)(request=request,**kw);self._observe(row);return row
    def batch_get_documents(self,*,request,**kw):return self._stream('batch_get_documents',request,kw)
    def run_query(self,*,request,**kw):return self._stream('run_query',request,kw)
    def begin_transaction(self,*,request,**kw):return self._unary('begin_transaction',request,kw)
    def commit(self,*,request,before_dispatch=None,**kw):return self._unary('commit',request,kw,before_dispatch)
    def rollback(self,*,request,**kw):return self._unary('rollback',request,kw)
    def __getattr__(self,name):raise BackfillUnavailable('WORD_CAMPAIGN_FIXED_TRANSPORT_REQUIRED')


def metered_client(client,budget):
    from google.cloud.firestore_v1 import Client
    view=Client(project=client.project,database=client._database,credentials=client._credentials)
    view._firestore_api_internal=_API(client._firestore_api,budget)
    if view._credentials is not client._credentials or view._database_string!=client._database_string:raise BackfillUnavailable('WORD_CAMPAIGN_TRANSPORT_INVALID')
    return view
