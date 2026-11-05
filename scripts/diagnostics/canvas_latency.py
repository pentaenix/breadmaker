"""Synthetic hosted timings; reports real fixture HTTP with explicit added latency."""
import argparse,json,statistics,tempfile
from pathlib import Path
from tests.test_v10569_narration_browser import browser
from tests.test_v10581_canvas_navigation_browser import _board

parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
results=[]
with browser(Path(tempfile.mkdtemp(prefix='canvas-latency-')),local=False) as (_,page,_):
    ids,errors=_board(page)
    page.evaluate('''(id)=>{
      const original=api;window.canvasMetricCalls=[];window.canvasMetricDelay=0;
      api=async(path,options={})=>{
        const relevant=path===`/api/canvases/${id}`||path===`/api/ui-state/canvas-viewport-${id}`;
        const call={path,start:performance.now()};if(relevant){canvasMetricCalls.push(call);await new Promise(resolve=>setTimeout(resolve,canvasMetricDelay))}
        try{return await original(path,options)}finally{if(relevant)call.end=performance.now()}
      };
    }''',ids['cv'])
    for delay in (0,200):
        cold=[];warm=[];traces=[]
        for index in range(5):
            for kind in ('cold','warm'):
                page.evaluate('''({id,kind,delay})=>{
                  setView('manuscript');if(kind==='cold')state.currentCanvas=null;state.lastCanvasId=id;
                  window.canvasMetricCalls=[];window.canvasMetricDelay=delay;window.canvasMetricEnd=0;
                  const view=document.querySelector('#canvasView'),button=document.querySelector('.main-tab[data-view="canvas"]');
                  button.addEventListener('click',()=>window.canvasMetricStart=performance.now(),{once:true,capture:true});
                  const observer=new MutationObserver(()=>{if(view.classList.contains('active')){observer.disconnect();requestAnimationFrame(()=>window.canvasMetricEnd=performance.now())}});
                  observer.observe(view,{attributes:true,attributeFilter:['class']});
                }''',{'id':ids['cv'],'kind':kind,'delay':delay})
                page.locator('.main-tab[data-view="canvas"]').click()
                page.wait_for_function('()=>canvasMetricEnd>0')
                record=page.evaluate('()=>({ms:canvasMetricEnd-canvasMetricStart,calls:canvasMetricCalls})')
                (cold if kind=='cold' else warm).append(record['ms'])
                if kind=='cold':traces.append(record['calls'])
        results.append({'added_delay_per_read_ms':delay,'samples':5,'cold_ms':cold,'warm_ms':warm,'cold_median_ms':statistics.median(cold),'warm_median_ms':statistics.median(warm),'cold_read_traces':traces})
    assert not errors,errors
Path(args.output).write_text(json.dumps({'environment':'Linux Chromium, synthetic hosted persistence; not production latency','measurements':results},indent=2)+'\n')
print(json.dumps(results,indent=2))
