from __future__ import annotations

"""Task lifecycle routes."""

from fastapi import APIRouter, HTTPException
from api.tasks.contracts import TaskCreate, TaskUpdate
from backend.data_access import _delete_polymorphic_references, allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values

router=APIRouter()

@router.get("/api/tasks")
def list_tasks():
    with storage_mutations.tx() as con:
        p=project(con)
        return allrows(con,"""SELECT t.*,ml.name marker_name,ml.color marker_color,
            (SELECT COUNT(*) FROM references_map r WHERE r.project_id=t.project_id AND r.source_type='task' AND r.source_id=CAST(t.id AS TEXT)) reference_count,
            (SELECT COUNT(*) FROM task_milestones tm WHERE tm.task_id=t.id) milestone_count,
            (SELECT COUNT(*) FROM task_milestones tm WHERE tm.task_id=t.id AND tm.completed=1) milestone_completed_count,
            COALESCE((SELECT GROUP_CONCAT(COALESCE(tm.title,'') || ' ' || COALESCE(tm.description,''),'\n') FROM task_milestones tm WHERE tm.task_id=t.id),'') milestone_search_text,
            COALESCE((SELECT GROUP_CONCAT(CAST(tm.id AS TEXT) || CHAR(29) || COALESCE(tm.title,'') || CHAR(30) || COALESCE(tm.description,''),CHAR(31)) FROM task_milestones tm WHERE tm.task_id=t.id),'') milestone_quick_data
            FROM tasks t LEFT JOIN markup_labels ml ON ml.id=t.marker_id
            WHERE t.project_id=?
            ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'later' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,t.id DESC""",(p['id'],))

@router.post("/api/tasks")
def create_task(body:TaskCreate):
    title=body.title.strip()
    if not title: raise HTTPException(400,"TASK_001: Task cannot be empty")
    if body.status not in {'open','later','done','archived'}: raise HTTPException(400,'TASK_002: Invalid task status')
    with storage_mutations.tx() as con:
        p=project(con); pid=p['id']
        if body.marker_id is not None and not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(body.marker_id,pid)): raise HTTPException(400,"TASK_005: Marker no longer exists")
        t=storage_values.now(); archived_from='open' if body.status=='archived' else None; tid=con.execute("INSERT INTO tasks(project_id,title,description,status,archived_from_status,marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,title,body.description[:12000],body.status,archived_from,body.marker_id,t,t)).lastrowid
        return one(con,"SELECT * FROM tasks WHERE id=?",(tid,))

@router.patch("/api/tasks/{tid}")
def update_task(tid:int,body:TaskUpdate):
    vals=body.model_dump(exclude_unset=True)
    if 'status' in vals and vals['status'] not in {'open','later','done','archived'}: raise HTTPException(400,'TASK_003: Invalid task status')
    if 'title' in vals:
        vals['title']=(vals['title'] or '').strip()
        if not vals['title']: raise HTTPException(400,"TASK_001: Task cannot be empty")
    if vals:
        with storage_mutations.tx() as con:
            p=project(con); current=one(con,"SELECT * FROM tasks WHERE id=? AND project_id=?",(tid,p['id']))
            if not current: raise HTTPException(404,"TASK_006: Task not found")
            # Archived tasks are immutable records until explicitly unarchived.
            # Only a status transition out of Archive is accepted while archived.
            if current['status']=='archived':
                requested=vals.get('status')
                restore=current['archived_from_status'] if current['archived_from_status'] in {'open','later','done'} else 'open'
                if requested!=restore or len(vals)!=1:
                    raise HTTPException(409,"TASK_007: Archived tasks are read-only until unarchived")
                vals={'status':restore,'archived_from_status':None}
            elif vals.get('status')=='archived':
                vals['archived_from_status']=current['status'] if current['status'] in {'open','later','done'} else 'open'
            if vals.get('marker_id') is not None and not one(con,"SELECT id FROM markup_labels WHERE id=? AND project_id=?",(vals['marker_id'],p['id'])): raise HTTPException(400,"TASK_005: Marker no longer exists")
            sets=', '.join(f"{k}=?" for k in vals)+", updated_at=?"; con.execute(f"UPDATE tasks SET {sets} WHERE id=?",(*vals.values(),storage_values.now(),tid))
    return {"ok":True}

@router.delete("/api/tasks/{tid}")
def delete_task(tid:int):
    with storage_mutations.tx() as con:
        p=project(con)
        if not con.execute("SELECT 1 FROM tasks WHERE id=? AND project_id=?",(tid,p['id'])).fetchone(): raise HTTPException(404,"TASK_006: Task not found")
        milestone_ids=[r[0] for r in con.execute("SELECT id FROM task_milestones WHERE task_id=? AND project_id=?",(tid,p['id'])).fetchall()]
        _delete_polymorphic_references(con,p['id'],{'task':(tid,),'milestone':milestone_ids})
        con.execute("DELETE FROM tasks WHERE id=? AND project_id=?",(tid,p['id']))
    return {"ok":True}
