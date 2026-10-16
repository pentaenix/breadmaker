from __future__ import annotations

from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values
from source_contracts import backend_source

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _review_manifest(*, content_html: str, text_content: str = 'Safe text') -> dict:
    from formats import breadreview
    return breadreview.seal({
        'package_id': 'breadreview_security_fixture',
        'project_id': 'a' * 32,
        'review_id': 'review_security_fixture',
        'snapshot_id': 'snapshot_security_fixture',
        'participant_id': 'participant_security_fixture',
        'review': {'id': 'review_security_fixture', 'name': 'Security fixture', 'status': 'open'},
        'participant': {
            'id': 'participant_security_fixture',
            'review_id': 'review_security_fixture',
            'display_name': 'Reviewer',
            'status': 'not_started',
        },
        'snapshot': {
            'id': 'snapshot_security_fixture',
            'chapters': [],
            'scenes': [],
            'blocks': [{
                'source_block_id': 'block_security_fixture',
                'block_type': 'paragraph',
                'semantic_style': 'body',
                'content_html': content_html,
                'text_content': text_content,
                'image_path': '',
            }],
        },
        'feedback': {
            'anchors': [], 'highlights': [], 'comments': [], 'suggestions': [],
            'answers': [], 'events': [],
        },
        'questions': [],
        'assets': {},
    })


def _write_attacker_breadreview(path: Path, manifest: dict) -> None:
    """Build bytes without Breadmaker's writer so the test models an attacker."""
    from formats import breadreview
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(breadreview.MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
    path.write_bytes(breadreview._encode_envelope(inner.getvalue(), None))


def test_v963_release_critical_security_contracts_are_explicit():
    version = ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int, version.split('.'))) >= (9, 6, 3)

    app = backend_source(ROOT)
    workspace = ROOT.joinpath('src','workspace','projects','packages.py').read_text(encoding='utf-8')
    breadreview = ROOT.joinpath('src','formats','breadreview.py').read_text(encoding='utf-8')
    verifier = ROOT.joinpath('scripts', 'verify_release.py').read_text(encoding='utf-8')

    # Detached package state is keyed by the opaque server session, never one process
    # global package/passphrase shared by all authenticated users.
    assert '_TEMP_PORTABLE_REVIEWS: dict[str,_TemporaryPortableReviewState]' in app
    assert "return f'session:{principal.session_id}'" in app
    assert '_TEMP_PORTABLE_REVIEW_PATH' not in app
    assert '_TEMP_PORTABLE_REVIEW_PASSPHRASE' not in app
    assert 'PORTABLE_REVIEW_STANDALONE_FORBIDDEN_WITH_ACCOUNT_SESSIONS' in app

    # import-current is project-context-free only while inspecting the detached file;
    # the write transaction must be explicitly rebound to the authenticated project.
    route = app[app.index('def portable_review_import_current():'):app.index("@app.get('/api/portable-review/status')")]
    assert "with runtime.context.bind(target_project_id,user_id=user_id or None):" in route
    assert "_require_project_permission('review.process',target_project_id)" in route
    assert route.index('runtime.context.bind') < route.index("storage_mutations.tx(operation='breadreview-import')")

    # Package hashes are not a trust boundary. Both project and Review import readers
    # independently reject non-canonical/active HTML before it reaches innerHTML.
    assert 'content_security.find_unsafe_project_html(check)' in workspace
    assert 'PROJECT_PACKAGE_UNSAFE_HTML' in workspace
    assert 'content_security.find_unsafe_review_snapshot_html(snapshot)' in breadreview
    assert 'BREADREVIEW_043' in breadreview

    # The release command must actually discover the 9.x contracts that prove these
    # boundaries; a green verifier that stops at 8.x is not a security gate.
    assert 'TESTS.glob("test_v9*.py")' in verifier
    assert 'TESTS.glob("test_v9*_browser.py")' in verifier


