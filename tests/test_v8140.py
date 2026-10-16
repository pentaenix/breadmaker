from __future__ import annotations

from workspace import workspace

from storage import schema as storage_schema
from storage import values as storage_values

from api.reviews import domain as reviews_domain
from api.reviews.contracts import ReviewSelectionIn
from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

import io
import json
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from conftest import assert_ok
import app as app_module
from formats import breadreview
from workspace import APP_SCHEMA_VERSION
from test_v8130 import _review

ROOT=Path(__file__).resolve().parents[1]
PASSPHRASE='correct horse battery staple'


def _secure_export(client,review,participant,passphrase=PASSPHRASE):
    response=client.post(
        f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview",
        json={'passphrase':passphrase},
    )
    assert response.status_code==200,response.text
    assert response.headers.get('x-breadreview-encrypted')=='1'
    return response.content


def _plain_export(client,review,participant):
    response=client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview")
    assert response.status_code==200,response.text
    return response.content


def _write(path:Path,data:bytes):
    path.write_bytes(data)
    return path


def _unencrypted_envelope(inner:bytes, *, version:int=2) -> bytes:
    header={
        'encrypted':False,
        'package_format':breadreview.PACKAGE_FORMAT,
        'package_version':version,
        'payload_encoding':'zip',
    }
    encoded=json.dumps(header,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return breadreview.MAGIC+struct.pack('>I',len(encoded))+encoded+inner


def test_v8140_release_contract_package_v2_and_no_schema_bump():
    version=ROOT.joinpath('VERSION').read_text().strip(); assert tuple(map(int,version.split('.'))) >= (8,14,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=30
    assert breadreview.PACKAGE_VERSION==2
    requirements=ROOT.joinpath('requirements.txt').read_text().splitlines()
    cryptography_pin=next(line for line in requirements if line.startswith('cryptography=='))
    cryptography_version=tuple(int(part) for part in cryptography_pin.split('==',1)[1].split('.'))
    assert cryptography_version >= (46,0,7)
    app=backend_source(ROOT)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    html=ROOT.joinpath('static','index.html').read_text()
    roadmap=ROOT.joinpath('docs','BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md').read_text()
    assert '/api/portable-review/unlock' in app
    assert 'AES-256-GCM' in ROOT.joinpath('src','formats','breadreview.py').read_text()
    assert 'scrypt' in ROOT.joinpath('src','formats','breadreview.py').read_text()
    assert 'breadReviewPassphraseDialog' in js and 'passphrase_optional' in js and 'breadReviewExportPassphraseConfirm' in js


def test_v8140_encrypted_export_hides_review_metadata_and_requires_passphrase(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    raw=_secure_export(client,review,rebeca)
    path=_write(tmp_path/'protected.breadreview',raw)
    info=breadreview.inspect(path)
    assert info=={'package_format':'breadmaker-review','package_version':2,'encrypted':True}
    for secret in (review['id'],rebeca['id'],'Portable Beta','Rebeca','alpha beta gamma'):
        assert secret.encode() not in raw
    with pytest.raises(breadreview.BreadReviewError) as missing:
        breadreview.read(path)
    assert missing.value.code=='BREADREVIEW_037'
    with pytest.raises(breadreview.BreadReviewError) as wrong:
        breadreview.read(path,'wrong passphrase')
    assert wrong.value.code=='BREADREVIEW_038'
    manifest=breadreview.read(path,PASSPHRASE)
    assert manifest['review_id']==review['id'] and manifest['participant_id']==rebeca['id']
    assert manifest['snapshot']['blocks'][0]['text_content']=='alpha beta gamma'


def test_v8140_encrypted_temporary_review_unlocks_rewrites_and_remains_isolated(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    package=_secure_export(client,review,rebeca)
    active_before=workspace.active_id()
    try:
        opened=assert_ok(client.post('/api/portable-review/open-temporary',files={'file':('rebeca.breadreview',package,'application/octet-stream')}))
        assert opened['locked'] is True and opened['encrypted'] is True and 'payload' not in opened
        denied=client.post('/api/portable-review/unlock',json={'passphrase':'incorrect passphrase'})
        assert denied.status_code==401 and 'BREADREVIEW_038' in denied.text
        unlocked=assert_ok(client.post('/api/portable-review/unlock',json={'passphrase':PASSPHRASE}))
        assert unlocked['locked'] is False and unlocked['payload']['participant']['display_name']=='Rebeca'
        assert workspace.active_id()==active_before
        assert_ok(client.post('/api/portable-review/begin'))
        reader=assert_ok(client.get('/api/portable-review/reader'));block=reader['snapshot']['blocks'][0]
        selection={'start_block_id':block['source_block_id'],'end_block_id':block['source_block_id'],'start_offset':0,'end_offset':5}
        assert_ok(client.post('/api/portable-review/comments',json={'selection':selection,'body':'Encrypted portable comment'}))
        returned=client.get('/api/portable-review/package');assert returned.status_code==200
        saved=_write(tmp_path/'returned.breadreview',returned.content)
        assert breadreview.inspect(saved)['encrypted'] is True
        assert b'Encrypted portable comment' not in returned.content
        manifest=breadreview.read(saved,PASSPHRASE)
        assert [row['body'] for row in manifest['feedback']['comments']]==['Encrypted portable comment']
        writer=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
        assert writer['comments']==[]
    finally:
        client.post('/api/portable-review/close-temporary')
    assert workspace.active_id()==active_before


def test_v8140_encrypted_return_import_requires_passphrase_and_is_idempotent(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    path=_write(tmp_path/'returned.breadreview',_secure_export(client,review,rebeca))
    manifest=breadreview.read(path,PASSPHRASE)
    block=manifest['snapshot']['blocks'][0]
    values=reviews_domain._review_selection_values(
        manifest['snapshot']['blocks'],manifest['snapshot_id'],
        ReviewSelectionIn(start_block_id=block['source_block_id'],end_block_id=block['source_block_id'],start_offset=0,end_offset=5),
    )
    anchor={'id':'review_anchor_v8140','review_id':review['id'],'created_at':storage_values.now(),**values}
    comment={'id':'review_comment_v8140','review_id':review['id'],'participant_id':rebeca['id'],'participant_name':'Rebeca','anchor_id':anchor['id'],'body':'Protected import','tag':'','status':'open','created_at':storage_values.now(),'updated_at':storage_values.now()}
    manifest['feedback']['anchors']=[anchor];manifest['feedback']['comments']=[comment];manifest['updated_at']=storage_values.now()
    breadreview.rewrite(path,manifest,PASSPHRASE)
    no_password=client.post('/api/reviews/import-breadreview',files={'file':('returned.breadreview',path.read_bytes(),'application/octet-stream')})
    assert no_password.status_code==401 and 'BREADREVIEW_037' in no_password.text
    imported=assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('returned.breadreview',path.read_bytes(),'application/octet-stream')},data={'passphrase':PASSPHRASE}))
    assert imported['duplicate'] is False and imported['imported']>0
    duplicate=assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('returned.breadreview',path.read_bytes(),'application/octet-stream')},data={'passphrase':PASSPHRASE}))
    assert duplicate['duplicate'] is True
    feedback=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert [row['body'] for row in feedback['comments']]==['Protected import']


