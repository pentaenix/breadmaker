from __future__ import annotations

"""Short-lived, actor/action/target-bound destructive-action confirmations."""

import secrets
import threading
import time

from fastapi import HTTPException

_DANGER_CHALLENGES: dict[str, dict[str, object]] = {}
_DANGER_CHALLENGE_LOCK=threading.Lock()
_DANGER_CHALLENGE_TTL_SECONDS=300
_DANGER_ALPHABET="abcdefghijklmnopqrstuvwxyz0123456789"

def _new_danger_challenge(action:str,*,actor_id:str,target_id:str) -> dict[str,str]:
    if action not in {"delete_project","delete_all_data","delete_external_backups"}:
        raise HTTPException(400,detail="Unknown destructive action.")
    if not actor_id or not target_id:raise HTTPException(400,detail='Destructive confirmation requires an actor and target.')
    challenge_id=secrets.token_urlsafe(18)
    code=''.join(secrets.choice(_DANGER_ALPHABET) for _ in range(10))
    created=time.monotonic()
    with _DANGER_CHALLENGE_LOCK:
        expired=[key for key,value in _DANGER_CHALLENGES.items() if created-float(value.get("created",0))>_DANGER_CHALLENGE_TTL_SECONDS]
        for key in expired: _DANGER_CHALLENGES.pop(key,None)
        _DANGER_CHALLENGES[challenge_id]={"action":action,"code":code,"created":created,"actor_id":str(actor_id),"target_id":str(target_id)}
    return {"challenge_id":challenge_id,"code":code}

def _consume_danger_challenge(challenge_id:str,code:str,action:str,*,actor_id:str,target_id:str) -> None:
    with _DANGER_CHALLENGE_LOCK:
        item=_DANGER_CHALLENGES.pop(challenge_id,None)
    if not item or time.monotonic()-float(item.get("created",0))>_DANGER_CHALLENGE_TTL_SECONDS:
        raise HTTPException(400,detail="Confirmation code expired. Start the delete action again.")
    matches=(item.get("action")==action and secrets.compare_digest(str(item.get("code") or ''),str(code or ''))
             and secrets.compare_digest(str(item.get('actor_id') or ''),str(actor_id or ''))
             and secrets.compare_digest(str(item.get('target_id') or ''),str(target_id or '')))
    if not matches:raise HTTPException(400,detail="Confirmation code did not match this actor, action, and target.")
