"""Readable export uses one portable snapshot and preserves native restore."""
import io
import json
import zipfile

import pytest
from docx import Document
from PIL import Image
from conftest import assert_ok


def _image():
    out=io.BytesIO();Image.new('RGB',(12,8),(80,120,180)).save(out,format='PNG');return out.getvalue()


def test_readable_zip_has_every_document_images_structure_and_restorable_project(client,fresh_project):
    boot=fresh_project['bootstrap'];chapter=boot['chapters'][0]
    assert_ok(client.put(f'/api/chapters/{chapter["id"]}/blocks',json={'blocks':[{
        'text_content':'Bold italic under\tline\nbreak',
        'content_html':'<strong>Bold</strong> <em>italic</em> <u>under</u>\tline<br>break'}]}))
    payload=_image();asset=assert_ok(client.post('/api/assets',files={'file':('Synthetic.png',payload,'image/png')}))['path']
    target=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks'][0]['id']
    assert_ok(client.post('/api/manuscript/images/insert',json={'image_path':asset,'target_block_id':target,
        'placement':'after','image_width':37,'image_footer':'Synthetic caption','image_description':'Synthetic description'}))
    assert_ok(client.post('/api/documents',json={'title':'CON # / Notes','is_manuscript':False}))
    assert_ok(client.post('/api/tasks',json={'title':'Keep task'}))
    response=client.get('/api/export/zip');assert response.status_code==200,response.text
    assert response.headers['content-type']=='application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names=archive.namelist();assert 'START-HERE.html' in names
        assert len([name for name in names if name.endswith('.docx')])==2
        assert archive.read('assets/'+asset.rsplit('/',1)[-1])==payload
        reference=json.loads(archive.read('data/project-content.json'))
        assert reference['tables']['tasks'][0]['title']=='Keep task'
        assert not {'ui_state','bookmarks','review_participants'} & set(reference['tables'])
        path=next(name for name in names if name.endswith('.docx'))
        doc=Document(io.BytesIO(archive.read(path)))
        paragraph=next(p for p in doc.paragraphs if p.text.startswith('Bold'))
        assert paragraph.text=='Bold italic under\tline\nbreak'
        assert next(r for r in paragraph.runs if r.text=='Bold').bold
        assert next(r for r in paragraph.runs if r.text=='italic').italic
        assert next(r for r in paragraph.runs if r.text=='under').underline
        assert len(doc.inline_shapes)==1 and doc.inline_shapes[0]._inline.docPr.get('descr')=='Synthetic description'
        portable=archive.read('Project.breadmaker')
    restored=assert_ok(client.post('/api/projects/import',files={'file':('Restored.breadmaker',portable,'application/octet-stream')}))
    assert restored['project']['id']!=fresh_project['project']['id']
    current=assert_ok(client.get('/api/bootstrap'))
    assert len(current['tabs'])==2
    data=assert_ok(client.get(f'/api/chapters/{current["chapters"][0]["id"]}'))
    assert any(block['text_content']=='Bold italic under\tline\nbreak' for block in data['blocks'])
    assert client.get(asset).content==payload
    assert assert_ok(client.get('/api/integrity/deep'))['ok']


def test_zip_rendering_uses_frozen_content_even_if_current_changes(client,fresh_project,monkeypatch):
    import formats.readable_archive as archive_module
    chapter=fresh_project['bootstrap']['chapters'][0]
    def write(text):return assert_ok(client.put(f'/api/chapters/{chapter["id"]}/blocks',json={'blocks':[{'text_content':text,'content_html':text}]}))
    write('Before snapshot');native=archive_module.docx_bytes;changed=[]
    def render(*args):
        if not changed:changed.append(True);write('After snapshot')
        return native(*args)
    monkeypatch.setattr(archive_module,'docx_bytes',render)
    response=client.get('/api/export/zip');assert response.status_code==200,response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text=archive.read(next(name for name in archive.namelist() if name.endswith('.txt') and name!='README.txt')).decode()
        assert 'Before snapshot' in text and 'After snapshot' not in text
        assert 'Before snapshot' in archive.read('data/project-content.json').decode()
    assert assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks'][0]['text_content']=='After snapshot'


