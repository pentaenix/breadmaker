from __future__ import annotations

"""Runtime-independent Word rendering for manuscript and readable ZIP exports."""

from io import BytesIO

from bs4 import BeautifulSoup, NavigableString
from security.content_security import sanitize_inline_html

def manuscript_block_groups(blocks:list[dict]):
    """Yield one prose/image block or one validated adjacent image row at a time."""
    ordered=list(blocks);i=0
    while i<len(ordered):
        block=ordered[i];row_id=str(block.get('image_row_id') or '').strip() if block.get('block_type')=='image' else ''
        if row_id:
            group=[block];j=i+1
            while j<len(ordered) and ordered[j].get('block_type')=='image' and str(ordered[j].get('image_row_id') or '').strip()==row_id:
                group.append(ordered[j]);j+=1
            if 2<=len(group)<=4:
                yield group;i=j;continue
        yield [block];i+=1


def _inline(paragraph, fragment, text):
    soup=BeautifulSoup(sanitize_inline_html(fragment or '', text or ''),'html.parser')
    def walk(node,bold=False,italic=False,underline=False):
        if isinstance(node,NavigableString):
            run=paragraph.add_run(str(node));run.bold=bold or None;run.italic=italic or None;run.underline=underline or None
        elif node.name=='br':
            paragraph.add_run().add_break()
        else:
            for child in node.children:
                walk(child,bold or node.name in {'b','strong'},italic or node.name in {'i','em'},underline or node.name=='u')
    for child in soup.contents:walk(child)


def docx_bytes(title, tabs, chapters, blocks, read_asset, *, image_placeholder='[Image unavailable]'):
    # Keep the dependency off normal local editor startup.
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc=Document();doc.core_properties.title=str(title)
    grouped={chapter['id']:[] for chapter in chapters}
    for block in blocks:grouped.setdefault(block['chapter_id'],[]).append(block)
    def image(paragraph,block,width):
        paragraph.alignment=WD_ALIGN_PARAGRAPH.CENTER
        try:
            payload=read_asset(str(block.get('image_path') or ''))
            if not payload:raise ValueError('Image unavailable')
            shape=paragraph.add_run().add_picture(BytesIO(payload),width=max(1,int(width)))
            shape._inline.docPr.set('descr',str(block.get('image_description') or ''))
        except Exception:
            paragraph.add_run(image_placeholder)
    for ti,tab in enumerate(tabs):
        if len(tabs)>1 and not tab.get('is_manuscript'):doc.add_heading(str(tab.get('title') or 'Document'),level=1)
        wrote=False
        for chapter in (ch for ch in chapters if ch['tab_id']==tab['id']):
            chapter_blocks=grouped.get(chapter['id'],[])
            if chapter.get('is_implicit') and not any(b.get('block_type')=='image' or str(b.get('text_content') or '').strip() for b in chapter_blocks):continue
            if wrote:doc.add_page_break()
            if not chapter.get('is_implicit'):doc.add_heading(str(chapter.get('title') or ''),level=1)
            section=doc.sections[-1];usable=int(section.page_width)-int(section.left_margin)-int(section.right_margin)
            for group in manuscript_block_groups(chapter_blocks):
                if len(group)>1:
                    width=usable*max(5,min(100,int(group[0].get('image_width') or 100)))/100
                    table=doc.add_table(rows=1,cols=len(group));table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False
                    for index,block in enumerate(group):
                        cell=table.cell(0,index);cell.width=int(width/len(group));cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.TOP
                        image(cell.paragraphs[0],block,width/len(group)*.9)
                        if block.get('image_footer'):
                            p=cell.add_paragraph(str(block['image_footer']));p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                    continue
                block=group[0]
                if block.get('block_type')=='image':
                    image(doc.add_paragraph(),block,usable*max(5,min(100,int(block.get('image_width') or 100)))/100)
                    if block.get('image_footer'):
                        p=doc.add_paragraph(str(block['image_footer']));p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                    continue
                style=str(block.get('semantic_style') or '')
                paragraph=doc.add_paragraph(style='Heading '+style[-1] if style in {'heading2','heading3','heading4'} else None)
                _inline(paragraph,block.get('content_html'),block.get('text_content'))
            wrote=True
        if ti<len(tabs)-1:doc.add_page_break()
    out=BytesIO();doc.save(out);return out.getvalue()
