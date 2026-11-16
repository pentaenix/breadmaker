"""Bounded account/ownership eligibility, optionally in an existing transaction.

The fingerprint carries no numeric totals or identity payload. Root generation is
checked while collecting; it is deliberately absent from the cache eligibility
key so a dated observation can survive an ordinary edit, never account closure,
project removal, owner transfer, account recreation or an erasure fence.
"""
import hashlib
import json
from hosted.firestore.ids import require_user_id,require_project_id
from hosted.security.saved_word_reader import (GoogleFirestoreSavedWordsProducer,SavedWordsUnavailable,
    USER_FIELDS,OWNER_FIELDS)
from hosted.security.saved_word_identity import verify_owner,_stamp

ROOT_FIELDS=('status','generation','created_at')


class GoogleFirestoreAccountEligibility(GoogleFirestoreSavedWordsProducer):
    def __init__(self,*a,transaction=None,**kw):
        super().__init__(*a,**kw);self.transaction=transaction

    def _get(self,path,fields,deadline):
        if self.transaction is None:return super()._get(path,fields,deadline)
        row=self.client.document('/'.join(path)).get(field_paths=list(fields),transaction=self.transaction,
            timeout=min(2,self._remaining(deadline)),retry=None)
        self._remaining(deadline)
        if not row.exists:return None
        data=row.to_dict() or {};return {k:data[k] for k in fields if k in data}

    def _query(self,path,fields,field,value,limit,deadline):
        if self.transaction is None:return super()._query(path,fields,field,value,limit,deadline)
        from google.cloud.firestore_v1.base_query import FieldFilter
        query=self.client.collection('/'.join(path)).where(filter=FieldFilter(field,'==',value))
        stream=query.select(list(fields)).limit(limit).stream(transaction=self.transaction,
            timeout=min(2,self._remaining(deadline)),retry=None)
        rows=[]
        try:
            for row in stream:
                self._remaining(deadline)
                if len(rows)>=limit:raise SavedWordsUnavailable('ACCOUNT_ELIGIBILITY_PROVIDER_LIMIT')
                data=row.to_dict() or {}
                rows.append((tuple(row.reference.path.split('/')),{k:data[k] for k in fields if k in data}))
        finally:
            close=getattr(stream,'close',None)
            if close is not None:close()
        self._remaining(deadline);return rows

    def _population(self,collection,fields,deadline):
        rows=self._query((collection,),fields,'status','active',101,deadline)
        if len(rows)>100:raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_LIMIT')
        found={}
        for path,data in rows:
            if len(path)!=2 or path[0]!=collection or data.get('status')!='active':
                raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_INVALID')
            key=require_user_id(path[1]) if collection=='users' else require_project_id(path[1])
            if key in found:raise SavedWordsUnavailable('ACCOUNT_WORD_DUPLICATE_POPULATION')
            _stamp(data.get('created_at'))
            if collection=='users':
                if data.get('user_id')!=key:raise SavedWordsUnavailable('ACCOUNT_WORD_PROFILE_INVALID')
            elif type(data.get('generation')) is not int or not 1<=data['generation']<=2**53-1:
                raise SavedWordsUnavailable('ACCOUNT_WORD_ROOT_INVALID')
            found[key]=data
        return found

    def snapshot(self,*,deadline):
        if not self.enabled or self.client is None:raise SavedWordsUnavailable('ACCOUNT_ELIGIBILITY_DISABLED')
        all_profiles=self._population('users',USER_FIELDS,deadline)
        locks={uid:self._get(('account_erasure_locks',uid),('user_id',),deadline) for uid in all_profiles}
        profiles={uid:p for uid,p in all_profiles.items() if locks[uid] is None}
        roots=self._population('projects',ROOT_FIELDS,deadline)
        manifests={uid:{} for uid in profiles};owners={};inactive={};project_pins={}
        for pid,root in roots.items():
            rows=self._query(('projects',pid,'members'),OWNER_FIELDS,'role','owner',2,deadline)
            owners[pid]=rows
            if len(rows)!=1:raise SavedWordsUnavailable('ACCOUNT_WORD_OWNER_UNVERIFIED')
            path,member=rows[0];uid=require_user_id(member.get('user_id'))
            if path!=('projects',pid,'members',uid) or member.get('role')!='owner':
                raise SavedWordsUnavailable('ACCOUNT_WORD_OWNER_UNVERIFIED')
            if uid not in profiles:
                if uid in all_profiles:continue
                profile=self._get(('users',uid),USER_FIELDS,deadline)
                if profile is not None and profile.get('status')=='active':
                    raise SavedWordsUnavailable('ACCOUNT_WORD_POPULATION_CHANGED')
                inactive[uid]=profile;continue
            owner=verify_owner(project_id=pid,owner_rows=rows,hosted_user=profiles[uid])
            manifests[uid][pid]=(root['created_at'],root['generation'])
            project_pins[pid]=[root['created_at'],uid,owner.hosted_created_at,owner.membership_created_at]
        basis={'accounts':{uid:p['created_at'] for uid,p in profiles.items()},'projects':project_pins}
        digest=hashlib.sha256(json.dumps(basis,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
        return dict(all_profiles=all_profiles,profiles=profiles,locks=locks,roots=roots,owners=owners,
                    inactive=inactive,manifests=manifests,fingerprint=digest)
