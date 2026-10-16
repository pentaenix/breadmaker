from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REVIEWS=ROOT/'src'/'api'/'reviews'


def test_v10217_reviews_is_a_real_domain_package_without_compatibility_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,17)
    assert REVIEWS.is_dir()
    assert not (ROOT/'src/api/reviews.py').exists()
    expected={'domain.py','snapshots.py','presets.py','portable.py','reader.py','questionnaires.py','feedback.py','participants.py','markers.py','lifecycle.py','__init__.py','README.md'}
    assert expected <= {p.name for p in REVIEWS.iterdir()}
    init=(REVIEWS/'__init__.py').read_text(encoding='utf-8')
    tree=ast.parse(init)
    assert not [node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in init


def test_v10217_reviews_owners_are_bounded_and_cross_feature_edges_target_real_owners():
    implementation=[p for p in REVIEWS.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in implementation) < 650
    manuscript=(ROOT/'src/api/manuscript/document.py').read_text(encoding='utf-8')
    request_pipeline=(ROOT/'src/backend/request_pipeline.py').read_text(encoding='utf-8')
    runtime_routes=(ROOT/'src/backend/runtime_routes.py').read_text(encoding='utf-8')
    assert "feature_call('reviews.lifecycle', '_review_rows')" in manuscript
    assert "feature_call('reviews.portable','_prune_expired_temporary_portable_reviews')" in request_pipeline
    assert "feature_call('reviews.portable','_cleanup_temporary_portable_review')" in runtime_routes


def test_v10217_route_map_records_review_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    review=next(domain for domain in manifest['domains'] if domain['module']=='api.reviews')
    assert review['file']=='src/api/reviews/__init__.py'
    assert len(review['routes'])==76
    files={route['file'] for route in review['routes']}
    assert 'src/api/reviews/lifecycle.py' in files
    assert 'src/api/reviews/portable.py' in files
    assert 'src/api/reviews/feedback.py' in files
    assert all(path.startswith('src/api/reviews/') for path in files)


def test_v10217_tests_target_canonical_review_owners():
    v8140=(ROOT/'tests/test_v8140.py').read_text(encoding='utf-8')
    v963=(ROOT/'tests/test_v963.py').read_text(encoding='utf-8')
    v9613=(ROOT/'tests/test_v9613.py').read_text(encoding='utf-8')
    assert 'from api.reviews import domain as reviews_domain' in v8140
    assert 'from api.reviews import portable as portable_reviews' in v963
    assert 'from api.reviews import portable as portable_reviews' in v9613
    assert 'from api import reviews as reviews_api' not in (v8140+v963+v9613)
