from __future__ import annotations

from io import BytesIO

import html
import os
import re
import tempfile
from pathlib import Path
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.dependencies import feature_call
from backend.environment import _HOSTED_LIMITS, runtime
from workspace.filesystem import safe_name
from api.import_export.contracts import PDFExportOptions
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations

from .docx_export import _manuscript_block_groups

_primary_manuscript_tab = feature_call('checkpoints.capture', '_primary_manuscript_tab')

router=APIRouter()

def _pdf_inline_html(raw_html:str, fallback:str='') -> str:
    soup=BeautifulSoup(raw_html or html.escape(fallback or ''),'html.parser')
    allowed={'b','strong','i','em','u','br'}
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed: tag.unwrap()
        else: tag.attrs={}
    text=''.join(str(x) for x in soup.contents).replace('\t','    ')
    return text or '&nbsp;'

def _pdf_export_book(path:Path, title:str, options:PDFExportOptions, chapters:list[dict], blocks:list[dict]) -> None:
    # PDF generation is an explicit export action; keep ReportLab off the normal
    # Breadmaker startup path.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image as RLImage, KeepTogether, PageBreak, Paragraph as RLParagraph, SimpleDocTemplate, Spacer, Table as RLTable, TableStyle
    page_size=A4 if options.page_size.lower()=='a4' else LETTER
    font_map={
        'book-serif':('Times-Roman','Times-Bold','Times-Italic'),
        'clean-sans':('Helvetica','Helvetica-Bold','Helvetica-Oblique'),
        'proof-mono':('Courier','Courier-Bold','Courier-Oblique'),
    }
    body_font,bold_font,italic_font=font_map.get(options.font_type,font_map['book-serif'])
    size=max(9,min(15,int(options.font_size or 11)))
    styles=getSampleStyleSheet()
    body=ParagraphStyle('BreadmakerBody',parent=styles['BodyText'],fontName=body_font,fontSize=size,leading=size*1.48,spaceAfter=size*.58,textColor=colors.HexColor('#151515'))
    chapter_style=ParagraphStyle('BreadmakerChapter',parent=body,fontName=bold_font,fontSize=size*1.72,leading=size*2.05,spaceBefore=size*.8,spaceAfter=size*1.2,keepWithNext=True)
    h2=ParagraphStyle('BreadmakerH2',parent=body,fontName=bold_font,fontSize=size*1.28,leading=size*1.55,spaceBefore=size*1.25,spaceAfter=size*.55,keepWithNext=True)
    h3=ParagraphStyle('BreadmakerH3',parent=body,fontName=bold_font,fontSize=size*1.13,leading=size*1.42,spaceBefore=size,spaceAfter=size*.4,keepWithNext=True)
    h4=ParagraphStyle('BreadmakerH4',parent=body,fontName=bold_font,fontSize=size*1.04,leading=size*1.38,spaceBefore=size*.85,spaceAfter=size*.35,keepWithNext=True)
    cover_title=ParagraphStyle('BreadmakerCover',parent=body,fontName=bold_font,fontSize=30,leading=36,alignment=TA_CENTER,spaceAfter=16)
    cover_author=ParagraphStyle('BreadmakerCoverAuthor',parent=body,fontName=body_font,fontSize=15,leading=20,alignment=TA_CENTER,spaceAfter=10,textColor=colors.HexColor('#333333'))
    cover_meta=ParagraphStyle('BreadmakerCoverMeta',parent=body,fontName=body_font,fontSize=10.5,leading=15,alignment=TA_CENTER,spaceAfter=8,textColor=colors.HexColor('#666666'))
    cover_notes=ParagraphStyle('BreadmakerCoverNotes',parent=body,fontName=italic_font,fontSize=10.5,leading=15,alignment=TA_CENTER,spaceBefore=18,textColor=colors.HexColor('#5e5e5e'))
    doc=SimpleDocTemplate(str(path),pagesize=page_size,rightMargin=.78*inch,leftMargin=.78*inch,topMargin=.82*inch,bottomMargin=.78*inch,title=title,author=(options.author.strip() or 'Breadmaker'))
    image_footer=ParagraphStyle('BreadmakerImageFooter',parent=body,fontName=body_font,fontSize=max(8,size*.88),leading=max(11,size*1.18),alignment=TA_CENTER,leftIndent=doc.width*.07,rightIndent=doc.width*.07,spaceBefore=size*.2,spaceAfter=size*.45,textColor=colors.HexColor('#666666'))
    image_row_footer=ParagraphStyle('BreadmakerImageRowFooter',parent=image_footer,leftIndent=0,rightIndent=0,spaceAfter=size*.2)
    by_ch={}
    for b in blocks: by_ch.setdefault(int(b.get('chapter_id') or 0),[]).append(b)
    story=[]
    if options.cover:
        story.extend([Spacer(1,1.78*inch),RLParagraph(html.escape(options.title.strip() or title),cover_title)])
        if options.author.strip(): story.append(RLParagraph(html.escape(options.author.strip()),cover_author))
        meta=[]
        if options.version.strip(): meta.append(f"Version {html.escape(options.version.strip())}")
        if options.cover_date.strip(): meta.append(html.escape(options.cover_date.strip()))
        if meta: story.append(RLParagraph(' &nbsp;&middot;&nbsp; '.join(meta),cover_meta))
        if options.notes.strip():
            notes=html.escape(options.notes.strip()).replace('\n','<br/>')
            story.extend([Spacer(1,.28*inch),RLParagraph(notes,cover_notes)])
        story.append(PageBreak())
    wrote_segment=False
    for ch in chapters:
        chapter_blocks=sorted(by_ch.get(int(ch.get('id') or 0),[]),key=lambda x:(int(x.get('position') or 0),str(x.get('id') or '')))
        if ch.get('is_implicit') and not any(b.get('block_type')=='image' or str(b.get('text_content') or '').strip() for b in chapter_blocks):
            continue
        if wrote_segment and options.chapter_page_breaks: story.append(PageBreak())
        if not ch.get('is_implicit'):
            story.append(RLParagraph(html.escape(str(ch.get('title') or 'Untitled Chapter')),chapter_style))
        for group in _manuscript_block_groups(chapter_blocks):
            if len(group)>1 and all(b.get('block_type')=='image' and b.get('image_path') for b in group):
                row_width=max(5,min(100,int(group[0].get('image_width') or 100)));total_width=doc.width*row_width/100;cell_width=total_width/len(group);cells=[]
                for b in group:
                    cell=[];asset_name=Path(str(b.get('image_path') or '')).name
                    if runtime.assets.exists(asset_name):
                        if _HOSTED_LIMITS is not None:
                            cell.append(RLParagraph('[Image]',body))
                        else:
                            try:
                                img=RLImage(BytesIO(runtime.assets.read_blob(asset_name)));target_width=max(12,cell_width-10);ratio=(img.imageHeight/img.imageWidth) if img.imageWidth else 1;target_height=target_width*ratio;maxh=4.8*inch
                                if target_height>maxh:target_height=maxh;target_width=maxh/ratio if ratio else target_width
                                img.drawWidth=target_width;img.drawHeight=target_height;img.hAlign='CENTER';cell.append(img)
                            except Exception: cell.append(RLParagraph('[Image]',body))
                    footer=str(b.get('image_footer') or '').strip()
                    if footer:cell.append(RLParagraph(html.escape(footer).replace('\n','<br/>'),image_row_footer))
                    cells.append(cell or [Spacer(1,1)])
                table=RLTable([cells],colWidths=[cell_width]*len(group),hAlign='CENTER')
                table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
                story.append(KeepTogether([Spacer(1,size*.45),table,Spacer(1,size*.35)]));continue
            b=group[0]
            if b.get('block_type')=='image' and b.get('image_path'):
                asset_name=Path(str(b.get('image_path'))).name
                if runtime.assets.exists(asset_name):
                    if _HOSTED_LIMITS is not None:
                        story.append(RLParagraph('[Image]',body))
                        footer=str(b.get('image_footer') or '').strip()
                        if footer: story.append(RLParagraph(html.escape(footer).replace('\n','<br/>'),image_footer))
                    else:
                        try:
                            img=RLImage(BytesIO(runtime.assets.read_blob(asset_name)));width_percent=max(5,min(100,int(b.get('image_width') or 100)));target_width=doc.width*width_percent/100;maxh=4.8*inch
                            ratio=(img.imageHeight/img.imageWidth) if img.imageWidth else 1;target_height=target_width*ratio
                            if target_height>maxh:
                                target_height=maxh;target_width=maxh/ratio if ratio else target_width
                            img.drawWidth=target_width;img.drawHeight=target_height;img.hAlign='CENTER'
                            story.extend([Spacer(1,size*.45),img])
                            footer=str(b.get('image_footer') or '').strip()
                            if footer: story.append(RLParagraph(html.escape(footer).replace('\n','<br/>'),image_footer))
                            else: story.append(Spacer(1,size*.55))
                        except Exception: story.append(RLParagraph('[Image]',body))
                continue
            style={'heading2':h2,'heading3':h3,'heading4':h4}.get(str(b.get('semantic_style') or ''),body)
            story.append(RLParagraph(_pdf_inline_html(str(b.get('content_html') or ''),str(b.get('text_content') or '')),style))
        wrote_segment=True
    def decorate(canvas,doc_obj):
        page=canvas.getPageNumber(); is_cover=bool(options.cover and page==1); width,height=page_size
        canvas.saveState()
        if not is_cover and options.header.strip():
            canvas.setFont(body_font,8.5); canvas.setFillColor(colors.HexColor('#666666')); canvas.drawCentredString(width/2,height-.43*inch,options.header.strip()[:180])
        if not is_cover and options.page_numbering:
            displayed=page-1 if options.cover else page; canvas.setFont(body_font,8.5); canvas.setFillColor(colors.HexColor('#666666')); canvas.drawCentredString(width/2,.38*inch,str(displayed))
        if not is_cover and options.watermark.strip():
            mark=options.watermark.strip()[:80]
            # A watermark should read as background, never as manuscript content.
            # Use a pale fallback color as well as transparency so renderers that
            # ignore PDF alpha still produce a restrained mark.
            try: canvas.setFillAlpha(.065)
            except Exception: pass
            canvas.setFillColor(colors.HexColor('#D8D8D8'))
            watermark_size=max(44,min(76,int(76-(max(0,len(mark)-12)*1.15))))
            canvas.setFont(bold_font,watermark_size); canvas.translate(width/2,height/2); canvas.rotate(38); canvas.drawCentredString(0,0,mark)
        canvas.restoreState()
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)

@router.post("/api/export/pdf")
def export_pdf(options:PDFExportOptions):
    with storage_mutations.tx() as con:
        p=project(con); tab=_primary_manuscript_tab(con,p['id'])
        chapters=allrows(con,"SELECT id,position,title,is_implicit FROM chapters WHERE project_id=? AND tab_id=? ORDER BY position,id",(p['id'],tab['id']))
        ids=[c['id'] for c in chapters]; blocks=[]
        if ids:
            q=','.join('?' for _ in ids); blocks=allrows(con,f"SELECT id,chapter_id,position,block_type,semantic_style,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id FROM manuscript_blocks WHERE chapter_id IN ({q}) ORDER BY chapter_id,position,id",ids)
        project_title=str(p.get('title') or 'Book')
    fd,tmp_name=tempfile.mkstemp(prefix='breadmaker-pdf-',suffix='.pdf'); os.close(fd); out=Path(tmp_name)
    try: _pdf_export_book(out,project_title,options,chapters,blocks)
    except Exception:
        out.unlink(missing_ok=True); raise
    filename=safe_name(options.title.strip() or project_title)+'.pdf'
    return FileResponse(out,filename=filename,media_type='application/pdf',background=BackgroundTask(out.unlink,missing_ok=True))
