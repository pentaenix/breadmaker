"""Small ENCRYPTED derivative read index. No canonical-generation/History writes.

Private excerpts/source hashes/voice/ranges stay sealed at rest. Publication
prepares encryption outside the retrying transaction, then checks/appends index
writes in the SAME final head CAS. The caller must finish ALL other reads and
validation BEFORE PreparedIndex.commit; only writes may follow it.
"""
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import math
import re

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id
from hosted.narration.recordings import ROOT
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.narration.authority import require_authority

PAGE_RECORDS = 128
MAX_PAGES = 160
MAX_PAGE_BYTES = 256 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


def _root(pid): return (ROOT, pid, 'index_control', 'current')
def _page(pid, page): return (ROOT, pid, 'index_pages', page)


def summary(record, source, *, record_id, publication, generation, duration, audio_bytes):
    """Source hashes exist only inside ciphertext/authorized transient responses."""
    return dict(id=record_id, publication=publication, generation=generation,
        block_id=record['block_id'], target_type=record.get('target_type', 'block'),
        chapter_id=source['chapter'], document_id=source['tab'], source_generation=source['generation'],
        source_hash=hashlib.sha256(record['source_text'].encode()).hexdigest(),
        source_length=len(record['source_text'].encode('utf-16-le')) // 2,
        text=record['text'][:160], start=record['start'], end=record['end'], voice=record['voice'],
        engine=record['engine'], duration=duration, audio_bytes=audio_bytes)


def _entry(value):
    fields = {'id', 'publication', 'generation', 'block_id', 'target_type', 'chapter_id', 'document_id',
              'source_generation', 'source_hash', 'source_length', 'text', 'start', 'end', 'voice',
              'engine', 'duration', 'audio_bytes'}
    try:
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError()
        for key in ('id', 'publication', 'chapter_id', 'document_id'):
            require_opaque_id(value[key])
        if value['target_type'] not in {'block', 'chapter_title'}:
            raise ValueError()
        if value['target_type'] == 'chapter_title':
            if value['block_id'] != 'chapter-title:' + value['chapter_id']:
                raise ValueError()
        else:
            require_opaque_id(value['block_id'])
        if (not isinstance(value['source_hash'], str) or not re.fullmatch('[0-9a-f]{64}', value['source_hash'])
                or any(type(value[key]) is not int or not 1 <= value[key] < 2**63 for key in ('generation', 'source_generation'))
                or any(type(value[key]) is not int for key in ('start', 'end', 'source_length', 'audio_bytes'))
                or not 0 <= value['start'] < value['end'] <= value['source_length'] <= 200_000
                or not 44 < value['audio_bytes'] <= 32 * 1024**2
                or not isinstance(value['text'], str) or len(value['text']) > 160
                or not isinstance(value['voice'], str) or not re.fullmatch('[a-z]{2}_[a-z0-9_]{1,60}', value['voice'])
                or not isinstance(value['engine'], str) or not 1 <= len(value['engine']) <= 160
                or type(value['duration']) not in (int, float) or not math.isfinite(value['duration'])
                or not 0 < value['duration'] <= 700):
            raise ValueError()
        return dict(value)
    except (KeyError, TypeError, ValueError):
        raise HostedValidationError('NARRATION_INDEX_ENTRY_INVALID') from None


@dataclass(frozen=True)
class PreparedIndex:
    owner: object
    uid: str
    pid: str
    expected: dict
    writes: dict
    locations: dict

    def commit(self, tx):
        require_authority(self.owner.authority, tx, self.uid, self.pid, 'write')
        current = tx.get_many(list(self.expected))
        if any(actual != self.expected[path] for path, actual in zip(self.expected, current)):
            raise DocumentConflict('NARRATION_INDEX_CHANGED')
        # All serialization/encryption validation happened before ANY write.
        for path, data in self.writes.items():
            if data is None:
                tx.delete(path)
            else:
                tx.set(path, data)


