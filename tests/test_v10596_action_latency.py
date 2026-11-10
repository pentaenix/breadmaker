"""Coalescing task reads retains mutation and project isolation boundaries."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_task_reads_coalesce_without_reusing_invalidated_or_other_project_data():
    node=shutil.which('node')
    if not node:pytest.skip('Node runtime unavailable')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('static/js/features/tasks.js','utf8');
const code=src.slice(src.indexOf('let taskListRenderEpoch='),src.indexOf('function milestoneQuickEntries'));
const pending=[];let project='A';
const context={state:{taskCache:null},Map,currentProjectIdentity:()=>project,
  apiList:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),assert,pending};
vm.createContext(context);vm.runInContext(code,context);
(async()=>{
  const a=context.getTasks(),b=context.getTasks();
  assert.equal(pending.length,1,'Concurrent reads should share one request');
  pending[0].resolve(['first']);assert.deepEqual(await a,['first']);assert.deepEqual(await b,['first']);
  await context.getTasks();assert.equal(pending.length,1,'Completed reads retain ordinary cache');
  context.invalidateTaskCache();const stale=context.getTasks();context.invalidateTaskCache();const fresh=context.getTasks();
  pending[2].resolve(['fresh']);await fresh;pending[1].resolve(['stale']);await stale;
  assert.deepEqual(context.state.taskCache,['fresh'],'Late pre-mutation result must not replace fresh data');
  context.invalidateTaskCache();const other=context.getTasks();project='B';const next=context.getTasks();
  pending[3].resolve(['A']);await other;assert.equal(context.state.taskCache,null);
  pending[4].resolve(['B']);await next;assert.deepEqual(context.state.taskCache,['B']);
  context.invalidateTaskCache();const obsolete=context.getTasks();const forced=context.getTasks({force:true});
  pending[6].resolve(['forced']);await forced;pending[5].resolve(['obsolete']);await obsolete;
  assert.deepEqual(context.state.taskCache,['forced']);
  context.invalidateTaskCache();const failure=context.getTasks();pending[7].reject(new Error('synthetic outage'));
  await assert.rejects(failure,/synthetic outage/);const retry=context.getTasks();assert.equal(pending.length,9);
  pending[8].resolve(['recovered']);await retry;assert.deepEqual(context.state.taskCache,['recovered']);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node,'-e',script],cwd=ROOT,check=True,timeout=10)
