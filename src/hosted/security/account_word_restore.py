"""Bounded current-state repair after an already committed Undo/Redo.

Reuses existing clients. No background loop, credentials, journal/content write,
activation or retry. Large/failed repairs remain unknown for explicit resumption.
"""
import time
from hosted.firestore.ids import require_project_id,require_user_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.memory import MemoryObjectStore
from hosted.security.account_word_backfill import AccountWordBackfill
from hosted.security.account_word_backfill_store import BoundedAccountWordStore,BackfillUnavailable
from hosted.security.account_word_content import BoundedCurrentBlockReader


class AccountWordRestoreRepair:
    def __init__(self,store,objects,*,memory_reader=None,resource_budget=None):
        if not ((type(store) is GoogleFirestoreDocumentStore and type(objects) is GoogleCloudObjectStore)
                or (type(store) is MemoryDocumentStore and type(objects) is MemoryObjectStore and callable(memory_reader))):
            raise BackfillUnavailable('WORD_REPAIR_COMPOSITION_UNVERIFIED')
        self.store,self.objects,self.memory_reader=store,objects,memory_reader
        self.resource_budget=resource_budget

    def __call__(self,*,project_id,actor_user_id,block_ids):
        if self.resource_budget is not None:
            return self.resource_budget.run_restore(self._repair,project_id=project_id,actor_user_id=actor_user_id,block_ids=block_ids)
        return self._repair(project_id=project_id,actor_user_id=actor_user_id,block_ids=block_ids)

    def _repair(self,*,project_id,actor_user_id,block_ids,budget=None):
        pid,actor=require_project_id(project_id),require_user_id(actor_user_id)
        deadline=min(time.monotonic()+6,budget.deadline) if budget is not None else time.monotonic()+6
        def resolve(tx):
            profile=tx.get(('users',actor));member=tx.get(('projects',pid,'members',actor))
            root=tx.get(('projects',pid))
            if (profile is None or profile.data.get('status')!='active'
                    or tx.get(('account_erasure_locks',actor)) is not None or root is None
                    or root.data.get('status')!='active' or member is None
                    or member.data.get('user_id')!=actor or member.data.get('role') not in {'owner','editor'}):
                raise BackfillUnavailable('WORD_REPAIR_ACTOR_UNAVAILABLE')
            owners=tx.saved_word_owners(pid,timeout=min(2,deadline-time.monotonic()))
            if len(owners)!=1:raise BackfillUnavailable('WORD_REPAIR_OWNER_UNVERIFIED')
            owner=require_user_id(owners[0].data.get('user_id'))
            if owners[0].path!=('projects',pid,'members',owner):raise BackfillUnavailable('WORD_REPAIR_OWNER_UNVERIFIED')
            return owner
        if type(self.store) is MemoryDocumentStore:
            owner=self.store.run_transaction(resolve,max_attempts=1)
            worker=AccountWordBackfill(self.store,self.memory_reader,enabled=True)
        else:
            client=self.store._client
            authority=BoundedAccountWordStore(client,project_id=client.project,owner_user_id=actor,budget=budget)
            owner=authority.run_transaction(resolve,deadline=deadline)
            scope=BoundedAccountWordStore(client,project_id=client.project,owner_user_id=owner,budget=budget)
            worker=AccountWordBackfill(scope,BoundedCurrentBlockReader(scope,self.objects),enabled=True)
        return worker.repair_current_blocks(owner,pid,block_ids,deadline=deadline)
