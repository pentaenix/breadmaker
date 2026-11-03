"""Shared phone controls reuse translated labels in all supported locales."""
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]


def test_phone_rail_openers_and_backdrops_resolve_all_seven_locales():
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(), 'html.parser')
    catalogs=[json.loads((ROOT/'locales'/f'{code}.json').read_text())['strings']
              for code in ('en','es','fr','it','pt-BR','ja','zh-CN')]
    for prefix,rail in (('Book','bookNavigation'),('Canvas','canvasRail'),('Map','mapRail'),('Review','reviewRail')):
        opener=soup.select_one(f'#phone{prefix}NavBtn')
        backdrop=soup.select_one(f'#phone{prefix}NavBackdrop')
        assert opener['aria-controls']==rail and opener['aria-expanded']=='false'
        assert backdrop['tabindex']=='-1'
        for node in (opener,backdrop):
            key=re.fullmatch(r'\{\{i18n:([^}]+)\}\}',node['aria-label']).group(1)
            for catalog in catalogs:
                assert catalog[key].strip() and '{{i18n:' not in catalog[key]
