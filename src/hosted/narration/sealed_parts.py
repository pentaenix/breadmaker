"""Binary transport of the existing reviewed envelope, not another cipher.

Only ciphertext lives in GCS. Small authenticated key/nonce headers live in
Firestore so a future CAS key rewrap need not copy private audio objects.
"""
import base64
import hashlib
from dataclasses import asdict

from hosted.security.envelope import HostedEnvelopeCodec, EnvelopeError
from hosted.storage.contracts import ObjectIntegrityError

PART_BYTES = 2 * 1024**2


class SealedNarrationParts:
    def __init__(self, objects, envelope: HostedEnvelopeCodec):
        if not isinstance(envelope, HostedEnvelopeCodec):
            raise ValueError('NARRATION_ENVELOPE_REQUIRED')
        self.objects, self.envelope = objects, envelope

    @staticmethod
    def context(pid, publication, ordinal):
        return {'project_id': pid, 'entity': 'narration_part',
                'record_id': publication + '_' + str(ordinal)}

    def seal(self, payload, *, pid, publication, ordinal):
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= PART_BYTES:
            raise ValueError('NARRATION_PART_SIZE_INVALID')
        record = self.envelope.seal(payload, context=self.context(pid, publication, ordinal))
        ciphertext = base64.b64decode(record.pop('ciphertext'), validate=True)
        return ciphertext, record

    def write(self, ciphertext, header, *, pid, publication, ordinal):
        name = f'projects/{pid}/narration/{publication}/{ordinal:03d}.bin'
        metadata = self.objects.write_chunks(name, [ciphertext], max_bytes=PART_BYTES + 16,
            content_type='application/octet-stream', if_generation_match=0)
        reference = {**asdict(metadata), 'header': dict(header)}
        # Successful upload is not proof of read integrity or authoritative publication.
        if self.read(reference, pid=pid, publication=publication, ordinal=ordinal) is None:
            raise ObjectIntegrityError('NARRATION_PART_INTEGRITY_FAILED')
        return reference

    def read(self, reference, *, pid, publication, ordinal):
        expected = f'projects/{pid}/narration/{publication}/{ordinal:03d}.bin'
        if reference.get('name') != expected or type(reference.get('generation')) is not int:
            raise ObjectIntegrityError('NARRATION_PART_REFERENCE_INVALID')
        size = reference.get('size')
        if type(size) is not int or not 17 <= size <= PART_BYTES + 16:
            raise ObjectIntegrityError('NARRATION_PART_REFERENCE_INVALID')
        ciphertext = b''.join(self.objects.read_chunks(expected, max_bytes=size,
            if_generation_match=reference['generation']))
        if len(ciphertext) != size or hashlib.sha256(ciphertext).hexdigest() != reference.get('sha256'):
            raise ObjectIntegrityError('NARRATION_PART_INTEGRITY_FAILED')
        try:
            return self.envelope.open({**reference['header'],
                'ciphertext': base64.b64encode(ciphertext).decode('ascii')},
                context=self.context(pid, publication, ordinal))
        except (KeyError, TypeError, EnvelopeError):
            raise ObjectIntegrityError('NARRATION_PART_INTEGRITY_FAILED') from None
