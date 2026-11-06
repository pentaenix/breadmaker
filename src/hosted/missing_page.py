"""Branded HTML for unmatched browser pages; API/auth boundaries stay unchanged."""
from functools import lru_cache
from html import escape
import json
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException
from backend.environment import APP_VERSION, LOCALES

_CODES=('en','es','fr','it','ja','pt-BR','zh-CN')
_FALLBACK={'title':'Page not found','message':'This link may be outdated. Return to your writing space to continue.','return':'Return to Breadmaker'}


def _preferences(value):
    choices=[]
    for index,part in enumerate(value.split(',')):
        fields=part.strip().split(';');quality=1.0
        for option in fields[1:]:
            if option.strip().startswith('q='):
                try:quality=float(option.strip()[2:])
                except ValueError:quality=0
        if quality>0:choices.append((-quality,index,fields[0].strip().lower()))
    return [item[2] for item in sorted(choices)]


@lru_cache(maxsize=7)
def _copy(code):
    try:strings=json.loads((LOCALES/f'{code}.json').read_text(encoding='utf-8'))['strings']
    except (OSError,ValueError,KeyError):strings={}
    return {key:str(strings.get('missing_page.'+key,value)) for key,value in _FALLBACK.items()}


def _language(header):
    for wanted in _preferences(header):
        for code in _CODES:
            if code.lower()==wanted:return code
        for code in _CODES:
            if code.lower().split('-')[0]==wanted.split('-')[0]:return code
    return 'en'


async def missing_page(request,exc):
    path=request.url.path
    if (exc.status_code!=404 or request.scope.get('route') is not None
        or request.method not in {'GET','HEAD'} or path in {'/api','/static','/locales'}
        or path.startswith(('/api/','/static/','/locales/'))
        or 'text/html' not in _preferences(request.headers.get('accept',''))):
        return await http_exception_handler(request,exc)
    code=_language(request.headers.get('accept-language',''));copy=_copy(code)
    title,message,back=(escape(copy[key]) for key in ('title','message','return'))
    version=escape(APP_VERSION,quote=True)
    page=(f'<!doctype html><html lang="{code}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<title>{title} · Breadmaker</title>'
        f'<link rel="icon" type="image/svg+xml" href="/static/breadmaker_logo.svg?v={version}">'
        f'<link rel="stylesheet" href="/static/css/account/identity-platform.css?v={version}">'
        f'<link rel="stylesheet" href="/static/css/shared/missing-page.css?v={version}">'
        '</head><body><div class="identity-login"><header class="identity-header">'
        '<a class="identity-brand" href="https://breadmaker.studio" aria-label="Breadmaker">'
        '<span class="identity-logo" aria-hidden="true"></span><strong>Breadmaker</strong></a></header>'
        '<main class="identity-center"><div class="identity-stack"><section class="identity-card">'
        f'<p class="missing-page-code">404</p><h1>{title}</h1><p class="missing-page-message">{message}</p>'
        f'<a class="identity-primary missing-page-return" href="/">{back}</a>'
        '</section></div></main></div></body></html>')
    return HTMLResponse(page,status_code=404,headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex','Vary':'Accept, Accept-Language'})


def install(app):
    app.add_exception_handler(HTTPException,missing_page)
