# Exact _hydrate method from Editor 0ae065284909cd83b298a1e0cba68be20b50a557
# Source method SHA256: 336d9491e0635b46dad5c185e8617ad906fc17e17bb8d3e4c5157bcf395e6efd
# Executed with current unchanged module dependencies by the rollback test.
def _hydrate(self, logical_entity: str, row: Mapping[str, Any], *,
             ids: Mapping[str, str] | None = None, user_id: str | None = None,
             payload_cache=None) -> dict[str, Any]:
    if self.encrypted_fields is not None and self.encrypted_fields.protects(logical_entity):
        if ids is None or user_id is None:
            raise HostedValidationError("HOSTED_PRIVATE_FIRESTORE_CONTEXT_REQUIRED")
        public = self.encrypted_fields.open(logical_entity, ids, row)
    else:
        public = _public(row)
    fields = _HYBRID_PAYLOAD_FIELDS.get(logical_entity)
    if not fields:
        return public
    pointer = public.get("payload_object")
    if not isinstance(pointer, dict):
        if all(key in public for key in ("object_name", "object_generation", "object_size")):
            pointer = {
                "object_name": public.get("object_name"),
                "object_generation": public.get("object_generation"),
                "object_size": public.get("object_size"),
                "object_sha256": public.get("object_sha256"),
            }
    if self.encrypted_objects is not None and logical_entity in _HYBRID_PAYLOAD_FIELDS:
        # Fail closed on legacy plaintext and missing pointers, including
        # post-deployment reads from a different server instance.
        if (not isinstance(pointer, dict) or
                pointer.get("encryption_format") != _ENCRYPTED_FORMAT or
                not pointer.get("object_name") or ids is None or user_id is None):
            raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_REQUIRED")
        if logical_entity == "manuscript_block" and str(public.get("block_id")) != str(ids.get("block_id")):
            raise HostedValidationError("HOSTED_ENCRYPTED_BLOCK_ID_MISMATCH")
        operation = _operation_id(str(pointer.get("encryption_operation") or ""))
        ref = EncryptedObjectReference(
            name=str(pointer["object_name"]),
            generation=pointer["object_generation"],
            ciphertext_sha256=pointer["object_sha256"],
            ciphertext_size=pointer["object_size"],
        )
        if logical_entity == "manuscript_block":
            _check_encrypted_block_metadata(public)
        decoded = self.encrypted_objects.read(
            project_id=str(ids["project_id"]), user_id=require_user_id(user_id),
            entity=logical_entity,
            record_id=self._encrypted_hybrid_record_id(logical_entity, ids),
            operation_id=operation, reference=ref,
        )
        public.update(_json_gunzip(decoded))
        return public
    if not isinstance(pointer, dict) or not pointer.get("object_name"):
        return public
    pointer=self._read_pointer(row)
    key=self._payload_key(pointer)
    body=payload_cache[key] if payload_cache is not None and key in payload_cache else self._read_hybrid_body(pointer)
    if 'bundle_entry' in pointer:
        eid=str(pointer['bundle_entry'])
        if (logical_entity!='manuscript_block' or eid!=str(public.get('block_id'))
                or not pointer.get('object_sha256')):
            raise HostedValidationError('HOSTED_BLOCK_BUNDLE_ID_INVALID')
        entries=body.get('entries')
        body=entries.get(eid) if isinstance(entries,dict) and len(entries)<=64 else None
        if not isinstance(body,dict) or set(body)!={'content_html','text_content'}:
            raise HostedValidationError('HOSTED_BLOCK_BUNDLE_INVALID')
        if (pointer.get('source_sha256') and
                hashlib.sha256(_json_gzip(body)).hexdigest()!=pointer['source_sha256']):
            raise HostedValidationError('HOSTED_BLOCK_BUNDLE_SOURCE_MISMATCH')
    public.update(body)
    return public
