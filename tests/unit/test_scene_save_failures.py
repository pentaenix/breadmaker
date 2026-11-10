"""Run the real save coordinator against exact unsupported/lease error contracts."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_failure(*, message, status, code='', hosted=True, view='scene', navigate=False):
    source = (ROOT / 'static/js/features/manuscript/persistence.js').read_text()
    function = 'async function flushSave(){' + source.split('async function flushSave(){', 1)[1].split('\n\nfunction showManuscriptConflict', 1)[0]
    options = json.dumps(dict(message=message, status=status, code=code, hosted=hosted, view=view, navigate=navigate))
    script = '''
const options=OPTIONS;
const state={dirty:true,saveInFlight:null,dirtyChapters:new Set(['chapter_1']),chapterDirtyGeneration:new Map([['chapter_1',7]]),chapterSavedGeneration:new Map(),editGeneration:7,viewMode:options.view,scene:{id:'scene_1'},chapter:{id:'chapter_1'},saveTimer:99};
const draft={text:'Unsaved synthetic manuscript'};
const result={lease:0,conflict:0,retries:[],cleared:[],statuses:[],toasts:[],editable:true};
const entityId=value=>value;
const runtimeSupports=()=>options.hosted;
const clearTimeout=id=>result.cleared.push(id);
const setTimeout=(callback,delay)=>{result.retries.push(delay);return 100};
const setSaveState=status=>result.statuses.push(status);
const toast=message=>result.toasts.push(message);
const showLeaseLost=()=>{result.lease++;result.editable=false};
const showManuscriptConflict=()=>result.conflict++;
const saveSceneView=async()=>{if(options.navigate)state.viewMode='chapter';throw Object.assign(Error(options.message),{status:options.status,code:options.code})};
const saveOneChapter=saveSceneView;
const renderChapterTree=()=>{};
const kickHostedReadCache=()=>{};
FUNCTION
(async()=>{let rejected=false;try{await flushSave()}catch(error){rejected=error.message===options.message}
console.log(JSON.stringify({...result,rejected,dirty:state.dirty,pending:[...state.dirtyChapters],generation:state.chapterDirtyGeneration.get('chapter_1'),saved:state.chapterSavedGeneration.size,inFlight:state.saveInFlight,draft}));})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('OPTIONS', options).replace('FUNCTION', function)
    completed = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=2)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('message,status,code,navigate', [
    ('HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE', 409, '', False),
    ('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE', 400, '', False),
    ('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE', 409, '', False),
    ('Unsupported structure', 400, 'HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE', False),
    ('HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE', 409, '', True),
])
def test_unsupported_scene_save_preserves_draft_and_editing_without_retry(message, status, code, navigate):
    result = run_failure(message=message, status=status, code=code, navigate=navigate)
    assert result['rejected'] and result['dirty'] and result['inFlight'] is None
    assert result['pending'] == ['chapter_1'] and result['generation'] == 7 and result['saved'] == 0
    assert result['draft']['text'] == 'Unsaved synthetic manuscript'
    assert result['lease'] == result['conflict'] == 0 and result['editable']
    assert result['retries'] == [] and result['cleared'] == [99]
    assert result['statuses'] == ['Save failed']
    assert result['toasts'] == ['Save failed. Your text remains in this window.']


@pytest.mark.parametrize('message,status,code,hosted,view,lease,conflict,retries', [
    ('LEASE_LOST', 409, '', True, 'scene', 1, 0, []),
    ('LEASE_LOST', 409, '', False, 'scene', 1, 0, []),
    ('HOSTED_GENERATION_CONFLICT', 409, '', True, 'scene', 0, 1, []),
    ('Local conflict', 409, 'MANUSCRIPT_CONFLICT_001', False, 'scene', 0, 1, []),
    ('HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE', 409, '', False, 'scene', 1, 0, []),
    ('HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE', 409, '', True, 'chapter', 1, 0, []),
    ('Synthetic outage', 503, '', True, 'scene', 0, 0, [1500]),
    ('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE', 503, '', True, 'scene', 0, 0, [1500]),
])
def test_existing_conflict_lease_and_transient_retry_behaviors_are_unchanged(message, status, code, hosted, view, lease, conflict, retries):
    result = run_failure(message=message, status=status, code=code, hosted=hosted, view=view)
    assert result['lease'] == lease and result['conflict'] == conflict and result['retries'] == retries
    assert result['dirty'] and result['pending'] == ['chapter_1'] and result['rejected']
    assert result['draft']['text'] == 'Unsaved synthetic manuscript'
