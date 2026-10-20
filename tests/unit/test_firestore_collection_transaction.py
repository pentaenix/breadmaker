from types import SimpleNamespace

from hosted.firestore.google_driver import _GoogleTransaction


def test_unfiltered_collection_stream_preserves_transaction():
    transaction = object()
    seen = []

    class Collection:
        # A CollectionReference is not a Query accepted by Transaction.get.
        def stream(self, *, transaction):
            seen.append(transaction)
            return [SimpleNamespace(reference=SimpleNamespace(path='projects/p1/chapters/c1'),
                                    to_dict=lambda: {'title': 'Synthetic'}, exists=True)]

    client = SimpleNamespace(collection=lambda path: Collection() if path == 'projects/p1/chapters' else None)
    rows = _GoogleTransaction(client, transaction).query_collection(('projects', 'p1', 'chapters'))
    assert seen == [transaction]
    assert len(rows) == 1 and rows[0].path == ('projects', 'p1', 'chapters', 'c1')
    assert rows[0].data == {'title': 'Synthetic'}
