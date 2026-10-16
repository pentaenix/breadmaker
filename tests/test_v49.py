from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

from conftest import assert_ok


def test_v49_version_and_static_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'Milestones' in js and 'Add Web Link' in js
    assert '.milestone-card' in css and '.task-milestone-progress' in css


def test_v49_milestone_lifecycle_progress_reorder_and_duplicate(client, fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Developmental edit','description':'Chapter pass'}))
    one=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Opening','description':'Strengthen the first page'}))
    two=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Continuity'}))
    three=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Final reread'}))

    assert_ok(client.patch(f"/api/task-milestones/{two['id']}",json={'completed':True,'description':'Check chapter 11'}))
    listed=assert_ok(client.get(f"/api/tasks/{task['id']}/milestones"))
    assert [m['title'] for m in listed]==['Opening','Continuity','Final reread']
    assert listed[1]['completed']==1 and listed[1]['description']=='Check chapter 11'

    task_row=next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert task_row['milestone_count']==3 and task_row['milestone_completed_count']==1

    assert_ok(client.post(f"/api/tasks/{task['id']}/milestones/reorder",json={'ids':[three['id'],one['id'],two['id']]}))
    listed=assert_ok(client.get(f"/api/tasks/{task['id']}/milestones"))
    assert [m['id'] for m in listed]==[three['id'],one['id'],two['id']]

    copy=assert_ok(client.post(f"/api/tasks/{task['id']}/duplicate"))
    copied=assert_ok(client.get(f"/api/tasks/{copy['id']}/milestones"))
    assert copy['status']=='open'
    assert [m['title'] for m in copied]==['Final reread','Opening','Continuity']
    assert all(m['completed']==0 for m in copied)


def test_v49_milestone_internal_and_web_links_cleanup_with_task(client, fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Research sources'}))
    milestone=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Verify history'}))
    chapter=fresh_project['bootstrap']['chapters'][0]

    internal=assert_ok(client.post('/api/references',json={
        'source_type':'milestone','source_id':str(milestone['id']),
        'target_type':'chapter','target_id':str(chapter['id'])
    }))
    web=assert_ok(client.post('/api/references',json={
        'source_type':'milestone','source_id':str(milestone['id']),
        'target_type':'url','target_id':'https://example.com/reference','relationship':'Reference article'
    }))
    refs=assert_ok(client.get(f"/api/references?source_type=milestone&source_id={milestone['id']}"))
    assert {r['id'] for r in refs}=={internal['id'],web['id']}
    web_row=next(r for r in refs if r['target_type']=='url')
    assert web_row['target_label']=='Reference article'

    bad=client.post('/api/references',json={
        'source_type':'milestone','source_id':str(milestone['id']),
        'target_type':'url','target_id':'javascript:alert(1)'
    })
    assert bad.status_code==400

    assert_ok(client.delete(f"/api/tasks/{task['id']}"))
    assert assert_ok(client.get(f"/api/references?source_type=milestone&source_id={milestone['id']}"))==[]


def test_v49_archived_task_freezes_milestones_and_links(client, fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Archive me'}))
    milestone=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Keep history'}))
    ref=assert_ok(client.post('/api/references',json={
        'source_type':'milestone','source_id':str(milestone['id']),
        'target_type':'url','target_id':'https://example.com'
    }))
    assert_ok(client.patch(f"/api/tasks/{task['id']}",json={'status':'archived'}))

    assert client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Blocked'}).status_code==409
    assert client.patch(f"/api/task-milestones/{milestone['id']}",json={'completed':True}).status_code==409
    assert client.delete(f"/api/task-milestones/{milestone['id']}").status_code==409
    assert client.delete(f"/api/references/{ref['id']}").status_code==409

    row=next(t for t in assert_ok(client.get('/api/tasks')) if t['id']==task['id'])
    assert_ok(client.patch(f"/api/tasks/{task['id']}",json={'status':row['archived_from_status']}))
    assert_ok(client.patch(f"/api/task-milestones/{milestone['id']}",json={'completed':True}))


def test_v49_milestones_survive_revision_restore(client, fresh_project):
    task=assert_ok(client.post('/api/tasks',json={'title':'Revision task'}))
    milestone=assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Original milestone','description':'Keep me'}))
    assert_ok(client.patch(f"/api/task-milestones/{milestone['id']}",json={'completed':True}))
    assert_ok(client.post('/api/references',json={
        'source_type':'milestone','source_id':str(milestone['id']),
        'target_type':'url','target_id':'https://example.com/revision','relationship':'Revision source'
    }))
    revision=assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('Milestone checkpoint'))))['id']

    assert_ok(client.patch(f"/api/task-milestones/{milestone['id']}",json={'title':'Changed','completed':False}))
    assert_ok(client.post(f"/api/tasks/{task['id']}/milestones",json={'title':'Extra'}))
    assert_ok(client.post(f"/api/revisions/{revision}/restore"))

    restored=assert_ok(client.get(f"/api/tasks/{task['id']}/milestones"))
    assert len(restored)==1
    assert restored[0]['title']=='Original milestone' and restored[0]['completed']==1 and restored[0]['description']=='Keep me'
    refs=assert_ok(client.get(f"/api/references?source_type=milestone&source_id={restored[0]['id']}"))
    assert len(refs)==1 and refs[0]['target_type']=='url' and refs[0]['target_label']=='Revision source'

def test_v491_milestone_collapse_control_owns_chevron_and_accessibility():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'data-milestone-toggle aria-expanded=' in js
    assert 'data-milestone-chevron' in js
    # The chevron must live inside the toggle button so the obvious click target collapses the card.
    assert 'data-milestone-link-count' in js and 'data-milestone-chevron aria-hidden="true">›</span></button>' in js
    assert 'grid-template-columns:minmax(0,1fr) auto 16px' in css
