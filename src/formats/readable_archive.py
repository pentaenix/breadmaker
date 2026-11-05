from __future__ import annotations

"""Readable copies built from one existing, authorized portable snapshot.

This codec never reads Current or a user's home, and never imports a project.
The original .breadmaker payload remains byte-for-byte available for restoration.
"""

import html
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from formats.manuscript_docx import docx_bytes
from formats.readable_names import file_stem
from security.content_security import is_safe_project_asset_path, sanitize_inline_html

_CONTENT_TABLES=(
    'document_tabs','chapters','scenes','manuscript_blocks','markup_labels','annotations',
    'canvases','canvas_nodes','canvas_edges','story_maps','map_lines','map_items',
    'map_junctions','tasks','task_milestones','references_map','icon_markers',
)
_STYLE='''body{max-width:48rem;margin:3rem auto;padding:0 1.5rem;font:18px/1.65 Georgia,serif;color:#252525;background:#fff}h1,h2,h3,h4{line-height:1.25}article+article{break-before:page;margin-top:3rem}p{white-space:pre-wrap}figure{text-align:center;margin:1.5rem auto}img{max-width:100%;height:auto}figcaption{font-size:.85em}a{color:#354d3d}'''
_README='''BREADMAKER READABLE ZIP

Extract the whole ZIP, then open START-HERE.html in a browser.
The documents folder contains a Word-compatible DOCX, a UTF-8 text copy and
an HTML copy of every document tab, in tab/chapter/block order. HTML copies
use the images in assets; keep these folders together. Filenames are numbered
so duplicate titles and names work on Windows, macOS and Linux.

Project.breadmaker is the original portable project snapshot. Import that file
through Breadmaker > Import > Breadmaker Project to restore project features.
The readable ZIP itself is not an import or recovery package. Editing a DOCX,
HTML or text copy does not update Project.breadmaker or your live project.

The data folder contains readable JSON for current manuscript structure,
markers, canvases, maps, tasks and links. It is a reference copy, not the
Breadmaker Project JSON import format. Reviews, comments, checkpoints, Cuts
and other supported project state remain in Project.breadmaker.

DOCX retains semantic headings, bold, italics, underline, tabs, line breaks,
images, image rows, widths, captions and image descriptions where supported.
Unsupported image formats (such as WebP in the Word renderer) use an explicit
[Image unavailable] placeholder; original assets remain available in HTML.
It does not reproduce editor themes, fonts, Editor's Eye, markers, scene labels
or exact on-screen pagination. Text copies cannot preserve visual formatting.
Use the separate Manuscript as PDF command for a PDF.

Only existing project image assets are copied. No network resources are fetched.
Regenerable narration caches and installation/account credentials are excluded
by the existing portable project exporter. Share this archive only with people
who should receive the project and its included history.
'''


def _page(title,body):
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' file:; style-src \'unsafe-inline\'">'+\
        '<title>'+html.escape(str(title))+'</title><style>'+_STYLE+'</style></head><body>'+body+'</body></html>'


def _document_copies(tab,chapters,blocks,asset_names):
    chunks=[];plain=[]
    for chapter in chapters:
        if chapter['tab_id']!=tab['id']:continue
        owned=[block for block in blocks if block['chapter_id']==chapter['id']]
        if chapter.get('is_implicit') and not any(b.get('block_type')=='image' or str(b.get('text_content') or '').strip() for b in owned):continue
        content=[]
        if not chapter.get('is_implicit'):
            title=str(chapter.get('title') or '');content.append('<h1>'+html.escape(title)+'</h1>');plain.append(title+'\n')
        for block in owned:
            if block.get('block_type')=='image':
                path=str(block.get('image_path') or '');name=path.rsplit('/',1)[-1]
                description=str(block.get('image_description') or '');footer=str(block.get('image_footer') or '')
                if is_safe_project_asset_path(path) and name in asset_names:
                    width=max(5,min(100,int(block.get('image_width') or 100)))
                    image=f'<img src="../assets/{html.escape(name,quote=True)}" alt="{html.escape(description,quote=True)}">'
                    content.append(f'<figure style="width:{width}%">'+image+('<figcaption>'+html.escape(footer)+'</figcaption>' if footer else '')+'</figure>')
                else:content.append('<p>[Image unavailable]</p>')
                plain.append('[Image'+(': '+description if description else '')+']'+('\n'+footer if footer else ''))
                continue
            text=str(block.get('text_content') or '')
            inline=sanitize_inline_html(str(block.get('content_html') or ''),text)
            tag={'heading2':'h2','heading3':'h3','heading4':'h4'}.get(block.get('semantic_style'),'p')
            content.append(f'<{tag}>'+inline+f'</{tag}>');plain.append(text)
        chunks.append('<article>'+''.join(content)+'</article>')
    return _page(tab.get('title') or 'Document',''.join(chunks)), '\n\n'.join(plain)+'\n'


