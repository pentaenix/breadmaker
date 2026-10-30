"""Authorized private metadata pages and precharged, bounded audio reads."""
import json

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_opaque_id
from hosted.narration.recordings import ROOT, MAX_RECORDS
from hosted.narration.transfers import NarrationTransferBudget


class NarrationReads:
    def __init__(self, recordings, *, policy=None, now=None):
        self.recordings = recordings
        self.policy, self.now = policy, now

    def _current(self, tx, uid, pid, rid, before):
        _, current = self.recordings.record(tx, uid, pid, rid)
        if current != before:
            raise DocumentConflict('NARRATION_RECORD_CHANGED')
        return True

    def list_page(self, uid, pid, *, after=''):
        if after:
            require_opaque_id(after)
        service = self.recordings
        def read(tx):
            service.authority(tx, uid, pid, 'read')
            # Only tiny heads are queried; private prose/timings stay encrypted
            # in the selected intents. The account-wide 20k capacity is finite.
            rows = tx.query_collection((ROOT, pid, 'records'), field='state', value='active')
            if len(rows) > MAX_RECORDS + 2:
                raise HostedValidationError('NARRATION_RECORD_CAPACITY')
            ids = sorted(row.path[-1] for row in rows if row.path[-1] > after)
            return [(rid, *service.record(tx, uid, pid, rid)) for rid in ids[:20]], len(ids) > 20
        chosen, more = service.documents.run_transaction(read)
        records, consumed, size = [], [], 0
        for rid, head, data in chosen:
            record = service._open(pid, data['publication'], data['sealed_metadata'])
            record.update(id=rid, duration=data['duration'], audio_bytes=data['audio_bytes'], generation=head['generation'])
            encoded = len(json.dumps(record, ensure_ascii=False).encode())
            if records and size + encoded > 1024**2:
                more = True
                break
            size += encoded
            records.append(record); consumed.append((rid, head, data))
        def recheck(tx):
            service.authority(tx, uid, pid, 'read')
            for rid, head, data in consumed:
                current_head, current = service.record(tx, uid, pid, rid)
                if (current_head, current) != (head, data):
                    raise DocumentConflict('NARRATION_RECORD_CHANGED')
        service.documents.run_transaction(recheck)
        return {'records': records, 'next': consumed[-1][0] if more and consumed else None}

    def audio_part(self, uid, pid, rid, ordinal):
        """Returns server-only lease state; HTTP MUST finish after send, not here.

        Failed reads are charged and release the lease. Every successful delivery
        must have a <=60s ASGI deadline covering send/backpressure in its finally.
        """
        service = self.recordings
        _, before = service.documents.run_transaction(lambda tx: service.record(tx, uid, pid, rid))
        if type(ordinal) is not int or not 0 <= ordinal < before['part_count']:
            raise HostedValidationError('NARRATION_PART_INVALID')
        reference = before['parts'][str(ordinal)]
        size = reference['size'] - 16
        budget = NarrationTransferBudget(service.documents,
            authorize=lambda tx, account, project, kind: self._current(tx, account, project, rid, before)
                if kind == 'audio' else False, policy=self.policy, now=self.now)
        permit = budget.reserve(account_id=uid, project_id=pid, kind='audio', response_bytes=size)
        try:
            body = service.parts.read(reference, pid=pid, publication=reference['attempt'], ordinal=ordinal)
            if len(body) != size:
                raise HostedValidationError('NARRATION_PART_SIZE_INVALID')
            service.documents.run_transaction(lambda tx: self._current(tx, uid, pid, rid, before))
            return body, permit, budget
        except BaseException:
            budget.finish(permit)
            raise
