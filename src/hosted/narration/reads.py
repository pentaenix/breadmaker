"""Authorized encrypted-index pages, lazy details and precharged audio reads."""

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
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
        return self.recordings.index.page(uid, pid, after=after)

    def metadata(self, uid, pid, rid, expected_generation):
        if type(expected_generation) is not int or expected_generation < 1:
            raise HostedValidationError('NARRATION_GENERATION_INVALID')
        service = self.recordings
        head, before = service.documents.run_transaction(lambda tx: service.record(tx, uid, pid, rid))
        if head['generation'] != expected_generation:
            raise DocumentConflict('NARRATION_RECORD_CHANGED')
        record = service._open(pid, before['publication'], before['sealed_metadata'])
        def recheck(tx):
            current_head, current = service.record(tx, uid, pid, rid)
            if (current_head, current) != (head, before):
                raise DocumentConflict('NARRATION_RECORD_CHANGED')
        service.documents.run_transaction(recheck)
        return service.authorized_view(record, before, head)

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
