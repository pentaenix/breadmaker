// Manuscript edit-history snapshots consumed by core/history.js.

function recordUndo(chapterIds,force=false){if(state.viewMode==='scene'&&state.scene){const snapshot=localSceneSnapshot();if(!snapshot)return;const key=`scene:${snapshot.id}`,time=Date.now();if(!force&&state.undoGroup.key===key&&time-state.undoGroup.at<850){state.undoGroup.at=time;return}historyPush('undo',{kind:'scene_edit',scene:snapshot});historyClear('redo');state.undoGroup={key,at:time};return}chapterIds=[...new Set((chapterIds||[]).filter(Boolean).map(entityId))].sort(compareEntityIds);if(!chapterIds.length)return;const key=chapterIds.join(','),time=Date.now();if(!force&&state.undoGroup.key===key&&time-state.undoGroup.at<850){state.undoGroup.at=time;return}const chapters=chapterIds.map(localChapterSnapshot).filter(Boolean);if(!chapters.length)return;historyPush('undo',{kind:'manuscript',chapters});historyClear('redo');state.undoGroup={key,at:time};}
async function currentSnapshotForIds(ids){const chapters=[];for(const id of ids){const local=localChapterSnapshot(id);if(local){chapters.push(local);continue}const data=await api(`/api/chapters/${id}`);chapters.push({id,title:data.chapter.title,blocks:data.blocks.map(b=>({id:b.id,block_type:b.block_type,semantic_style:b.semantic_style,content_html:b.content_html,text_content:b.text_content,image_path:b.image_path,image_width:b.image_width||100,image_footer:b.image_footer||'',image_description:b.image_description||'',image_row_id:b.image_row_id||'',scene_id:b.scene_id}))})}return{kind:'manuscript',chapters};}
async function currentSceneEditSnapshot(sceneId){
  if(state.viewMode==='scene'&&entityId(state.scene?.id)===entityId(sceneId)){const local=localSceneSnapshot();return{kind:'scene_edit',scene:local}}
  const data=await api(`/api/scenes/${sceneId}/manuscript`);return{kind:'scene_edit',scene:{id:sceneId,title:data.scene.title,blocks:data.blocks.map(b=>({id:b.id,chapter_id:b.chapter_id,block_type:b.block_type,semantic_style:b.semantic_style,content_html:b.content_html,text_content:b.text_content,image_path:b.image_path,image_width:b.image_width||100,image_footer:b.image_footer||'',image_description:b.image_description||'',image_row_id:b.image_row_id||'',scene_id:b.scene_id}))}}
}
async function restoreSceneEditSnapshot(snapshot){
  const chapterIds=[...new Set((snapshot.scene.blocks||[]).map(block=>entityId(block.chapter_id)).filter(Boolean))];
  const expected_revisions=Object.fromEntries(chapterIds.map(id=>[String(id),state.chapterRevisions.get(id)||'']));
  const result=await api(`/api/scenes/${snapshot.scene.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:snapshot.scene.blocks,expected_revisions})});
  for(const [cid,revision] of Object.entries(result.chapter_revisions||{}))state.chapterRevisions.set(entityId(cid),revision);
  resetDirtyState();setSaveState('Saved');await refresh(false);await selectScene(snapshot.scene.id,false)
}
async function restoreEditSnapshot(snapshot){
  for(const ch of snapshot.chapters){
    const known=visibleChapterById(ch.id);let revision=state.chapterRevisions.get(entityId(ch.id))||known?.revision||'';
    if(known&&known.title!==ch.title){const titled=await api(`/api/chapters/${ch.id}/title`,{method:'PATCH',body:JSON.stringify({title:ch.title,expected_revision:revision})});revision=titled.revision||revision;state.chapterRevisions.set(entityId(ch.id),revision);known.title=ch.title}
    const saved=await api(`/api/chapters/${ch.id}/blocks`,{method:'PUT',body:JSON.stringify({blocks:ch.blocks,expected_revision:revision})});revision=saved.revision||revision;state.chapterRevisions.set(entityId(ch.id),revision);if(known)known.revision=revision;await clearCrashDraft(entityId(ch.id))
  }
  resetDirtyState();setSaveState('Saved');await refresh(false);await reloadManuscriptView();
}
