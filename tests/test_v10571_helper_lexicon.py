"""Exact native lexicon semantics and fail-closed SQL-free helper projection."""
import hashlib
import json
import sqlite3
import sys

import pytest

from narration import pronunciation as p


def test_native_lexicon_keeps_first_pronunciation_in_native_priority_order(tmp_path):
    with sqlite3.connect(tmp_path / 'lexicon.db') as db:
        db.execute('CREATE TABLE word_phonemes(word TEXT,phonemes TEXT,pron_order INTEGER)')
        db.executemany('INSERT INTO word_phonemes VALUES(?,?,?)',
            [('ciao', 'second', 2), ('ciao', 'ʧ a o', 1), ('mondo', 'm o n d o', 1)])
    assert p._italian_lexicon(tmp_path) == {'ciao': 'ʧao', 'mondo': 'mondo'}


def test_projected_lexicon_loads_exact_mapping_without_importing_sqlite(tmp_path, monkeypatch):
    expected = {'ciao': 'ʧao', 'mondo': 'mondo'}
    raw = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    (tmp_path / 'breadmaker-lexicon.json').write_bytes(raw)
    monkeypatch.setattr(p, '_ITALIAN_LEXICON_SHA256', hashlib.sha256(raw).hexdigest())
    monkeypatch.setitem(sys.modules, 'sqlite3', None)
    assert p._italian_lexicon(tmp_path) == expected


def test_corrupt_or_oversized_projection_never_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'sqlite3', None)
    for raw in (b'{"private-looking-fixture":"untrusted"}', b' ' * (1024 * 1024 + 1)):
        (tmp_path / 'breadmaker-lexicon.json').write_bytes(raw)
        with pytest.raises(p.PronunciationError, match='LANGUAGE_DATA_PIN_MISMATCH'):
            p._italian_lexicon(tmp_path)
