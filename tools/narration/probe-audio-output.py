"""Diagnostic-only headless Firefox output check using a trusted click."""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser=pw.firefox.launch(headless=True)
    page=browser.new_page()
    page.set_content('<button id="resume">Resume output</button>')
    page.evaluate("""()=>{
      window.probe={state:'not-created',settled:false};
      document.querySelector('#resume').onclick=()=>{
        window.probe.active=navigator.userActivation?.isActive;
        window.output=new AudioContext();
        window.probe.state=output.state;
        output.resume().then(()=>{probe.state=output.state;probe.settled=true},error=>{probe.error=String(error);probe.settled=true});
      };
    }""")
    page.locator('#resume').click()
    try:page.wait_for_function('()=>probe.settled',timeout=2000)
    except Exception:pass
    print('FIREFOX_AUDIO_OUTPUT',json.dumps(page.evaluate('()=>({...probe,currentState:output.state})')),flush=True)
    browser.close()
