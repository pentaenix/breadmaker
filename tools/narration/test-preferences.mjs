import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
let pid='one',fail=false;const stored=new Map(),writes=[];
const context={currentProjectIdentity:()=>pid,async api(path,options){const project=options.headers['X-Breadmaker-Project'];if(options.method==='PUT'){if(fail)throw Error('offline');const value=JSON.parse(options.body).value;stored.set(project,value);writes.push(project);return {ok:true}}return {value:stored.get(project)}},applyNarrationPreferences(value){vm.runInContext('narrationPreferences.value=next',Object.assign(context,{next:value}))}};
vm.createContext(context);vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-voices.js',import.meta.url),'utf8'),context);vm.runInContext(await readFile(new URL('../../static/js/features/manuscript/narration-preferences.js',import.meta.url),'utf8'),context);
const run=source=>vm.runInContext(source,context);
assert.equal(run('normalizeNarrationPreferences({gpu:"false",speed:100,voice:"bad"}).gpu'),true);
assert.equal(run('normalizeNarrationPreferences({speed:1.05}).speed'),1.05);
assert.equal(run('normalizeNarrationPreferences({speed:1.75}).speed'),1.75);
assert.equal(run('normalizeNarrationPreferences({speed:2}).speed'),2);
assert.equal(run('normalizeNarrationPreferences({speed:2.5}).speed'),1);
assert.equal(run('normalizeNarrationPreferences({voice:"af_nicole"}).voice'),'af_nicole');
assert(run('normalizeNarrationPreferences({voice:"af_nicole",visibleVoices:["af_heart","bad","af_heart"]}).visibleVoices.includes("af_nicole")'));
assert.equal(run('normalizeNarrationPreferences({visibleVoices:[]}).visibleVoices.length'),20);
assert.equal(run('normalizeNarrationPreferences({showAllLanguages:"true"}).showAllLanguages'),false);
assert.equal(run('normalizeNarrationPreferences({showAllLanguages:true}).showAllLanguages'),true);
assert.equal(run('normalizeNarrationPreferences({voice:"if_sara",voicesByLanguage:{"en-us":"bf_emma",es:"bad"}}).voicesByLanguage["en-us"]'),'bf_emma');
const voiceCatalog=JSON.parse(await readFile(new URL('../../static/narration-voices.json',import.meta.url),'utf8'));
assert.deepEqual(Array.from(run('NARRATION_VOICES')).sort(),voiceCatalog.voices.map(voice=>voice.id).sort());
for(const voice of voiceCatalog.voices){assert.equal(voice.size,522240);assert.match(voice.sha256,/^[0-9a-f]{64}$/);assert(['en-us','es','fr','it','ja','pt-BR','zh-CN'].includes(voice.language))}
assert.equal(voiceCatalog.voices.filter(voice=>voice.language==='en-us'&&voice.default).length,4);
context.i18nCatalog={code:'en'};context.testCatalog=voiceCatalog;
run('narrationVoiceCatalog.value=testCatalog');
assert.equal(run('narrationLanguageVoices(testCatalog.voices).length'),28);
assert.equal(run('narrationEffectiveVoice()'),'af_heart');
context.i18nCatalog.code='es';assert.equal(run('narrationLanguageVoices(testCatalog.voices).length'),3);assert.equal(run('narrationEffectiveVoice()'),'ef_dora');
context.i18nCatalog.code='fr';assert.equal(run('narrationLanguageVoices(testCatalog.voices).length'),1);assert.equal(run('narrationEffectiveVoice()'),'ff_siwis');
assert.equal(run('narrationLanguageVoices(testCatalog.voices,true).length'),44);
context.i18nCatalog.code='en';
const html=await readFile(new URL('../../static/index.html',import.meta.url),'utf8');
const speedOptions=[...html.match(/<select id="narrationSpeed"[^>]*>(.*?)<\/select>/s)[1].matchAll(/<option value="([\d.]+)"/g)].map(match=>Number(match[1]));
assert.deepEqual(speedOptions,Array.from(run('NARRATION_SPEEDS')),'Panel and settings speed choices must agree');
assert.equal(run('normalizeNarrationPreferences({punctuationPauses:false}).punctuationPauses'),false);
assert.equal(run('normalizeNarrationPreferences({punctuationPauses:"false"}).punctuationPauses'),true);
assert.equal(run('normalizeNarrationPreferences({generationPace:.95,pitch:-2}).generationPace'),.95);
assert.equal(run('normalizeNarrationPreferences({generationPace:.95,pitch:-2}).pitch'),-2);
assert.equal(run('normalizeNarrationPreferences({generationPace:NaN,pitch:Infinity}).generationPace'),1);
assert.equal(run('normalizeNarrationPreferences({generationPace:".95",pitch:99}).pitch'),0);
await run('loadNarrationPreferences()');
await Promise.all([run('saveNarrationPreferences({gpu:false})'),run('saveNarrationPreferences({speed:1.05,autoplay:false})')]);
assert.equal(stored.get('one').gpu,false);assert.equal(stored.get('one').speed,1.05);assert.equal(stored.get('one').autoplay,false);
pid='two';await run('loadNarrationPreferences()');assert.equal(run('narrationPreferences.value.gpu'),true);
pid='one';await run('loadNarrationPreferences()');assert.equal(run('narrationPreferences.value.gpu'),false);
fail=true;await assert.rejects(()=>run('saveNarrationPreferences({gpu:true})'),/offline/);assert.equal(run('narrationPreferences.value.gpu'),false);
fail=false;await run('saveNarrationPreferences({word:false})');assert.equal(stored.get('one').word,false);
console.log('PASS: validated defaults, project scoping/reload, serialized preference merges, failed-write preservation and retry.');
const files=['../../static/js/features/projects/settings.js','../../static/js/features/projects/narration_voices.js','../../static/js/features/manuscript/narration-voices.js','../../static/js/features/manuscript/narration.js','../../static/index.html'];
const keys=new Set();for(const file of files){const source=await readFile(new URL(file,import.meta.url),'utf8');for(const match of source.matchAll(/(?:['"]|i18n:)(narration\.[a-z_]+)/g))keys.add(match[1])}
keys.delete('narration.wav'); // Upload filename, not a UI string.
keys.delete('narration.accent_');keys.delete('narration.gender_');keys.delete('narration.language_');
// Validate the actual dynamically constructed markers, not just static keys.
context.escapeHtml=value=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
for(const match of run('narrationVoiceOptions(testCatalog.voices)').matchAll(/\{\{i18n:([^}]+)\}\}/g))keys.add(match[1]);
for(const voice of voiceCatalog.voices){keys.add('narration.accent_'+voice.accent);keys.add('narration.gender_'+voice.gender)}
const english=JSON.parse(await readFile(new URL('../../locales/en.json',import.meta.url),'utf8')).strings;
const narrationKeys=Object.keys(english).filter(key=>key.startsWith('narration.')).sort();
const placeholders=value=>[...value.matchAll(/\{([a-z_]+)\}/g)].map(match=>match[1]).sort();
for(const locale of ['en','es','fr','it','ja','pt-BR','zh-CN']){
  const source=await readFile(new URL(`../../locales/${locale}.json`,import.meta.url),'utf8'),catalog=JSON.parse(source);
  for(const key of keys)assert(catalog.strings[key],`${locale}: ${key}`);
  assert.deepEqual(Object.keys(catalog.strings).filter(key=>key.startsWith('narration.')).sort(),narrationKeys,`${locale}: full narration key parity`);
  assert.equal([...source.matchAll(/"narration\.[A-Za-z_]+"\s*:/g)].length,narrationKeys.length,`${locale}: no duplicate narration keys`);
  for(const key of narrationKeys){assert(!/\{\{i18n:|UI preview|placeholder/i.test(catalog.strings[key])&&!/\bTODO\b/.test(catalog.strings[key]),`${locale}: unfinished copy ${key}`);assert.deepEqual(placeholders(catalog.strings[key]),placeholders(english[key]),`${locale}: ${key} interpolation`)}
}
console.log('PASS: All narration labels, complete key sets, interpolation tokens and duplicate/unfinished-copy checks across seven locales.');
