from __future__ import annotations

"""Task duplication route."""

from fastapi import APIRouter, HTTPException
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values

router=APIRouter()

@router.post("/api/tasks/{tid}/duplicate")
def duplicate_task(tid:int):
    with storage_mutations.tx() as con:
        p=project(con); source=one(con,"SELECT * FROM tasks WHERE id=? AND project_id=?",(tid,p['id']))
        if not source: raise HTTPException(404,"TASK_006: Task not found")
        t=storage_values.now(); new_id=con.execute("INSERT INTO tasks(project_id,title,description,status,archived_from_status,marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(p['id'],f"{source['title']} Copy",source['description'],'open',None,source['marker_id'],t,t)).lastrowid
        for milestone in allrows(con,"SELECT * FROM task_milestones WHERE task_id=? ORDER BY position,id",(tid,)):
            con.execute("INSERT INTO task_milestones(project_id,task_id,position,title,description,completed,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",(p['id'],new_id,milestone['position'],milestone['title'],milestone['description'],t,t))
        return one(con,"SELECT * FROM tasks WHERE id=?",(new_id,))