def test_hosted_word_and_zip_keep_formatting_and_portability_without_local_workspace(monkeypatch,tmp_path):
    from tests.test_v10364 import _replicas
    from hosted.runtime_services import _DEV_USER_ID as owner
    from hosted.document_io import export_docx_bytes
    from hosted.packages import HostedProjectPackageService
    from formats.readable_archive import write_readable_archive
    one,make,docs,pid,cid=_replicas(monkeypatch);domain=one.projects.domain
    block=domain.list('manuscript_block',{'project_id':pid},user_id=owner)[0]
    domain.update('manuscript_block',{'project_id':pid,'block_id':block['id']},{'text_content':'Bold\nbreak','content_html':'<b>Bold</b><br>break'},user_id=owner)
    with one.context.bind(pid,user_id=owner):
        raw=export_docx_bytes(domain,one.assets,one.projects.get(pid),pid,owner)
    paragraph=next(p for p in Document(io.BytesIO(raw)).paragraphs if p.text.startswith('Bold'))
    assert paragraph.text=='Bold\nbreak' and paragraph.runs[0].bold
    package=tmp_path/'project.breadmaker';original=HostedProjectPackageService(one.projects).export_local_bytes(pid,owner);package.write_bytes(original)
    output=tmp_path/'readable.zip';write_readable_archive(package,output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read('Project.breadmaker')==original
        restored=HostedProjectPackageService(make().projects).import_bytes(archive.read('Project.breadmaker'),owner)
    assert any(row['text_content']=='Bold\nbreak' for row in make().projects.domain.list('manuscript_block',{'project_id':restored['id']},user_id=owner))


def test_hosted_zip_rejects_other_membership_and_encrypted_mode_before_render(monkeypatch):
    from tests.test_v10364 import _replicas
    from hosted.runtime_services import _DEV_USER_ID as owner
    from hosted.firestore.contracts import ProjectUnavailable
    from hosted.packages import HostedProjectPackageService
    one,*_,pid,cid=_replicas(monkeypatch);service=HostedProjectPackageService(one.projects)
    with pytest.raises(ProjectUnavailable):service.export_local_bytes(pid,'usr_'+'f'*32)
    one.projects.domain.encrypted_fields=object()
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):service.export_local_bytes(pid,owner)


def test_hosted_portable_notes_translate_thread_links_and_restore_messages(monkeypatch,tmp_path):
    from tests.test_v10364 import _replicas
    from hosted.runtime_services import _DEV_USER_ID as owner
    from hosted.packages import HostedProjectPackageService
    from formats.readable_archive import write_readable_archive
    one,make,docs,pid,cid=_replicas(monkeypatch);domain=one.projects.domain
    block=domain.list('manuscript_block',{'project_id':pid},user_id=owner)[0]
    thread=domain.create('comment_thread',{'project_id':pid},{'status':'open'},user_id=owner)
    domain.create('comment_message',{'project_id':pid,'thread_id':thread['id']},{'body':'Preserved note message'},user_id=owner)
    domain.create('annotation',{'project_id':pid},{'block_id':block['id'],'chapter_id':cid,
        'annotation_type':'comment','thread_id':thread['id'],'body':'Preserved note anchor'},user_id=owner)
    raw=HostedProjectPackageService(one.projects).export_local_bytes(pid,owner)
    package=tmp_path/'notes.breadmaker';package.write_bytes(raw)
    write_readable_archive(package,tmp_path/'notes.zip')
    restored=HostedProjectPackageService(make().projects).import_bytes(raw,owner)
    annotations=make().projects.domain.list('annotation',{'project_id':restored['id']},user_id=owner)
    threads=make().projects.domain.list('comment_thread',{'project_id':restored['id']},user_id=owner)
    messages=make().projects.domain.list('comment_message',{'project_id':restored['id'],'thread_id':threads[0]['id']},user_id=owner)
    assert len(annotations)==len(threads)==len(messages)==1
    assert annotations[0]['thread_id']==threads[0]['id']
    assert messages[0]['body']=='Preserved note message'


def test_export_download_removes_transient_snapshot_and_zip_after_response(client,fresh_project,monkeypatch):
    from api.import_export import readable_export
    from pathlib import Path
    native=readable_export.tempfile.TemporaryDirectory;created=[]
    def temporary(*args,**kwargs):
        result=native(*args,**kwargs);created.append(Path(result.name));return result
    monkeypatch.setattr(readable_export.tempfile,'TemporaryDirectory',temporary)
    response=client.get('/api/export/zip');assert response.status_code==200,response.text
    assert created and all(not path.exists() for path in created)
