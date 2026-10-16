from __future__ import annotations

import ast
import re
from pathlib import Path

from source_contracts import frontend_styles_source, frontend_stylesheet_hrefs, frontend_stylesheet_paths

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'

EXPECTED_CSS=[
    '/static/styles.css',
    '/static/css/shared/workspace-foundations.css',
    '/static/css/canvas/navigation-and-legend.css',
    '/static/css/projects/center-interactions.css',
    '/static/css/canvas/object-presentation.css',
    '/static/css/maps/workspace.css',
    '/static/css/manuscript/editor-tools.css',
    '/static/css/projects/history-verification-export.css',
    '/static/css/canvas/configuration.css',
    '/static/css/projects/metadata-danger.css',
    '/static/css/manuscript/workspace.css',
    '/static/css/projects/settings.css',
    '/static/css/canvas/workspace.css',
    '/static/css/projects/import-browse.css',
    '/static/css/maps/presentation.css',
    '/static/css/shared/application-controls.css',
    '/static/css/projects/recovery-backups.css',
    '/static/css/reviews/workspace.css',
    '/static/css/manuscript/media-and-split.css',
    '/static/css/reviews/manuscript-layer.css',
    '/static/css/profile/local-profile.css',
    '/static/css/profile/account-security.css',
    '/static/css/shared/startup-hydration.css',
]


def test_v1036_release_identity_and_ordered_stylesheet_boundary():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,6)
    hrefs=frontend_stylesheet_hrefs(ROOT)
    assert [href.split('?',1)[0] for href in hrefs]==EXPECTED_CSS
    assert [f'/{path.relative_to(ROOT).as_posix()}' for path in frontend_stylesheet_paths(ROOT)]==EXPECTED_CSS


def test_v1036_css_is_owned_without_compatibility_facade_or_import_loader():
    foundation=(STATIC/'styles.css').read_text(encoding='utf-8')
    assert len(foundation.splitlines()) <= 24
    assert '@import' not in foundation
    assert ':root{' in foundation
    for directory in ('shared','canvas','projects','maps','manuscript','reviews','profile'):
        assert (STATIC/'css'/directory).is_dir()
    css=frontend_styles_source(ROOT)
    assert '.manuscript-layout' in css
    assert '.canvas-layout' in css
    assert '.maps-layout' in css
    assert '.review-sidebar' in css
    assert '.danger-zone' in css


def test_v1036_historical_css_contracts_follow_runtime_boundary():
    direct=[]
    for path in (ROOT/'tests').glob('test_*.py'):
        if path.name=='test_v1036.py':
            continue
        source=path.read_text(encoding='utf-8')
        # A file that never mentions the legacy foundation path cannot contain a
        # forbidden direct styles.css read. This prefilter preserves the contract
        # while avoiding hundreds of unnecessary AST parses under verification.
        if 'styles.css' not in source:
            continue
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'read_text','read'}:
                target=ast.get_source_segment(source,node.func.value) or ''
                if 'styles.css' in target and 'frontend_styles_source_from_styles_path' not in target:
                    direct.append(path.name)
                    break
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='open':
                segment=ast.get_source_segment(source,node) or ''
                if 'styles.css' in segment:
                    direct.append(path.name)
                    break
    assert sorted(set(direct))==[]


def test_v1036_css_ownership_is_documented():
    docs=(STATIC/'css'/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'Stylesheet order is runtime behavior' in docs
    assert '`static/styles.css` remains the global foundation owner' in docs
    assert 'frontend_styles_source()' in docs
    assert 'ordered CSS ownership' in architecture
