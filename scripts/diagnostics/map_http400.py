from contextlib import contextmanager
from pathlib import Path
import tempfile
import tests.test_v10565_browser as shell
from tests.test_v10586_map_creation_browser import _hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport
original=shell.hosted_browser
@contextmanager
def observed(home):
 with original(home) as (base,page):
  def response(r):
   if r.status>=400:
    print('BAD_RESPONSE',r.status,r.url,'BODY',r.text(),'REQUEST',r.request.post_data,flush=True)
  page.on('response',response)
  yield base,page
shell.hosted_browser=observed
for i in range(20):
 print('ATTEMPT',i,flush=True)
 _hosted_map_creation_reuses_commit_and_preserves_undo_and_viewport(Path(tempfile.mkdtemp(prefix='bm-map-400-')),1400)
