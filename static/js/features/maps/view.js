// Map view typography and ruler-scale presentation preferences.

const MAP_RULER_SCALE_KEY='breadmaker-map-ruler-scale';
const MAP_RULER_SCALE_DEFAULT=1, MAP_RULER_SCALE_MIN=.6, MAP_RULER_SCALE_MAX=1.8;
function normalizeMapRulerScale(value){const scale=Number(value);if(!Number.isFinite(scale))return MAP_RULER_SCALE_DEFAULT;return Math.max(MAP_RULER_SCALE_MIN,Math.min(MAP_RULER_SCALE_MAX,Math.round(scale*20)/20))}
function formatMapRulerScale(value){return `${Math.round(normalizeMapRulerScale(value)*100)}%`}
function mapStationRenderScale(rulerScale=state.mapRulerScale){const scale=normalizeMapRulerScale(rulerScale);if(scale>=1)return 1;const t=(scale-MAP_RULER_SCALE_MIN)/(1-MAP_RULER_SCALE_MIN);return .8+.2*Math.max(0,Math.min(1,t))}
function mapStationSpacingCompression(rulerScale=state.mapRulerScale){const scale=normalizeMapRulerScale(rulerScale);if(scale>=1)return 1;const t=(scale-MAP_RULER_SCALE_MIN)/(1-MAP_RULER_SCALE_MIN);return .82+.18*Math.max(0,Math.min(1,t))}
function mapStationOuterRadius(){return MAP_STOP_R*mapStationRenderScale()}
function mapStationInnerRadius(){return MAP_STOP_INNER_R*mapStationRenderScale()}
function applyMapRulerScale(value,{persist=true,render=true}={}){const scale=normalizeMapRulerScale(value);state.mapRulerScale=scale;const slider=$('#mapRulerScale'),output=$('#mapRulerScaleValue');if(slider)slider.value=String(Math.round(scale*100));if(output)output.textContent=formatMapRulerScale(scale);if(persist){try{localStorage.setItem(MAP_RULER_SCALE_KEY,String(scale))}catch{}}if(render&&state.mapData)renderMapVisual();return scale}
function restoreMapRulerScale(){let stored=MAP_RULER_SCALE_DEFAULT;try{stored=localStorage.getItem(MAP_RULER_SCALE_KEY)??stored}catch{}applyMapRulerScale(stored,{persist:false,render:false})}
const MAP_TEXT_SIZE_KEY='breadmaker-map-text-size';
const MAP_TEXT_SIZE_DEFAULT=16;
function normalizeMapTextSize(value){const size=Number(value);if(!Number.isFinite(size))return MAP_TEXT_SIZE_DEFAULT;return Math.max(MANUSCRIPT_TEXT_SIZE_MIN,Math.min(MANUSCRIPT_TEXT_SIZE_MAX,Math.round(size*2)/2))}
function formatMapTextSize(value){const size=normalizeMapTextSize(value);return `${Number.isInteger(size)?size:size.toFixed(1)} px`}
function applyMapTextSize(value,{persist=true,render=true}={}){const size=normalizeMapTextSize(value);state.mapFontSize=size;document.documentElement.style.setProperty('--map-label-font-size',`${size}px`);const slider=$('#mapTextSize'),output=$('#mapTextSizeValue');if(slider)slider.value=String(size);if(output)output.textContent=formatMapTextSize(size);mapLabelMeasureContext=null;if(persist){try{localStorage.setItem(MAP_TEXT_SIZE_KEY,String(size))}catch{}}if(render&&state.mapData)renderMapVisual();return size}
function restoreMapTextSize(){let stored=MAP_TEXT_SIZE_DEFAULT;try{stored=localStorage.getItem(MAP_TEXT_SIZE_KEY)??stored}catch{}applyMapTextSize(stored,{persist:false,render:false})}
const MAP_FONT_KEY='breadmaker-map-font';
function applyMapFont(value,{persist=true,render=true}={}){const key=Object.hasOwn(MANUSCRIPT_FONTS,String(value||''))?String(value):'arial';state.mapFont=key;document.documentElement.style.setProperty('--map-font-family',MANUSCRIPT_FONTS[key].css);const select=$('#mapFont');if(select)select.value=key;mapLabelMeasureContext=null;if(persist){try{localStorage.setItem(MAP_FONT_KEY,key)}catch{}}if(render&&state.mapData)renderMapVisual();return key}
function restoreMapFont(){let stored='arial';try{stored=localStorage.getItem(MAP_FONT_KEY)||stored}catch{}applyMapFont(stored,{persist:false,render:false})}
