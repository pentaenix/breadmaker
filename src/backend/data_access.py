from __future__ import annotations

"""Small canonical SQLite row helpers shared by feature routes."""

def one(con,q,args=()):
    r=con.execute(q,args).fetchone(); return dict(r) if r else None

def allrows(con,q,args=()): return [dict(r) for r in con.execute(q,args)]

def project(con): return one(con,"SELECT * FROM projects ORDER BY id LIMIT 1")

def _delete_polymorphic_references(con,pid:int,groups:dict[str,object]):
    """Delete heterogeneous links whenever a referenceable entity disappears."""
    clauses=[]; args=[pid]
    for typ,raw_ids in groups.items():
        ids=tuple(str(x) for x in raw_ids if x is not None)
        if not ids: continue
        marks=','.join('?' for _ in ids)
        clauses.append(f"((source_type=? AND source_id IN ({marks})) OR (target_type=? AND target_id IN ({marks})))")
        args.extend((typ,*ids,typ,*ids))
    if clauses:
        con.execute("DELETE FROM references_map WHERE project_id=? AND ("+' OR '.join(clauses)+")",args)
