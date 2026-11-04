"""Manual native-WebKit diagnostic; synthetic WAV and read-only controls only."""
import io,wave,math,struct,json,threading,os
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright
raw=io.BytesIO()
with wave.open(raw,'wb') as f:
 f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*2*math.pi*220/24000))) for i in range(24000)))
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/wave.wav':body=raw.getvalue();mime='audio/wav'
  elif self.path=='/pitch.js':body=(Path(__file__).resolve().parents[2]/'static/js/features/manuscript/narration-pitch-worklet.js').read_bytes();mime='application/javascript'
  else:body=b'<button id="play">Play</button>';mime='text/html'
  self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with sync_playwright() as pw:
  browser=pw.webkit.launch(headless=True)
  for mode in ['plain','context','pitch']:
   page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:'+str(server.server_port))
   page.evaluate("""mode=>{window.events=[];play.onclick=async()=>{try{
    window.audio=new Audio(URL.createObjectURL(await(await fetch('/wave.wav')).blob()));audio.loop=true;for(const e of ['play','playing','pause','ended','error'])audio.addEventListener(e,()=>events.push({event:e,time:audio.currentTime,paused:audio.paused}));
    if(mode!=='plain'){window.context=new AudioContext();window.source=context.createMediaElementSource(audio);
     window.analyser=context.createAnalyser();analyser.fftSize=2048;analyser.connect(context.destination);window.peak=0;const collect=()=>{const samples=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(samples);for(const v of samples)peak=Math.max(peak,Math.abs(v));requestAnimationFrame(collect)};collect();
     if(mode==='pitch'){await context.audioWorklet.addModule('/pitch.js');window.node=new AudioWorkletNode(context,'breadmaker-narration-pitch',{outputChannelCount:[1]});node.parameters.get('semitones').value=2;source.connect(node);node.connect(analyser)}else source.connect(analyser);
     await context.resume()}
    await audio.play();window.ready=true;
   }catch(e){window.failed=String(e)}}}""",mode)
   page.locator('#play').click()
   try:page.wait_for_function('()=>window.failed||(window.audio?.currentTime>.1&&(!window.context||window.peak>.0001))',timeout=8000)
   except Exception:pass
   result=page.evaluate('()=>({time:audio.currentTime,paused:audio.paused,readyState:audio.readyState,context:window.context?.state,contextTime:window.context?.currentTime,failed:window.failed,peak:window.peak,events})');result.update(mode=mode,errors=errors);print(json.dumps(result),flush=True)
   page.evaluate('()=>{audio.pause();window.context?.close()}');page.close()
  browser.close()
finally:server.shutdown();server.server_close()
