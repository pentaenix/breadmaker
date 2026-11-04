"""Saved-audio navigation regression shared by full and focused browser checks."""

def check_navigation_lifecycle(page, base, project_id, browser_name):
    # Deliver an already-queued media event across the navigation fence.
    # Reads still use the actual audio endpoint; no network errors are filtered.
    page.evaluate("""async()=>{
      window.testNavigationAudioReads=[];const audio=narrationStore.audio;
      narrationStore.audio=function(...args){testNavigationAudioReads.push(args[1]);return audio.apply(this,args)};
      const records=narration.records.filter(record=>record.duration>0);if(records.length<3)throw Error('Navigation fixture requires three saved recordings');
      await playNarration(records[0],records);narration.audio.pause();
    }""")
    queued=page.evaluate('()=>narration.queue.map(record=>record.id)')
    paused=page.evaluate("""()=>{
      dispatchEvent(new Event('beforeunload'));narration.audio.onended();
      return {paused:narration.audio.paused,reads:testNavigationAudioReads.length,queue:narration.queue.map(record=>record.id)};
    }""")
    assert paused=={'paused':True,'reads':1,'queue':queued},paused
    # A cancelled navigation keeps the selected recording/queue usable.
    # Pause on the real play event: one-second fixture clips must not
    # race the browser driver's next inspection by ending naturally.
    page.evaluate('''()=>{window.testNavigationResumed=0;window.testNavigationOnPlay=narration.audio.onplay;
      narration.audio.onplay=()=>{testNavigationOnPlay();narration.audio.pause();testNavigationResumed++}}''')
    page.locator('#narrationPlay').click();page.wait_for_function('()=>testNavigationResumed===1')
    assert page.evaluate('()=>narration.selected.id')==queued[0]
    page.evaluate('()=>narration.audio.onended()')
    page.wait_for_function('(id)=>narration.selected?.id===id&&testNavigationResumed===2',arg=queued[1])
    page.evaluate('()=>{narration.audio.onplay=testNavigationOnPlay;narration.audio.pause()}')
    reads=page.evaluate('()=>testNavigationAudioReads.length')
    if browser_name=='webkit':
        # WebKit keeps the old document executable during provisional
        # navigation; Chromium freezes it until the held response arrives.
        document=page.evaluate_handle('document')
        def deliver_queued_end(route):
            try:
                result=document.evaluate("""d=>d.defaultView.eval(`(()=>{narration.audio.onended();return {paused:narration.audio.paused,reads:testNavigationAudioReads.length}})()`)""")
                assert result=={'paused':True,'reads':reads},result
            finally:route.continue_()
        held=base+'/p/'+project_id+'?narration-navigation-fixture=1'
        page.route(held,deliver_queued_end)
        page.goto(held);page.locator('#app:not([inert])').wait_for(state='visible')
        page.unroute(held,deliver_queued_end)
    else:
        page.reload();page.locator('#app:not([inert])').wait_for(state='visible')
