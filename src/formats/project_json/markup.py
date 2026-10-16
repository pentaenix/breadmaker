from __future__ import annotations
import html,re
from bs4 import BeautifulSoup, NavigableString

_ESCAPE_SENTINELS = {"\\":"\ue000","*":"\ue001","_":"\ue002"}
_SENTINEL_REVERSE = {v:k for k,v in _ESCAPE_SENTINELS.items()}

def markup_to_html_text(markup: str) -> tuple[str,str]:
    """Parse Breadmaker Project JSON's deliberately tiny inline markup.

    Supported: **bold**, *italic*, __underline__. Backslash escapes the formatting
    characters (\\*, \\_, \\\\). Unmatched markers remain literal text.
    """
    value=str(markup or "")
    protected=[];i=0
    while i<len(value):
        if value[i]=="\\" and i+1<len(value) and value[i+1] in _ESCAPE_SENTINELS:
            protected.append(_ESCAPE_SENTINELS[value[i+1]]);i+=2
        else:protected.append(value[i]);i+=1
    safe=html.escape("".join(protected),quote=False)
    # Run strongest markers first. These regexes intentionally stay line-local and
    # non-greedy; malformed/unmatched syntax is preserved literally.
    safe=re.sub(r"\*\*([^\n*]+?)\*\*",r"<strong>\1</strong>",safe)
    safe=re.sub(r"__([^\n_]+?)__",r"<u>\1</u>",safe)
    safe=re.sub(r"(?<!\*)\*([^\n*]+?)\*(?!\*)",r"<em>\1</em>",safe)
    for sentinel,char in _SENTINEL_REVERSE.items():safe=safe.replace(sentinel,html.escape(char,quote=False))
    soup=BeautifulSoup(safe,"html.parser")
    text="".join(str(n) for n in soup.descendants if isinstance(n,NavigableString))
    return str(soup),text


def inline_html_to_markup(fragment:str,fallback:str="") -> str:
    soup=BeautifulSoup(fragment or html.escape(fallback or ""),"html.parser")
    def walk(node)->str:
        if isinstance(node,NavigableString):
            return str(node).replace("\\","\\\\").replace("*","\\*").replace("_","\\_")
        name=getattr(node,"name","")
        inner="".join(walk(ch) for ch in getattr(node,"children",[]))
        if name in {"strong","b"}: return f"**{inner}**"
        if name in {"em","i"}: return f"*{inner}*"
        if name=="u": return f"__{inner}__"
        if name=="br": return "\n"
        return inner
    return "".join(walk(ch) for ch in soup.contents)


def _content_lines(content:str)->list[tuple[str,str]]:
    # One JSON line becomes one manuscript paragraph. Empty lines are intentional
    # empty paragraphs. This makes the format readable while retaining editor blocks.
    raw_lines=str(content or "").replace("\r\n","\n").replace("\r","\n").split("\n")
    return [markup_to_html_text(line) for line in raw_lines]


# ---------- Layout ----------
