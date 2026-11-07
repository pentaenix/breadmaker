"""Publish a chapter deletion and its existing Undo images in one transaction."""
from hosted.firestore.contracts import DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
import json

MAX_CHANGED_RECORDS = 95
MAX_METADATA_BYTES = 300_000


def delete_chapter_atomic(domain, project_id, chapter_id, user_id):
    pid, cid, uid = require_project_id(project_id), require_opaque_id(chapter_id, label='chapter_id'), require_user_id(user_id)
    model = domain._model('chapter')
    ids = {'project_id': pid, 'chapter_id': cid}
    stamp = domain.clock.now_iso()
    def remove(tx):
        root_path, root = domain._authorize_and_project(tx, model, ids, uid, mutation=True)
        chapter = tx.get(('projects', pid, 'chapters', cid))
        if chapter is None:
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        rows = [chapter] + tx.query_collection(('projects', pid, 'blocks'), field='chapter_id', value=cid)
        rows += tx.query_collection(('projects', pid, 'scenes'), field='chapter_id', value=cid)
        if len(rows) > MAX_CHANGED_RECORDS or len(json.dumps([row.data for row in rows], ensure_ascii=False, default=str).encode()) > MAX_METADATA_BYTES:
            raise HostedValidationError('This chapter is too large to delete safely with Undo. Its contents have not changed.')
        # Membership and the complete bounded set are read before any mutation.
        # ActionDocumentStore records exactly these same images atomically.
        domain._advance_project(tx, root_path, root, expected_generation=root['generation'], stamp=stamp)
        for row in rows:
            tx.delete(row.path)
        return {'ok': True}
    return domain.documents.run_transaction(remove)
