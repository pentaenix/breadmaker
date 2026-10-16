from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
INDEX=(ROOT/'static/index.html').read_text(encoding='utf-8')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))

def test_v712_release_and_avatar_size():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,1,2)
    version=(ROOT/'VERSION').read_text().strip()
    assert '.user-avatar{width:24px;height:24px;' in CSS
    assert '.user-avatar svg{display:block;width:100%;height:100%}' in CSS