class PrivateNarrationIndex:
    def __init__(self, documents, envelope, *, authority, epoch):
        if not isinstance(envelope, HostedEnvelopeCodec) or not callable(authority):
            raise ValueError('NARRATION_PRIVATE_INDEX_DEPENDENCIES_REQUIRED')
        self.documents, self.envelope, self.authority = documents, envelope, authority
        self.epoch = require_opaque_id(epoch)

    def _control(self, row):
        if row is None:
            return dict(v=1, epoch=self.epoch, generation=0, pages={})
        data = dict(row.data)
        if (set(data) != {'v', 'epoch', 'generation', 'pages'} or type(data['v']) is not int or data['v'] != 1
                or data['epoch'] != self.epoch or type(data['generation']) is not int
                or not 0 < data['generation'] < 2**63 or not isinstance(data['pages'], dict)
                or len(data['pages']) > MAX_PAGES):
            raise HostedValidationError('NARRATION_INDEX_STATE_INVALID')
        for key, page in data['pages'].items():
            if (not isinstance(key, str) or not re.fullmatch('[0-9]{3}', key) or int(key) >= MAX_PAGES
                    or not isinstance(page, dict) or set(page) != {'count', 'generation'}
                    or type(page['count']) is not int or not 1 <= page['count'] <= PAGE_RECORDS
                    or type(page['generation']) is not int or not 1 <= page['generation'] <= data['generation']):
                raise HostedValidationError('NARRATION_INDEX_STATE_INVALID')
        return {**data, 'pages': {key: dict(value) for key, value in data['pages'].items()}}

    def _context(self, pid, page, generation):
        return dict(project_id=pid, entity='narration_index', record_id=f'{page}_{generation}')

    def _open(self, pid, page, row, expected):
        if (row is None or set(row.data) != {'v', 'epoch', 'generation', 'sealed'}
                or type(row.data.get('v')) is not int or row.data.get('v') != 1
                or type(row.data.get('generation')) is not int
                or row.data.get('epoch') != self.epoch or row.data.get('generation') != expected['generation']):
            raise HostedValidationError('NARRATION_INDEX_PAGE_INVALID')
        data = self.envelope.open(row.data['sealed'], context=self._context(pid, page, expected['generation']))
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                payload = stream.read(MAX_PAGE_BYTES + 1)
            if len(payload) > MAX_PAGE_BYTES:
                raise ValueError()
            values = json.loads(payload)
            if not isinstance(values, list) or len(values) != expected['count'] or len(values) > PAGE_RECORDS:
                raise ValueError()
            result = {_entry(value)['id']: dict(value) for value in values}
            if len(result) != len(values):
                raise ValueError()
            return result
        except (ValueError, TypeError, KeyError, OSError, EOFError):
            raise HostedValidationError('NARRATION_INDEX_PAGE_INVALID') from None

    def prepare(self, uid, pid, *, add=(), remove=()):
        """Server-only remove=(page,id,publication,generation) current head refs."""
        pid = require_project_id(pid)
        additions = [_entry(value) for value in add]
        if len(additions) > PAGE_RECORDS or len(remove) > PAGE_RECORDS or not additions and not remove:
            raise ValueError('NARRATION_INDEX_MUTATION_INVALID')
        if len({item['id'] for item in additions}) != len(additions):
            raise ValueError('NARRATION_INDEX_MUTATION_INVALID')
        for page, rid, publication, generation in remove:
            if (not isinstance(page, str) or not re.fullmatch('[0-9]{3}', page) or int(page) >= MAX_PAGES
                    or type(generation) is not int or generation < 1):
                raise ValueError('NARRATION_INDEX_MUTATION_INVALID')
            require_opaque_id(rid); require_opaque_id(publication)
        def capture(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            root = tx.get(_root(pid))
            control = self._control(root)
            if root is None and tx.query_collection((ROOT, pid, 'records'), field='state', value='active'):
                raise DocumentConflict('NARRATION_INDEX_BOOTSTRAP_REQUIRED')
            counts = {page: value['count'] for page, value in control['pages'].items()}
            for page, *_ in remove:
                if page not in counts:
                    raise DocumentConflict('NARRATION_INDEX_REMOVAL_CHANGED')
                counts[page] -= 1
                if counts[page] < 0:
                    raise DocumentConflict('NARRATION_INDEX_REMOVAL_CHANGED')
            locations = {}
            for item in additions:
                page = next((f'{i:03}' for i in range(MAX_PAGES) if counts.get(f'{i:03}', 0) < PAGE_RECORDS), None)
                if page is None:
                    raise DocumentConflict('NARRATION_INDEX_CAPACITY')
                counts[page] = counts.get(page, 0) + 1
                locations[item['id']] = page
            pages = sorted({page for page, *_ in remove} | set(locations.values()))
            fetched = tx.get_many([_page(pid, page) for page in pages])
            return root, control, locations, dict(zip(pages, fetched))
        root, control, locations, rows = self.documents.run_transaction(capture)
        entries = {page: self._open(pid, page, row, control['pages'][page]) if page in control['pages'] else {}
                   for page, row in rows.items()}
        if any(row is not None and page not in control['pages'] for page, row in rows.items()):
            raise DocumentConflict('NARRATION_INDEX_ORPHAN_PAGE')
        for page, rid, publication, generation in remove:
            value = entries[page].get(rid)
            if value is None or (value['publication'], value['generation']) != (publication, generation):
                raise DocumentConflict('NARRATION_INDEX_REMOVAL_CHANGED')
            del entries[page][rid]
        for item in additions:
            page = locations[item['id']]
            if item['id'] in entries[page]:
                raise DocumentConflict('NARRATION_INDEX_DUPLICATE')
            entries[page][item['id']] = item
        generation = control['generation'] + 1
        writes = {}
        for page, values in entries.items():
            if not values:
                control['pages'].pop(page)
                writes[_page(pid, page)] = None
                continue
            payload = _json([values[rid] for rid in sorted(values)])
            if len(values) > PAGE_RECORDS or len(payload) > MAX_PAGE_BYTES:
                raise HostedValidationError('NARRATION_INDEX_PAGE_TOO_LARGE')
            sealed = self.envelope.seal(gzip.compress(payload, mtime=0), context=self._context(pid, page, generation))
            writes[_page(pid, page)] = dict(v=1, epoch=self.epoch, generation=generation, sealed=sealed)
            control['pages'][page] = dict(count=len(values), generation=generation)
        control['generation'] = generation
        writes[_root(pid)] = control
        expected = {_root(pid): root, **{_page(pid, page): row for page, row in rows.items()}}
        return PreparedIndex(self, uid, pid, expected, writes, locations)

    def page(self, uid, pid, *, after=''):
        pid = require_project_id(pid)
        if after:
            match = re.fullmatch(r'([0-9]{3}):([A-Za-z0-9_-]{0,160})', after) if isinstance(after, str) else None
            if match is None or int(match[1]) >= MAX_PAGES:
                raise ValueError('NARRATION_INDEX_CURSOR_INVALID')
            first, last = match.groups()
        else:
            first, last = '000', ''
        def capture(tx):
            require_authority(self.authority, tx, uid, pid, 'read')
            root = tx.get(_root(pid))
            if root is None and tx.query_collection((ROOT, pid, 'records'), field='state', value='active'):
                raise DocumentConflict('NARRATION_INDEX_BOOTSTRAP_REQUIRED')
            control = self._control(root)
            pages = [page for page in sorted(control['pages']) if page >= first]
            page = pages[0] if pages else None
            row = tx.get(_page(pid, page)) if page else None
            return root, control, page, row, pages
        root, control, page, row, pages = self.documents.run_transaction(capture)
        if page is None:
            return {'records': [], 'next': None}
        values = self._open(pid, page, row, control['pages'][page])
        records = [value for rid, value in sorted(values.items()) if page != first or rid > last]
        next_cursor = pages[1] + ':' if len(pages) > 1 else None
        def recheck(tx):
            require_authority(self.authority, tx, uid, pid, 'read')
            if tx.get(_root(pid)) != root or tx.get(_page(pid, page)) != row:
                raise DocumentConflict('NARRATION_INDEX_CHANGED')
        self.documents.run_transaction(recheck)
        return {'records': records, 'next': next_cursor}
