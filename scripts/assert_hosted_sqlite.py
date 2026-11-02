"""Prove the running native core, portability and removed vulnerable surfaces.

Run inside the final image, without credentials, project data or network. Fail
closed on a different library, missing provenance, shell or FTS5. The release
gate retains raw scanner evidence alongside this concrete applicability proof.
"""
from __future__ import annotations

import ctypes
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def portable_package():
    """Export a synthetic canonical document and reopen its derived local DB."""
    from hosted.local_portability import to_local_package
    pid = 'a' * 32
    def record(field, ident, **data):
        return dict(ids={'project_id': pid, field: ident}, data=dict(id=ident, **data))
    entities = {
        'document_tab': [record('tab_id', 'tab_fixture', title='Synthetic', kind='manuscript')],
        'chapter': [record('chapter_id', 'chapter_fixture', tab_id='tab_fixture',
                           position=0, title='Synthetic chapter')],
        'manuscript_block': [record('block_id', 'block_fixture', tab_id='tab_fixture',
            chapter_id='chapter_fixture', position=0, text_content='Disposable Ω quietly text',
            content_html='<p>Disposable Ω quietly text</p>', block_type='paragraph')],
        'markup_label': [record('label_id', 'label_fixture', name='Synthetic marker', color='#335577',
                                editing_search_categories=['adverbs'])],
        'annotation': [record('annotation_id', 'annotation_fixture', block_id='block_fixture',
            label_id='label_fixture', annotation_type='highlight', body='editing-search/v1:adverbs',
            source_generation=1, start_offset=13, end_offset=20, selected_text='quietly', stale=False)],
    }
    source = io.BytesIO()
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(dict(format='breadmaker-hosted-project',
            format_version=3, source_project_id=pid, title='Synthetic codec test')))
        archive.writestr('project.json', json.dumps(dict(entities=entities)))
    output = to_local_package(source.getvalue(), project_generation=1)
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        raw = archive.read('project.sqlite3')
        manifest = json.loads(archive.read('manifest.json'))
    if hashlib.sha256(raw).hexdigest() != manifest['file_hashes']['project.sqlite3']:
        raise ValueError('SQLITE_PORTABLE_PACKAGE_HASH_FAILED')
    with sqlite3.connect(':memory:') as restored:
        restored.deserialize(raw)
        if (restored.execute('select text_content from manuscript_blocks').fetchall()
                != [('Disposable Ω quietly text',)] or restored.execute('PRAGMA foreign_key_check').fetchall()
                or restored.execute('PRAGMA integrity_check').fetchone() != ('ok',)):
            raise ValueError('SQLITE_PORTABLE_PACKAGE_ROUNDTRIP_FAILED')
        if restored.execute('select selected_text,stale from annotations').fetchall() != [('quietly', 0)]:
            raise ValueError('SQLITE_PORTABLE_ANNOTATIONS_FAILED')
        registry = json.loads(restored.execute("select value_json from ui_state where key='editing-search-markers/v1'").fetchone()[0])
        marker = restored.execute('select id,created_at from markup_labels').fetchone()
        if registry.get('adverbs') != dict(id=marker[0], created_at=marker[1]):
            raise ValueError('SQLITE_PORTABLE_MARKER_OWNERSHIP_FAILED')
        if restored.execute('select value from storage_meta where key="schema_version"').fetchone() != ('42',):
            raise ValueError('SQLITE_PORTABLE_SCHEMA_FAILED')


def check():
    pin = json.loads((ROOT / 'deploy/sqlite-source.json').read_text())
    evidence = json.loads(Path('/usr/share/breadmaker/sqlite/provenance.json').read_text())
    for key, value in pin.items():
        if evidence.get(key) != value:
            raise ValueError('SQLITE_PROVENANCE_MISMATCH')
    library = Path('/opt/python-libs/libsqlite3.so.0')
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    if evidence.get('library_sha256') != digest or sqlite3.sqlite_version != pin['version']:
        raise ValueError('SQLITE_RUNTIME_MISMATCH')
    native = ctypes.CDLL(str(library))
    native.sqlite3_sourceid.restype = ctypes.c_char_p
    if native.sqlite3_sourceid().decode() != pin['source_id']:
        raise ValueError('SQLITE_SOURCE_ID_MISMATCH')
    paths = {line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
             if 'libsqlite3' in line}
    if paths != {str(library)}:
        raise ValueError('SQLITE_UNREVIEWED_LIBRARY_LOADED')
    if evidence.get('shell_included') is not False or shutil.which('sqlite3') or any(Path(path).exists() for path in
            ('/usr/bin/sqlite3', '/bin/sqlite3', '/usr/local/bin/sqlite3', '/usr/local/lib/python3.14/sqlite3/__main__.py')):
        raise ValueError('SQLITE_SHELL_PRESENT')
    with sqlite3.connect(':memory:') as db:
        if db.execute('select sqlite_source_id()').fetchone()[0] != pin['source_id']:
            raise ValueError('SQLITE_PYTHON_BINDING_MISMATCH')
        options = {row[0] for row in db.execute('pragma compile_options')}
        if 'ENABLE_FTS5' in options or evidence.get('fts5_included') is not False:
            raise ValueError('SQLITE_FTS5_PRESENT')
        try:
            db.execute('CREATE VIRTUAL TABLE denied USING fts5(value)')
        except sqlite3.OperationalError as exc:
            if str(exc) != 'no such module: fts5':
                raise
        else:
            raise ValueError('SQLITE_FTS5_PRESENT')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('CREATE TABLE parent(id PRIMARY KEY);'
                         'CREATE TABLE child(id PRIMARY KEY,parent REFERENCES parent(id));')
        db.execute('INSERT INTO parent VALUES(?)', ('synthetic',))
        db.execute('INSERT INTO child VALUES(?,?)', (1, 'synthetic'))
        try:
            db.execute('INSERT INTO child VALUES(?,?)', (2, 'missing'))
        except sqlite3.IntegrityError:
            pass
        else:
            raise ValueError('SQLITE_FOREIGN_KEY_GUARD_MISSING')
        db.commit()
        db.execute('INSERT INTO parent VALUES(?)', ('rolled-back',))
        db.rollback()
        with sqlite3.connect(':memory:') as restored:
            restored.deserialize(db.serialize())
            if restored.execute('select * from parent').fetchall() != [('synthetic',)]:
                raise ValueError('SQLITE_SERIALIZATION_FAILED')
            if restored.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('SQLITE_INTEGRITY_FAILED')
    portable_package()
    return dict(version=pin['version'], package_version=pin['package_version'],
                source_id=pin['source_id'], source_sha3_256=pin['sha3_256'],
                library_sha256=digest, shell_present=False, fts5_present=False,
                serialization=True, foreign_keys=True, rollback=True, schema42=True)


if __name__ == '__main__':
    print(json.dumps(check(), sort_keys=True))
