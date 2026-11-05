"""Readable output names and HTML remain inert when project text is hostile."""
import re
from urllib.parse import unquote
from formats.readable_names import file_stem
from formats.readable_archive import _document_copies, _page


def test_portable_names_handle_reserved_unicode_and_traversal():
    for raw in ['../A\\B/C:*?<>|','CON','NUL.txt','..','a\x00b','🔥'*500,'A\u202eexe']:
        name=file_stem(raw)
        assert name and len(name.encode('utf-8'))<=180
        assert not re.search(r'[\\/:*?"<>|\x00-\x1f]',name)
        assert not re.match(r'^(con|nul|aux|prn|com[0-9]|lpt[0-9])(?:\.|$)',name,re.I)
        assert not name.endswith((' ','.'))


def test_html_copies_escape_titles_and_never_execute_stored_markup_or_external_assets():
    tab={'id':1,'title':'<script>bad()</script>'};chapter={'id':2,'tab_id':1,'title':'<img src=x onerror=bad()>'}
    blocks=[{'chapter_id':2,'text_content':'Safe\ntext','content_html':'<script>bad()</script><b onclick="bad()">Safe</b><br>text'},
            {'chapter_id':2,'block_type':'image','image_path':'https://example.test/secret.png','image_description':'<b>description</b>'}]
    rich,plain=_document_copies(tab,[chapter],blocks,set())
    assert '<script>' not in rich and 'onerror=' not in rich.replace('&lt;img src=x onerror=bad()&gt;','')
    assert '<b>Safe</b><br/>text' in rich and '[Image unavailable]' in rich
    assert 'https://example.test' not in rich
    assert 'Safe\ntext' in plain


def test_archive_rejects_asset_traversal_before_opening_database(tmp_path):
    import zipfile
    import pytest
    from formats.readable_archive import write_readable_archive
    source=tmp_path/'unsafe.breadmaker';output=tmp_path/'readable.zip'
    with zipfile.ZipFile(source,'w') as archive:
        archive.writestr('project.sqlite3',b'not a database')
        archive.writestr('assets/../../outside.png',b'unsafe')
    with pytest.raises(ValueError,match='READABLE_EXPORT_UNSAFE_ASSET_NAME'):
        write_readable_archive(source,output)
    assert not output.exists() and not (tmp_path/'outside.png').exists()


def test_word_subheadings_inherit_heading_style_while_preserving_inline_emphasis():
    from io import BytesIO
    from docx import Document
    from formats.manuscript_docx import docx_bytes
    blocks=[{'id':1,'chapter_id':1,'semantic_style':'heading2',
        'text_content':'Subheading emphasis','content_html':'Subheading <em>emphasis</em>'}]
    raw=docx_bytes('Synthetic',[{'id':1,'is_manuscript':True}],
        [{'id':1,'tab_id':1,'title':'Chapter'}],blocks,lambda path:b'')
    paragraph=next(p for p in Document(BytesIO(raw)).paragraphs if p.text=='Subheading emphasis')
    assert paragraph.style.name=='Heading 2' and paragraph.style.font.bold
    # Explicit False overrides Word's heading style and made headings lose bold.
    assert all(run.bold is None for run in paragraph.runs)
    assert next(run for run in paragraph.runs if run.text=='emphasis').italic


def test_hosted_readable_rejects_unsafe_members_bad_asset_hash_and_incomplete_documents(tmp_path):
    import json
    import zipfile
    import pytest
    from hosted.readable_source import write_hosted_readable_archive
    package=tmp_path/'source.breadmaker';output=tmp_path/'out.zip'
    pid='a'*32
    manifest={'format':'breadmaker-hosted-project','format_version':3,
        'authorization_portable':False,'source_project_id':pid,'title':'Synthetic'}
    entities={'document_tab':[{'ids':{'project_id':pid,'tab_id':'tab_one'},'data':{'title':'Main'}}],
        'chapter':[{'ids':{'project_id':pid,'chapter_id':'chapter_one'},'data':{'tab_id':'tab_missing'}}]}
    def write(extra=None):
        with zipfile.ZipFile(package,'w') as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            archive.writestr('project.json',json.dumps({'entities':entities}))
            for name,data in (extra or {}).items():archive.writestr(name,data)
    write({'assets/../../outside.png':b'bad'})
    with pytest.raises(ValueError,match='HOSTED_READABLE_SNAPSHOT_INVALID'):write_hosted_readable_archive(package,output)
    write({'assets/'+'a'*64+'.png':b'bad'})
    with pytest.raises(ValueError,match='HOSTED_READABLE_ASSET_HASH_MISMATCH'):write_hosted_readable_archive(package,output)
    write()
    with pytest.raises(ValueError,match='HOSTED_READABLE_DOCUMENT_LINK_INVALID'):write_hosted_readable_archive(package,output)
    assert not output.exists() and not (tmp_path/'outside.png').exists()
