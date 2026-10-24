from __future__ import annotations

import html
import re
from pathlib import Path
from docx import Document
from docx.document import Document as _Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from backend.request_context import _enforce_hosted_project_storage
from backend.dependencies import feature_call

_store_content_addressed_image_blob = feature_call('durability.cuts', '_store_content_addressed_image_blob')

HIGHLIGHT_COLORS={'YELLOW': '#ffff00', 'BRIGHT_GREEN': '#00ff00', 'TURQUOISE': '#00ffff', 'PINK': '#ff66cc', 'BLUE': '#5b7cff', 'RED': '#ff6666', 'DARK_BLUE': '#304b8f', 'TEAL': '#3aa6a0', 'GREEN': '#49a35a', 'VIOLET': '#9c65d6', 'DARK_RED': '#9e4747', 'DARK_YELLOW': '#b9a436', 'GRAY_50': '#808080', 'GRAY_25': '#c0c0c0', 'BLACK': '#333333'}

def iter_block_items(parent):
    parent_elm=parent.element.body if isinstance(parent,_Document) else parent._tc
    for child in parent_elm.iterchildren():
        if child.tag==qn('w:p'): yield Paragraph(child,parent)
        elif child.tag==qn('w:tbl'): yield Table(child,parent)

def _save_image_blob(blob:bytes,ext:str)->str:
    _enforce_hosted_project_storage(additional_bytes=len(blob))
    stored=_store_content_addressed_image_blob(blob,ext or '.png')
    return stored.api_path

def _highlight_info(run):
    try:
        h=run.font.highlight_color
        if h is None:return None
        name=getattr(h,'name',None) or str(h).split()[0]
        name=str(name).upper().replace(' ','_')
        return {'name':name.replace('_',' ').title(),'color':HIGHLIGHT_COLORS.get(name,'#ffff00')}
    except Exception:return None

def _styled_html(run,text):
    s=html.escape(text)
    if run.bold:s=f'<strong>{s}</strong>'
    if run.italic:s=f'<em>{s}</em>'
    if run.underline:s=f'<u>{s}</u>'
    return s

def paragraph_parts(p:Paragraph, *, image_store=None):
    """Return text/image parts in run-child order. Literal Word tabs remain \t."""
    parts=[]; text=''; html_bits=[]; highlights=[]; offset=0
    def flush():
        nonlocal text,html_bits,highlights,offset
        if text or html_bits:
            parts.append({'kind':'text','text':text,'html':''.join(html_bits),'highlights':highlights})
        text='';html_bits=[];highlights=[];offset=0
    for run in p.runs:
        hi=_highlight_info(run)
        for child in run._r:
            if child.tag==qn('w:rPr'): continue
            seg=''
            if child.tag==qn('w:t'): seg=child.text or ''
            elif child.tag==qn('w:tab'): seg='\t'
            elif child.tag==qn('w:br'):
                # Page breaks are structural; line breaks are text.
                if child.get(qn('w:type'))!='page': seg='\n'
            elif child.tag==qn('w:drawing'):
                flush()
                blips=child.xpath('.//a:blip')
                for blip in blips:
                    rid=blip.get(qn('r:embed'))
                    if rid and rid in p.part.rels:
                        part=p.part.rels[rid].target_part; blob=part.blob; ext=(part.content_type.split('/')[-1] or 'png').replace('jpeg','jpg')
                        parts.append({'kind':'image','image_path':(image_store or _save_image_blob)(blob,ext)})
                continue
            if seg:
                st=offset; text+=seg; html_bits.append(_styled_html(run,seg)); offset+=len(seg)
                if hi:
                    if highlights and highlights[-1]['end']==st and highlights[-1]['name']==hi['name'] and highlights[-1]['color']==hi['color']:
                        highlights[-1]['end']=offset; highlights[-1]['text']+=seg
                    else:
                        highlights.append({'start':st,'end':offset,'text':seg,'name':hi['name'],'color':hi['color']})
    flush()
    if not parts: parts=[{'kind':'text','text':p.text or '','html':html.escape(p.text or ''),'highlights':[]}]
    return parts

def has_page_break(p:Paragraph)->bool:
    return bool(p._p.xpath('.//w:br[@w:type="page"]')) or bool(p._p.xpath('./w:pPr/w:pageBreakBefore'))

def parse_docx(path:Path, chapter_heading:str='heading1', *, image_store=None):
    doc=Document(path); entries=[]
    for item in iter_block_items(doc):
        if isinstance(item,Table):
            text='\n'.join('\t'.join(c.text for c in row.cells) for row in item.rows); entries.append({'kind':'paragraph','text':text,'html':html.escape(text),'style':'body','page_break':False})
            continue
        p=item; style=(p.style.name or '').lower() if p.style else ''; semantic='body'
        heading_match=re.match(r'^heading\s+([1-6])(?:\b|$)',style)
        if heading_match: semantic=f'heading{heading_match.group(1)}'
        elif style=='title': semantic='title'
        parts=paragraph_parts(p,image_store=image_store); first=True
        for part in parts:
            if part['kind']=='text': entries.append({'kind':'paragraph','text':part['text'],'html':part['html'],'highlights':part.get('highlights',[]),'style':semantic,'page_break':has_page_break(p) if first else False})
            else: entries.append({'kind':'image','image_path':part['image_path'],'text':'','html':'','highlights':[],'style':'image','page_break':False})
            first=False
    # Google Docs "all tabs" DOCX exports tab title divider pages. We preserve explicit section/page title dividers as Source Tabs when detectable.
    tab_markers=[]
    for i,e in enumerate(entries):
        if e['kind']=='paragraph' and e['text'].strip() and e['style']=='title' and e['page_break']: tab_markers.append(i)
    tabs=[]
    if len(tab_markers)>=2:
        for j,idx in enumerate(tab_markers):
            start=idx+1; end=tab_markers[j+1] if j+1<len(tab_markers) else len(entries); tabs.append({'title':entries[idx]['text'].strip(), 'entries':entries[start:end]})
    else: tabs=[{'title':'Manuscript','entries':entries}]
    # The selected source heading is the Chapter level. Breadmaker's H1/H2/H3
    # are always sublevels *inside* a Chapter, so deeper DOCX headings are
    # normalized relative to the selected Chapter heading rather than competing
    # with the Chapter title at the same semantic level.
    chapter_level=int(chapter_heading[-1])
    for tab in tabs:
        chapters=[]; current=None
        for original in tab['entries']:
            e=dict(original)
            if e['kind']=='paragraph' and e['style']==chapter_heading and e['text'].strip():
                if current: chapters.append(current)
                current={'title':e['text'].strip(),'blocks':[]}; continue
            if e['kind']=='paragraph' and e.get('style','').startswith('heading') and e['style'][-1:].isdigit():
                source_level=int(e['style'][-1]); relative=source_level-chapter_level
                e['style']=f'heading{min(4,relative+1)}' if relative>0 else 'body'
            if current is None: current={'title':'Chapter 1' if len(tabs)==1 else tab['title'],'blocks':[]}
            current['blocks'].append(e)
        if current: chapters.append(current)
        if not chapters: chapters=[{'title':tab['title'] or 'Chapter 1','blocks':[]}]
        tab['chapters']=chapters
    return tabs
