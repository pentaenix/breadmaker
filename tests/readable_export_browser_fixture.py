"""Small, silent local/hosted export test server with no narration stubs."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import requests
from scripts.browser_runtime import start_browser, close_browser

ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def browser(home,*,local=False):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=dict(os.environ,BREADMAKER_HOME=str(home),PYTHONPATH=str(ROOT/'src'),
        BREADMAKER_RUNTIME='local' if local else 'hosted_dev',BREADMAKER_ENV='test',
        BREADMAKER_AUTH_MODE='none' if local else 'developer',
        BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED='1',BREADMAKER_NARRATION_ENABLED='false')
    with tempfile.TemporaryFile(mode='w+') as log:
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=ROOT,env=env,stdout=log,stderr=log)
        driver=instance=page=None
        try:
            for _ in range(120):
                assert proc.poll() is None,'Export test server exited'
                try:
                    if requests.get(base+'/api/runtime',timeout=.2).ok:break
                except requests.RequestException:pass
                time.sleep(.05)
            else:raise AssertionError('Export test server did not start')
            driver,instance,_=start_browser();page=instance.new_page()
            response=page.request.post(base+'/api/projects',data={'name':'Synthetic readable export'},headers={'Origin':base})
            assert response.ok,response.text();pid=response.json()['project']['id']
            page.goto(base+'/p/'+pid,wait_until='domcontentloaded')
            page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
            yield base,page,pid
        except BaseException:
            log.seek(0);print(log.read()[-2000:]);traceback.print_exc();raise
        finally:
            if driver is not None:
                try:
                    if page is not None:page.close()
                finally:close_browser(driver,instance)
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
