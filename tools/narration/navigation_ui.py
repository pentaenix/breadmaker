"""Saved-audio navigation regression shared by full and focused browser checks."""

def _check_navigation_lifecycle(page, base, project_id, browser_name):
    # Deliver an already-queued media event across the navigation fence.
    # Reads still use the actual audio endpoint; no network errors are filtered.
    page.evaluate("""async()=>{
      window.testNavigationAudioReads=[];const audio=narrationStore.audio;
      // Keep short cached fixtures alive through cold pitch-worklet startup.
      // This test explicitly delivers the ended event at the intended boundary.
      window.testNavigationPreviousLoop=narration.audio.native.loop;narration.audio.native.loop=true;
      narrationStore.audio=function(...args){testNavigationAudioReads.push(args[1]);return audio.apply(this,args)};
      const records=narration.records.filter(record=>record.duration>=3);if(records.length<3)throw Error('Navigation fixture requires three saved recordings');
      // Stage a paused saved recording without briefly starting a short clip.
      // Play-then-pause can already reach ended at the fixture's fast speed.
      const previous=narration.early;narration.early={autoplay:false};
      try{await playNarration(records[0],records,true)}finally{narration.early=previous}
      if(!narration.audio.paused||narration.audio.ended)throw Error('Navigation fixture must begin paused before playback');
    }""")
    queued=page.evaluate('()=>narration.queue.map(record=>record.id)')
    paused=page.evaluate("""()=>{
      dispatchEvent(new Event('beforeunload'));narration.audio.onended();
      return {paused:narration.audio.paused,reads:testNavigationAudioReads.length,queue:narration.queue.map(record=>record.id)};
    }""")
    assert paused=={'paused':True,'reads':1,'queue':queued},paused
    # A cancelled navigation keeps the selected recording/queue usable.
    # Native Linux WebKit can report a zero media-element clock while real PCM
    # renders. Observe the actual non-silent cache audio, not event cardinality.
    page.wait_for_function('()=>narration.audio.pitch===2&&!!narration.audio.nativePitch')
    page.evaluate("""()=>{
      if(!narration.audio.nativePitch)throw Error('Navigation fixture requires the real pitch graph');
      const context=narration.audio.nativeContext;
      window.testNavigationAnalyser=context.createAnalyser();testNavigationAnalyser.fftSize=2048;
      window.testNavigationSilent=context.createGain();testNavigationSilent.gain.value=0;
      narration.audio.nativePitch.connect(testNavigationAnalyser);testNavigationAnalyser.connect(testNavigationSilent);testNavigationSilent.connect(context.destination);
      window.testNavigationPeak=()=>{const data=new Float32Array(testNavigationAnalyser.fftSize);testNavigationAnalyser.getFloatTimeDomainData(data);return Math.max(...data.map(Math.abs))};
    }""")
    def pause_after_pcm(record_id):
        page.wait_for_function("""id=>{
          if(narration.selected?.id!==id||narration.audio.paused||testNavigationPeak()<.001)return false;
          narration.audio.pause();return true;
        }""",arg=record_id)
    page.locator('#narrationPlay').click();pause_after_pcm(queued[0])
    assert page.evaluate('()=>narration.selected.id')==queued[0]
    assert page.evaluate('()=>testNavigationAudioReads')==queued[:1]
    page.wait_for_function('()=>testNavigationPeak()<.000001')
    page.evaluate('()=>narration.audio.onended()');pause_after_pcm(queued[1])
    assert page.evaluate('()=>testNavigationAudioReads')==queued[:2]
    page.evaluate('()=>{narration.audio.pause();narration.audio.native.loop=testNavigationPreviousLoop;narration.audio.nativePitch.disconnect(testNavigationAnalyser);testNavigationAnalyser.disconnect();testNavigationSilent.disconnect()}')
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


def check_navigation_lifecycle(page, base, project_id, browser_name):
    try:
        return _check_navigation_lifecycle(page, base, project_id, browser_name)
    except Exception:
        try:
            state=page.evaluate('()=>({selected:narration.selected?.id,queue:narration.queue.map(r=>r.id),time:narration.audio.native.currentTime,pcm:window.testNavigationPeak?.(),stream:!!narration.audio.stream,reads:window.testNavigationAudioReads,paused:narration.audio.paused,nativePaused:narration.audio.native.paused,ended:narration.audio.ended,context:narration.audio.nativeContext?.state,suspended:narration.playbackSuspended,token:narration.token})')
        except Exception as diagnostic_error:
            state={'unavailable':type(diagnostic_error).__name__}
        print('NAVIGATION_FAILURE_STATE',state,flush=True)
        raise
