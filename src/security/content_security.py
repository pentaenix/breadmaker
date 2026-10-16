from __future__ import annotations

import html
import re
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser

from bs4 import BeautifulSoup, NavigableString

ALLOWED_INLINE = frozenset({'strong', 'b', 'em', 'i', 'u', 'br'})
CUT_ALLOWED = frozenset({'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'img', 'figure', 'figcaption'})
# Package validation is a security boundary, not a serializer-equality check. Older
# current-schema snapshots may contain an inert paragraph wrapper even though normal
# editor writes canonicalize it away.
IMPORTED_INLINE_ALLOWED = ALLOWED_INLINE | frozenset({'p'})

_SAFE_ASSET_NAME=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$')


def _safe_named_api_path(path:str,prefix:str) -> bool:
    raw=str(path or '').strip()
    if not raw.startswith(prefix):return False
    name=raw[len(prefix):]
    return bool(name and '/' not in name and '\\' not in name and '?' not in name and '#' not in name and '%' not in name and _SAFE_ASSET_NAME.fullmatch(name))


def is_safe_project_asset_path(path:str) -> bool:
    """Canonical persisted project images are same-origin content-addressed assets."""
    return _safe_named_api_path(path,'/api/assets/')


def is_safe_portable_review_asset_path(path:str) -> bool:
    """Portable Review image references may address only package-owned assets."""
    return _safe_named_api_path(path,'/api/portable-review/assets/')



@dataclass(frozen=True)
class UnsafeStoredHtml:
    surface: str
    row_id: str


def text_from_inline_html(fragment: str) -> str:
    soup = BeautifulSoup(fragment or '', 'html.parser')
    out: list[str] = []
    for node in soup.descendants:
        if isinstance(node, NavigableString):
            out.append(str(node))
        elif getattr(node, 'name', None) == 'br':
            out.append('\n')
    return ''.join(out)


def html_from_text(text: str) -> str:
    return html.escape(text or '').replace('\n', '<br>')


def sanitize_inline_html(fragment: str, text_content: str) -> str:
    """Canonicalize the small formatting subset Breadmaker renders with innerHTML.

    The searchable/exported plain text is authoritative. Active tags, attributes and
    any markup that changes that text are discarded rather than trusted.
    """
    soup = BeautifulSoup(fragment or '', 'html.parser')
    for bad in list(soup.find_all(['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math'])):
        bad.decompose()
    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_INLINE:
            tag.unwrap()
        else:
            tag.attrs = {}
    cleaned = str(soup)
    if text_from_inline_html(cleaned) != (text_content or ''):
        return html_from_text(text_content or '')
    return cleaned


def sanitize_cut_html(fragment: str) -> str:
    """Canonicalize Cut HTML before it reaches the Cut details innerHTML sink."""
    soup = BeautifulSoup(fragment or '', 'html.parser')
    for bad in list(soup.find_all(['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math'])):
        bad.decompose()
    for tag in list(soup.find_all(True)):
        if tag.name not in CUT_ALLOWED:
            tag.unwrap()
        elif tag.name == 'img':
            src = str(tag.get('src') or '')
            alt = str(tag.get('alt') or '')[:12000]
            tag.attrs = {'src': src, 'alt': alt} if is_safe_project_asset_path(src) else {}
        elif tag.name == 'figure':
            try:
                width = max(5, min(100, int(tag.get('data-width') or 100)))
            except (TypeError, ValueError):
                width = 100
            row = str(tag.get('data-row') or '').strip()[:80]
            tag.attrs = {'data-width': str(width)}
            if row:
                tag.attrs['data-row'] = row
        else:
            tag.attrs = {}

    normalized = BeautifulSoup('', 'html.parser')
    for node in list(soup.contents):
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                p = normalized.new_tag('p')
                p.string = text
                normalized.append(p)
        elif getattr(node, 'name', None) in {'p', 'img', 'figure'}:
            normalized.append(node)
        else:
            p = normalized.new_tag('p')
            p.append(node)
            normalized.append(p)
    return str(normalized)



class _StoredHtmlValidator(HTMLParser):
    """Small capability validator used only at package trust boundaries.

    Unlike BeautifulSoup-based editor sanitization this does not build a DOM. It only
    proves that imported bytes contain the inert subset Breadmaker can render safely.
    """
    def __init__(self, *, cut: bool = False):
        super().__init__(convert_charrefs=True)
        self.cut=cut
        self.safe=True
        self.text_parts: list[str]=[]

    def handle_starttag(self, tag: str, attrs) -> None:
        name=str(tag or '').lower()
        if self.cut:
            if name not in CUT_ALLOWED:self.safe=False;return
            names=[str(k).lower() for k,_ in attrs]
            if len(names)!=len(set(names)):self.safe=False;return
            values={str(k).lower():v for k,v in attrs}
            if name=='img':
                if set(values)-{'src','alt'}:self.safe=False;return
                if not is_safe_project_asset_path(str(values.get('src') or '')):self.safe=False
            elif name=='figure':
                if set(values)-{'data-width','data-row'}:self.safe=False;return
                if 'data-width' in values:
                    try:width=int(str(values.get('data-width') or ''))
                    except (TypeError,ValueError):self.safe=False;return
                    if not 5<=width<=100:self.safe=False
                if len(str(values.get('data-row') or ''))>80:self.safe=False
            elif attrs:self.safe=False
        else:
            if name not in IMPORTED_INLINE_ALLOWED or attrs:self.safe=False
            if name=='br':self.text_parts.append('\n')

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag,attrs)

    def handle_endtag(self, tag: str) -> None:
        allowed=CUT_ALLOWED if self.cut else IMPORTED_INLINE_ALLOWED
        if str(tag or '').lower() not in allowed:self.safe=False

    def handle_data(self, data: str) -> None:
        if not self.cut:self.text_parts.append(data)

    def handle_comment(self, data: str) -> None:self.safe=False
    def handle_decl(self, decl: str) -> None:self.safe=False
    def handle_pi(self, data: str) -> None:self.safe=False
    def unknown_decl(self, data: str) -> None:self.safe=False


def _validate_stored_html(fragment: str, *, cut: bool=False) -> _StoredHtmlValidator:
    parser=_StoredHtmlValidator(cut=cut)
    try:
        parser.feed(fragment or '')
        parser.close()
    except Exception:
        parser.safe=False
    return parser


def is_safe_inline_html(fragment: str, text_content: str) -> bool:
    """Validate imported prose without allocating a parser DOM for every block."""
    parser=_validate_stored_html(fragment or '')
    return parser.safe and ''.join(parser.text_parts)==(text_content or '')


def is_safe_cut_html(fragment: str) -> bool:
    """Validate persisted Cut markup by executable capability, not serializer bytes."""
    return _validate_stored_html(fragment or '',cut=True).safe

def _table_names(con: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def find_unsafe_project_html(con: sqlite3.Connection) -> UnsafeStoredHtml | None:
    """Return the first browser-active or external-resource fragment in a project DB.

    Package hashes are creator-controlled integrity metadata. Imported browser content
    therefore has to prove both script inertness *and* same-origin image provenance.
    """
    tables = _table_names(con)
    for table, surface in (
        ('manuscript_blocks', 'manuscript'),
        ('review_snapshot_blocks', 'review_snapshot'),
    ):
        if table not in tables:
            continue
        cols = {str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")')}
        if not {'id', 'block_type', 'content_html', 'text_content'} <= cols:
            continue
        has_image_path='image_path' in cols
        select='id,block_type,content_html,text_content'+(',image_path' if has_image_path else '')
        for row in con.execute(f'SELECT {select} FROM "{table}"'):
            row_id,block_type,fragment,text_content=row[:4];image_path=str(row[4] or '') if has_image_path else ''
            kind=str(block_type or '')
            raw=str(fragment or '')
            if kind=='image':
                if raw or not image_path or not is_safe_project_asset_path(image_path):
                    return UnsafeStoredHtml(surface, str(row_id or ''))
                continue
            if kind not in {'paragraph','text'} or image_path:
                return UnsafeStoredHtml(surface, str(row_id or ''))
            if raw and not is_safe_inline_html(raw, str(text_content or '')):
                return UnsafeStoredHtml(surface, str(row_id or ''))

    if 'canvas_nodes' in tables:
        cols={str(row[1]) for row in con.execute('PRAGMA table_info("canvas_nodes")')}
        if {'id','image_path'} <= cols:
            for row_id,image_path in con.execute("SELECT id,image_path FROM canvas_nodes WHERE COALESCE(image_path,'')<>''"):
                if not is_safe_project_asset_path(str(image_path or '')):
                    return UnsafeStoredHtml('canvas_image',str(row_id or ''))

    if 'cuts' in tables:
        cols = {str(row[1]) for row in con.execute('PRAGMA table_info("cuts")')}
        if {'id', 'content_html'} <= cols:
            for row_id, fragment in con.execute("SELECT id,content_html FROM cuts WHERE COALESCE(content_html,'')<>''"):
                if not is_safe_cut_html(str(fragment or '')):
                    return UnsafeStoredHtml('cut', str(row_id or ''))
    return None


def find_unsafe_review_snapshot_html(snapshot: object) -> UnsafeStoredHtml | None:
    if not isinstance(snapshot, dict):
        return None
    blocks = snapshot.get('blocks', [])
    if not isinstance(blocks, list):
        return None
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        row_id = str(block.get('source_block_id') or block.get('id') or index)
        kind=str(block.get('block_type') or 'paragraph')
        raw = str(block.get('content_html') or '')
        image_path=str(block.get('image_path') or '')
        if kind=='image':
            if raw or not image_path or not is_safe_portable_review_asset_path(image_path):
                return UnsafeStoredHtml('review_snapshot', row_id)
            continue
        if kind not in {'paragraph','text'} or image_path:
            return UnsafeStoredHtml('review_snapshot',row_id)
        if raw and not is_safe_inline_html(raw, str(block.get('text_content') or '')):
            return UnsafeStoredHtml('review_snapshot', row_id)
    return None