def test_v8140_ciphertext_header_and_truncation_tampering_fail_closed(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    original=bytearray(_secure_export(client,review,rebeca))
    header_start=len(breadreview.MAGIC)+4
    header_len=struct.unpack('>I',original[len(breadreview.MAGIC):header_start])[0]
    payload_start=header_start+header_len

    changed_cipher=bytearray(original);changed_cipher[-1]^=1
    p=_write(tmp_path/'cipher.breadreview',bytes(changed_cipher))
    with pytest.raises(breadreview.BreadReviewError) as exc:breadreview.read(p,PASSPHRASE)
    assert exc.value.code=='BREADREVIEW_038'

    changed_header=bytearray(original)
    header=json.loads(changed_header[header_start:payload_start].decode())
    header['nonce']='00'*breadreview.NONCE_BYTES
    encoded=json.dumps(header,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    assert len(encoded)==header_len
    changed_header[header_start:payload_start]=encoded
    p=_write(tmp_path/'header.breadreview',bytes(changed_header))
    with pytest.raises(breadreview.BreadReviewError) as exc:breadreview.read(p,PASSPHRASE)
    assert exc.value.code=='BREADREVIEW_038'

    truncated=_write(tmp_path/'truncated.breadreview',bytes(original[:-9]))
    with pytest.raises(breadreview.BreadReviewError):breadreview.read(truncated,PASSPHRASE)


def test_v8140_v1_and_future_packages_are_intentionally_unsupported(tmp_path):
    legacy=tmp_path/'legacy.breadreview'
    with zipfile.ZipFile(legacy,'w') as z:z.writestr('review.json','{}')
    with pytest.raises(breadreview.BreadReviewError) as old:
        breadreview.inspect(legacy)
    assert old.value.code=='BREADREVIEW_002'

    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('review.json','{}')
    future=_write(tmp_path/'future.breadreview',_unencrypted_envelope(buf.getvalue(),version=999))
    with pytest.raises(breadreview.BreadReviewError) as newer:
        breadreview.inspect(future)
    assert newer.value.code=='BREADREVIEW_002'


def test_v8140_archive_hardening_rejects_duplicates_unsafe_paths_and_compression_bombs(tmp_path):
    cases=[]
    duplicate=io.BytesIO()
    with zipfile.ZipFile(duplicate,'w') as z:
        z.writestr('review.json','{}');z.writestr('review.json','{}')
    cases.append(('duplicate',duplicate.getvalue(),'BREADREVIEW_027'))

    traversal=io.BytesIO()
    with zipfile.ZipFile(traversal,'w') as z:
        z.writestr('review.json','{}');z.writestr('assets/../escape.txt','x')
    cases.append(('traversal',traversal.getvalue(),'BREADREVIEW_016'))

    symlink=io.BytesIO()
    with zipfile.ZipFile(symlink,'w') as z:
        z.writestr('review.json','{}')
        info=zipfile.ZipInfo('assets/link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(info,'target')
    cases.append(('symlink',symlink.getvalue(),'BREADREVIEW_028'))

    bomb=io.BytesIO()
    with zipfile.ZipFile(bomb,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        z.writestr('review.json','{}');z.writestr('assets/bomb.txt',b'A'*(2*1024*1024))
    cases.append(('bomb',bomb.getvalue(),'BREADREVIEW_030'))

    for name,inner,code in cases:
        path=_write(tmp_path/f'{name}.breadreview',_unencrypted_envelope(inner))
        with pytest.raises(breadreview.BreadReviewError) as exc:
            breadreview.read(path)
        assert exc.value.code==code


def test_v8140_manifest_hardening_rejects_hidden_assets_and_participant_review_mismatch(client,fresh_project,tmp_path):
    review,rebeca,_question=_review(client)
    path=_write(tmp_path/'plain.breadreview',_plain_export(client,review,rebeca))
    manifest=breadreview.read(path)
    manifest['participant']['review_id']='review_other'
    with pytest.raises(breadreview.BreadReviewError) as mismatch:
        breadreview.write_new(tmp_path/'mismatch.breadreview',manifest,{})
    assert mismatch.value.code=='BREADREVIEW_039'
    assert not (tmp_path/'mismatch.breadreview').exists()

    valid=breadreview.read(path)
    inner=io.BytesIO()
    sealed=breadreview.seal(valid)
    with zipfile.ZipFile(inner,'w') as z:
        z.writestr('review.json',json.dumps(sealed,separators=(',',':')))
        z.writestr('assets/hidden.bin',b'hidden')
    hidden=_write(tmp_path/'hidden.breadreview',_unencrypted_envelope(inner.getvalue()))
    with pytest.raises(breadreview.BreadReviewError) as extra:
        breadreview.read(hidden)
    assert extra.value.code=='BREADREVIEW_041'


def test_v8140_json_limits_reject_deep_or_oversized_values():
    deep={'package_format':breadreview.PACKAGE_FORMAT,'package_version':2}
    cursor=deep
    for _ in range(breadreview.MAX_JSON_DEPTH+2):
        cursor['x']={};cursor=cursor['x']
    with pytest.raises(breadreview.BreadReviewError) as nested:
        breadreview.validate_manifest(deep)
    assert nested.value.code=='BREADREVIEW_023'

    huge={'package_format':breadreview.PACKAGE_FORMAT,'package_version':2,'x':'é'*(breadreview.MAX_JSON_STRING_BYTES//2+1)}
    with pytest.raises(breadreview.BreadReviewError) as oversized:
        breadreview.validate_manifest(huge)
    assert oversized.value.code=='BREADREVIEW_024'


def test_v8140_import_rejects_permission_escalation_from_malicious_return(client,fresh_project,tmp_path):
    from test_v8100 import _chapter,_set_blocks
    chapter=_chapter(client);_set_blocks(client,chapter['id'],[{'text_content':'locked text','content_html':'locked text'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Read only','permission_read':True,'permission_comment':False,'permission_suggest':False,'permission_feedback':False}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Mallory'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    path=_write(tmp_path/'malicious.breadreview',_plain_export(client,review,participant))
    manifest=breadreview.read(path);block=manifest['snapshot']['blocks'][0]
    selection=ReviewSelectionIn(start_block_id=block['source_block_id'],end_block_id=block['source_block_id'],start_offset=0,end_offset=6)
    values=reviews_domain._review_selection_values(manifest['snapshot']['blocks'],manifest['snapshot_id'],selection)
    anchor={'id':'review_anchor_attack','review_id':review['id'],'created_at':storage_values.now(),**values}
    comment={'id':'review_comment_attack','review_id':review['id'],'participant_id':participant['id'],'anchor_id':anchor['id'],'body':'Should not import','created_at':storage_values.now(),'updated_at':storage_values.now()}
    manifest['feedback']['anchors']=[anchor];manifest['feedback']['comments']=[comment]
    breadreview.rewrite(path,manifest)
    response=client.post('/api/reviews/import-breadreview',files={'file':('malicious.breadreview',path.read_bytes(),'application/octet-stream')})
    assert response.status_code==409 and 'BREADREVIEW_IMPORT_011' in response.text
    feedback=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert feedback['comments']==[]


def test_v8140_stale_return_cannot_erase_or_regress_newer_feedback(client,fresh_project,tmp_path):
    review,rebeca,question=_review(client)
    old_path=_write(tmp_path/'old.breadreview',_plain_export(client,review,rebeca))
    newer_path=_write(tmp_path/'newer.breadreview',old_path.read_bytes())
    manifest=breadreview.read(newer_path);block=manifest['snapshot']['blocks'][0]
    values=reviews_domain._review_selection_values(manifest['snapshot']['blocks'],manifest['snapshot_id'],ReviewSelectionIn(start_block_id=block['source_block_id'],end_block_id=block['source_block_id'],start_offset=0,end_offset=5))
    t=storage_values.now();anchor={'id':'review_anchor_newer','review_id':review['id'],'created_at':t,**values}
    comment={'id':'review_comment_newer','review_id':review['id'],'participant_id':rebeca['id'],'anchor_id':anchor['id'],'body':'Newer survives','created_at':t,'updated_at':t}
    frozen_question_id=manifest['questions'][0]['id']
    assert manifest['questions'][0]['source_question_id']==question['id']
    answer={'id':'review_answer_newer','review_id':review['id'],'question_id':frozen_question_id,'participant_id':rebeca['id'],'answer_text':'new answer','created_at':t,'updated_at':t}
    general={'id':'review_general_newer','review_id':review['id'],'participant_id':rebeca['id'],'body':'new general','created_at':t,'updated_at':t}
    manifest['feedback']['anchors']=[anchor];manifest['feedback']['comments']=[comment];manifest['feedback']['answers']=[answer];manifest['feedback']['general_feedback']=general
    breadreview.rewrite(newer_path,manifest)
    assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('newer.breadreview',newer_path.read_bytes(),'application/octet-stream')}))
    # Re-import the older state from the same package identity. It must be non-destructive.
    stale=assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('old.breadreview',old_path.read_bytes(),'application/octet-stream')}))
    assert stale['duplicate'] is False
    feedback=assert_ok(client.get(f"/api/reviews/{review['id']}/feedback"))
    assert [row['body'] for row in feedback['comments']]==['Newer survives']
    assert feedback['feedback_forms'][0]['answers'][0]['answer_text']=='new answer'


def test_v8140_unicode_passphrase_round_trip(tmp_path):
    manifest={
        'package_format':breadreview.PACKAGE_FORMAT,'package_version':2,'package_id':'breadreview_unicode',
        'project_id':'project_unicode','review_id':'review_unicode','snapshot_id':'snapshot_unicode','participant_id':'review_participant_unicode',
        'review':{'id':'review_unicode'},'participant':{'id':'review_participant_unicode','review_id':'review_unicode'},
        'snapshot':{'id':'snapshot_unicode','chapters':[],'scenes':[],'blocks':[]},
        'feedback':{'anchors':[],'highlights':[],'comments':[],'suggestions':[],'answers':[],'events':[]},
        'questions':[],'assets':{},
    }
    path=tmp_path/'unicode.breadreview';passphrase='pan dulce 🥐 密碼 contraseña'
    breadreview.write_new(path,manifest,{},passphrase)
    assert breadreview.read(path,passphrase)['project_id']=='project_unicode'
    assert passphrase.encode('utf-8') not in path.read_bytes()
