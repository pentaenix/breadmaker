"""Bounded synthetic WebKit navigation diagnostic; no production data or audio."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import tests.test_v10579_phone_workspace_browser as phone
original=phone.browser
@contextmanager
def observed(*args,**kwargs):
    with original(*args,**kwargs) as (base,page,pid):
        page.on('requestfailed',lambda r:print('FAILED_REQUEST',r.method,r.url,r.failure,flush=True))
        page.on('console',lambda m:print('CONSOLE_ERROR',m.text,flush=True) if m.type=='error' else None)
        page.on('framenavigated',lambda f:print('NAVIGATED',f.url,flush=True) if f==page.main_frame else None)
        yield base,page,pid
phone.browser=observed
from tests.test_v10588_targets_navigation_browser import test_targets_pagehide_keeps_journal_and_recovers_after_reopen
test_targets_pagehide_keeps_journal_and_recovers_after_reopen(Path(tempfile.mkdtemp(prefix='bm-journal-navigation-')))
for attempt in range(20):
    print('ATTEMPT',attempt,flush=True)
    phone._exercise_webkit_phone_viewport_menus_and_bottom_sheets(Path(tempfile.mkdtemp(prefix='bm-phone-navigation-')),(360,740))