def test_v963_temporary_portable_reviews_are_isolated_by_session(app_module_fixture, monkeypatch, tmp_path):
    from auth.sessions import SessionPrincipal
    from backend import portable_review_runtime
    from backend.environment import runtime
    from runtime.native import require_native_projects

    from api.reviews import portable as portable_reviews
    assert portable_review_runtime.current_portable_review_path() is None
    sessions = runtime.sessions
    current = [SessionPrincipal('ses_security_a', 'usr_a', 'test', 1.0, 9999999999.0)]
    monkeypatch.setattr(sessions, 'requires_authentication', True, raising=False)
    monkeypatch.setattr(sessions, 'current', lambda: current[0])

    with portable_reviews._TEMP_PORTABLE_REVIEW_LOCK:
        portable_reviews._TEMP_PORTABLE_REVIEWS.clear()

    root_a = tmp_path / 'a'; root_a.mkdir(); path_a = root_a / 'a.breadreview'; path_a.write_bytes(b'a')
    portable_reviews._install_temporary_portable_review(path_a, root_a, path_a.name)
    portable_reviews._set_temporary_portable_passphrase('session-a-secret')
    assert portable_reviews._active_portable_review_path() == path_a
    assert portable_reviews._active_portable_passphrase() == 'session-a-secret'

    current[0] = SessionPrincipal('ses_security_b', 'usr_b', 'test', 1.0, 9999999999.0)
    assert portable_reviews._active_portable_review_path() is None
    assert portable_reviews._active_portable_passphrase() is None
    assert portable_reviews._temporary_portable_review_active() is False

    root_b = tmp_path / 'b'; root_b.mkdir(); path_b = root_b / 'b.breadreview'; path_b.write_bytes(b'b')
    portable_reviews._install_temporary_portable_review(path_b, root_b, path_b.name)
    portable_reviews._set_temporary_portable_passphrase('session-b-secret')
    assert portable_reviews._active_portable_review_path() == path_b
    assert portable_reviews._active_portable_passphrase() == 'session-b-secret'

    current[0] = SessionPrincipal('ses_security_a', 'usr_a', 'test', 1.0, 9999999999.0)
    assert portable_reviews._active_portable_review_path() == path_a
    assert portable_reviews._active_portable_passphrase() == 'session-a-secret'
    portable_reviews._cleanup_temporary_portable_review()
    current[0] = SessionPrincipal('ses_security_b', 'usr_b', 'test', 1.0, 9999999999.0)
    portable_reviews._cleanup_temporary_portable_review()


def test_v963_attacker_breadreview_with_recomputed_payload_hash_is_rejected(tmp_path):
    from formats import breadreview
    malicious = '<strong onclick="alert(1)">Safe text</strong><img src=x onerror="alert(2)">'
    manifest = _review_manifest(content_html=malicious)
    # seal() gave the attacker a completely valid integrity hash for the malicious bytes.
    assert manifest['payload_hash'] == breadreview.payload_hash(manifest)

    package = tmp_path / 'crafted.breadreview'
    _write_attacker_breadreview(package, manifest)
    with pytest.raises(breadreview.BreadReviewError) as caught:
        breadreview.read(package)
    assert caught.value.code == 'BREADREVIEW_043'


def test_v963_legitimate_breadreview_inline_formatting_still_validates(tmp_path):
    from formats import breadreview
    manifest = _review_manifest(content_html='<strong>Safe text</strong>')
    package = tmp_path / 'safe.breadreview'
    _write_attacker_breadreview(package, manifest)
    loaded = breadreview.read(package)
    assert loaded['snapshot']['blocks'][0]['content_html'] == '<strong>Safe text</strong>'


@pytest.mark.parametrize(
    ('surface', 'schema', 'insert_sql', 'row'),
    [
        (
            'manuscript',
            'CREATE TABLE manuscript_blocks(id TEXT, block_type TEXT, content_html TEXT, text_content TEXT)',
            'INSERT INTO manuscript_blocks VALUES(?,?,?,?)',
            ('m1', 'paragraph', '<em onmouseover="alert(1)">Safe</em>', 'Safe'),
        ),
        (
            'review_snapshot',
            'CREATE TABLE review_snapshot_blocks(id TEXT, block_type TEXT, content_html TEXT, text_content TEXT)',
            'INSERT INTO review_snapshot_blocks VALUES(?,?,?,?)',
            ('r1', 'paragraph', '<svg onload="alert(1)"></svg>Safe', 'Safe'),
        ),
        (
            'cut',
            'CREATE TABLE cuts(id TEXT, content_html TEXT)',
            'INSERT INTO cuts VALUES(?,?)',
            ('c1', '<p onclick="alert(1)">Safe</p>'),
        ),
    ],
)
def test_v963_project_import_validator_covers_every_innerhtml_surface(surface, schema, insert_sql, row):
    from security import content_security
    con = sqlite3.connect(':memory:')
    try:
        con.execute(schema)
        con.execute(insert_sql, row)
        finding = content_security.find_unsafe_project_html(con)
    finally:
        con.close()
    assert finding is not None
    assert finding.surface == surface



