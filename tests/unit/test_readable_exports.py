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
