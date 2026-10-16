from __future__ import annotations

"""Task milestone routes."""

from fastapi import APIRouter, HTTPException
from api.tasks.contracts import MilestoneCreate, MilestoneUpdate
from api.shared_contracts import Reorder
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values

router=APIRouter()

def _task_for_milestone_write(con,pid:int,tid:int):
    task=one(con,"SELECT * FROM tasks WHERE id=? AND project_id=?",(tid,pid))
    if not task: raise HTTPException(404,"TASK_006: Task not found")
    if task['status']=='archived': raise HTTPException(409,"TASK_007: Archived tasks are read-only until unarchived")
    return task

@router.get("/api/tasks/{tid}/milestones")
def list_task_milestones(tid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not one(con,"SELECT id FROM tasks WHERE id=? AND project_id=?",(tid,p['id'])): raise HTTPException(404,"TASK_006: Task not found")
        return allrows(con,"""SELECT tm.*,
            (SELECT COUNT(*) FROM references_map r WHERE r.project_id=tm.project_id AND r.source_type='milestone' AND r.source_id=CAST(tm.id AS TEXT)) link_count
            FROM task_milestones tm WHERE tm.task_id=? AND tm.project_id=? ORDER BY tm.position,tm.id""",(tid,p['id']))

@router.post("/api/tasks/{tid}/milestones")
def create_task_milestone(tid:int,body:MilestoneCreate):
    title=(body.title or '').strip()
    if not title: raise HTTPException(400,"MILESTONE_001: Milestone cannot be empty")
    with storage_mutations.tx() as con:
        p=project(con); _task_for_milestone_write(con,p['id'],tid)
        pos=con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM task_milestones WHERE task_id=?",(tid,)).fetchone()[0]
        t=storage_values.now(); mid=con.execute("INSERT INTO task_milestones(project_id,task_id,position,title,description,completed,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",(p['id'],tid,pos,title[:500],(body.description or '')[:12000],t,t)).lastrowid
        return one(con,"SELECT * FROM task_milestones WHERE id=?",(mid,))

@router.patch("/api/task-milestones/{mid}")
def update_task_milestone(mid:int,body:MilestoneUpdate):
    vals=body.model_dump(exclude_unset=True)
    with storage_mutations.tx() as con:
        p=project(con); milestone=one(con,"SELECT * FROM task_milestones WHERE id=? AND project_id=?",(mid,p['id']))
        if not milestone: raise HTTPException(404,"MILESTONE_002: Milestone not found")
        _task_for_milestone_write(con,p['id'],milestone['task_id'])
        if 'title' in vals:
            vals['title']=(vals['title'] or '').strip()
            if not vals['title']: raise HTTPException(400,"MILESTONE_001: Milestone cannot be empty")
            vals['title']=vals['title'][:500]
        if 'description' in vals: vals['description']=(vals['description'] or '')[:12000]
        if 'completed' in vals: vals['completed']=1 if vals['completed'] else 0
        if vals:
            sets=', '.join(f"{key}=?" for key in vals)
            con.execute(f"UPDATE task_milestones SET {sets},updated_at=? WHERE id=?",(*vals.values(),storage_values.now(),mid))
    return {"ok":True}

@router.delete("/api/task-milestones/{mid}")
def delete_task_milestone(mid:int):
    with storage_mutations.tx() as con:
        p=project(con); milestone=one(con,"SELECT * FROM task_milestones WHERE id=? AND project_id=?",(mid,p['id']))
        if not milestone: raise HTTPException(404,"MILESTONE_002: Milestone not found")
        _task_for_milestone_write(con,p['id'],milestone['task_id'])
        _delete_polymorphic_references(con,p['id'],{'milestone':(mid,)})
        con.execute("DELETE FROM task_milestones WHERE id=?",(mid,))
    return {"ok":True}

@router.post("/api/tasks/{tid}/milestones/reorder")
def reorder_task_milestones(tid:int,body:Reorder):
    with storage_mutations.tx() as con:
        p=project(con); _task_for_milestone_write(con,p['id'],tid)
        current=[r[0] for r in con.execute("SELECT id FROM task_milestones WHERE task_id=? AND project_id=? ORDER BY position,id",(tid,p['id'])).fetchall()]
        if len(body.ids)!=len(current) or set(body.ids)!=set(current): raise HTTPException(400,"MILESTONE_003: Reorder must include every milestone exactly once")
        t=storage_values.now()
        for pos,mid in enumerate(body.ids): con.execute("UPDATE task_milestones SET position=?,updated_at=? WHERE id=? AND task_id=?",(pos,t,mid,tid))
    return {"ok":True}