def test_v963_package_validator_accepts_harmless_historical_wrappers_and_safe_cut_assets():
    from security import content_security
    con = sqlite3.connect(':memory:')
    try:
        con.execute('CREATE TABLE manuscript_blocks(id TEXT, block_type TEXT, content_html TEXT, text_content TEXT)')
        con.execute('CREATE TABLE review_snapshot_blocks(id TEXT, block_type TEXT, content_html TEXT, text_content TEXT)')
        con.execute('CREATE TABLE cuts(id TEXT, content_html TEXT)')
        con.execute('INSERT INTO manuscript_blocks VALUES(?,?,?,?)', ('m1', 'text', '<p>Safe <strong>history</strong></p>', 'Safe history'))
        con.execute('INSERT INTO review_snapshot_blocks VALUES(?,?,?,?)', ('r1', 'paragraph', '<p>Safe<br>review</p>', 'Safe\nreview'))
        con.execute('INSERT INTO cuts VALUES(?,?)', ('c1', '<figure data-width="55" data-row="row-1"><img src="/api/assets/safe.webp" alt="Safe"></figure>'))
        assert content_security.find_unsafe_project_html(con) is None
    finally:
        con.close()

    snapshot={'blocks':[{'source_block_id':'r2','block_type':'text','content_html':'<p>Safe <em>review</em></p>','text_content':'Safe review'}]}
    assert content_security.find_unsafe_review_snapshot_html(snapshot) is None

def test_v963_crafted_breadmaker_with_valid_recomputed_hashes_is_rejected(app_module_fixture, fresh_project, tmp_path):
    from revision_store.local_store import canonical_state_hash
    from workspace import WorkspaceError

    from backend.environment import runtime
    from runtime.native import require_native_projects
    project_id = str(fresh_project['project']['id'])
    source = tmp_path / 'safe.breadmaker'
    native_projects = require_native_projects(runtime)
    native_projects.export_package(project_id, source)
    assert native_projects.verify_package(source)['ok'] is True

    stage = tmp_path / 'crafted-project'; stage.mkdir()
    with zipfile.ZipFile(source) as archive:
        archive.extractall(stage)
    manifest_path = stage / 'manifest.json'
    db_path = stage / 'project.sqlite3'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    malicious = '<strong onclick="alert(1)">Owned</strong><img src=x onerror="alert(2)">'
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT id FROM manuscript_blocks WHERE block_type='paragraph' ORDER BY id LIMIT 1").fetchone()
        assert row is not None
        digest = storage_values.block_content_hash(malicious, 'Owned', '')
        con.execute(
            'UPDATE manuscript_blocks SET content_html=?,text_content=?,content_hash=? WHERE id=?',
            (malicious, 'Owned', digest, row[0]),
        )
        con.commit()
    finally:
        con.close()

    # Model an attacker who knows the public package format and recomputes every
    # integrity value after injecting HTML. The safety decision must still reject it.
    manifest['file_hashes']['project.sqlite3'] = hashlib.sha256(db_path.read_bytes()).hexdigest()
    manifest['canonical_state_hash'] = canonical_state_hash(
        db_path, storage_schema_contracts._GENERATION_CANONICAL_TABLES, manifest['file_hashes']
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

    crafted = tmp_path / 'crafted.breadmaker'
    with zipfile.ZipFile(crafted, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', manifest_path.read_text(encoding='utf-8'))
        archive.write(db_path, 'project.sqlite3')
        assets = stage / 'assets'
        if assets.is_dir():
            for path in sorted(assets.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())

    with pytest.raises(WorkspaceError, match='PROJECT_PACKAGE_UNSAFE_HTML'):
        native_projects.verify_package(crafted)