def write_snapshot_copies(package,destination,source,title,tabs,chapters,blocks,content,*,restore_in_web=False):
    """Write readable copies from an already frozen, validated snapshot."""
    asset_entries=[name for name in source.namelist() if name.startswith('assets/') and not name.endswith('/')]
    asset_names={entry.split('/')[1] for entry in asset_entries}
    def read_asset(path):
        if not is_safe_project_asset_path(path):raise ValueError('READABLE_EXPORT_UNSAFE_ASSET_PATH')
        return source.read('assets/'+path.rsplit('/',1)[-1])
    readme=_README
    if restore_in_web:
        readme=readme.replace('Project.breadmaker is the original portable project snapshot. Import that file\nthrough Breadmaker > Import > Breadmaker Project to restore project features.',
            'Project.breadmaker is the original hosted project snapshot. Import that file\nthrough Breadmaker web app > Import > Breadmaker Project to restore project\nfeatures. This hosted recovery file is not a desktop-native project package.')
    try:
        with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            archive.write(package,'Project.breadmaker',compress_type=zipfile.ZIP_STORED)
            archive.writestr('README.txt',readme)
            links=[]
            for index,tab in enumerate(tabs,1):
                stem=f'{index:02d}-'+file_stem(tab.get('title'))
                path='documents/'+stem
                owned_chapters=[ch for ch in chapters if ch['tab_id']==tab['id']]
                owned_ids={ch['id'] for ch in owned_chapters}
                owned_blocks=[block for block in blocks if block['chapter_id'] in owned_ids]
                doc_title=str(tab.get('title') or title)
                archive.writestr(path+'.docx',docx_bytes(doc_title,[tab],owned_chapters,owned_blocks,read_asset))
                rich,plain=_document_copies(tab,owned_chapters,owned_blocks,asset_names)
                archive.writestr(path+'.html',rich);archive.writestr(path+'.txt',plain)
                links.append('<li>'+html.escape(doc_title)+': '+ ' · '.join('<a href="'+html.escape(quote(path+'.'+ext),quote=True)+'">'+label+'</a>' for ext,label in [('html','Read'),('docx','Word'),('txt','Text')])+'</li>')
            archive.writestr('START-HERE.html',_page(title,'<h1>'+html.escape(title)+'</h1><ul>'+''.join(links)+'</ul><p>Read README.txt for contents, restoration and format limitations.</p>'))
            archive.writestr('data/project-content.json',json.dumps({'format':'breadmaker-readable-reference','version':1,'title':title,'tables':content},ensure_ascii=False,indent=2)+'\n')
            for entry in asset_entries:
                with source.open(entry) as incoming,archive.open(entry,'w') as outgoing:shutil.copyfileobj(incoming,outgoing)
    except Exception:
        destination.unlink(missing_ok=True);raise
    return file_stem(title)


def write_readable_archive(package:Path,destination:Path) -> str:
    """Render one generated .breadmaker package; return a safe download stem."""
    with zipfile.ZipFile(package) as source, tempfile.TemporaryDirectory(prefix='breadmaker-readable-') as td:
        names=source.namelist()
        if len(names)!=len(set(names)) or 'project.sqlite3' not in names:
            raise ValueError('READABLE_EXPORT_INVALID_SNAPSHOT')
        asset_entries=[name for name in names if name.startswith('assets/') and not name.endswith('/')]
        if any(not is_safe_project_asset_path('/api/'+name) for name in asset_entries):
            raise ValueError('READABLE_EXPORT_UNSAFE_ASSET_NAME')
        database=Path(td)/'snapshot.sqlite3'
        with source.open('project.sqlite3') as incoming,database.open('wb') as outgoing:shutil.copyfileobj(incoming,outgoing)
        con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
        try:
            con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=ON');con.execute('PRAGMA trusted_schema=OFF')
            title=str(con.execute('SELECT title FROM projects ORDER BY id LIMIT 1').fetchone()['title'])
            tabs=[dict(row) for row in con.execute('SELECT * FROM document_tabs ORDER BY position,id')]
            chapters=[dict(row) for row in con.execute('SELECT * FROM chapters ORDER BY tab_id,position,id')]
            blocks=[dict(row) for row in con.execute('SELECT * FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id')]
            content={table:[dict(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY id')] for table in _CONTENT_TABLES}
        finally:con.close()
        return write_snapshot_copies(package,destination,source,title,tabs,chapters,blocks,content)
