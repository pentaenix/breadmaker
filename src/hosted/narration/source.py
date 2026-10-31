"""Canonical hosted narration targets and retryable source fences; no SQLite."""
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound
from hosted.firestore.ids import require_opaque_id
from hosted.narration.authority import require_authority


class NarrationSource:
    def __init__(self, domain, authority):
        self.domain, self.authority = domain, authority

    def check(self, tx, uid, pid, fence):
        require_authority(self.authority, tx, uid, pid, 'write')
        kind, eid = fence['kind'], require_opaque_id(fence['id'])
        if kind not in {'block', 'chapter_title'}:
            raise ValueError('NARRATION_TARGET_INVALID')
        path = ('projects', pid, 'blocks' if kind == 'block' else 'chapters', eid)
        row = tx.get(path)
        if row is None:
            raise DocumentNotFound('NARRATION_BLOCK_NOT_FOUND')
        chapter_id = row.data.get('chapter_id') if kind == 'block' else eid
        chapter = tx.get(('projects', pid, 'chapters', require_opaque_id(chapter_id)))
        if chapter is None:
            raise DocumentNotFound('NARRATION_BLOCK_NOT_FOUND')
        tab = tx.get(('projects', pid, 'tabs', require_opaque_id(chapter.data.get('tab_id'))))
        if tab is None:
            raise DocumentNotFound('NARRATION_BLOCK_NOT_FOUND')
        if (type(row.data.get('generation')) is not int or row.data['generation'] < 1
                or (kind == 'block' and row.data.get('tab_id') != chapter.data.get('tab_id'))):
            raise DocumentConflict('NARRATION_SOURCE_STRUCTURE_INVALID')
        current = {'kind': kind, 'id': eid, 'generation': row.data.get('generation'),
                   'chapter': chapter_id, 'tab': chapter.data.get('tab_id')}
        if current != fence:
            raise DocumentConflict('NARRATION_TEXT_CHANGED')
        return dict(row.data)

    def capture(self, uid, pid, *, kind, entity_id):
        eid = require_opaque_id(entity_id)
        if kind not in {'block', 'chapter_title'}:
            raise ValueError('NARRATION_TARGET_INVALID')
        def read(tx):
            require_authority(self.authority, tx, uid, pid, 'write')
            path = ('projects', pid, 'blocks' if kind == 'block' else 'chapters', eid)
            row = tx.get(path)
            if row is None:
                raise DocumentNotFound('NARRATION_BLOCK_NOT_FOUND')
            chapter_id = row.data.get('chapter_id') if kind == 'block' else eid
            chapter = tx.get(('projects', pid, 'chapters', require_opaque_id(chapter_id)))
            if chapter is None:
                raise DocumentNotFound('NARRATION_BLOCK_NOT_FOUND')
            fence = {'kind': kind, 'id': eid, 'generation': row.data.get('generation'),
                     'chapter': chapter_id, 'tab': chapter.data.get('tab_id')}
            self.check(tx, uid, pid, fence)
            return fence, dict(row.data)
        fence, raw = self.domain.documents.run_transaction(read)
        entity, field = ('manuscript_block', 'block_id') if kind == 'block' else ('chapter', 'chapter_id')
        # GCS/KMS outside the retried callback; the exact source generation is
        # checked again in the final pointer transaction, including on resume.
        hydrated = self.domain._hydrate(entity, raw, ids={'project_id': pid, field: eid}, user_id=uid)
        return fence, str(hydrated.get('text_content' if kind == 'block' else 'title') or '')
