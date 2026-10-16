from __future__ import annotations

from conftest import assert_ok
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import values as storage_values


def test_revision_store_handles_ten_thousand_paragraph_manuscript(client, fresh_project, app_module_fixture):
    """Current History must handle a novel-scale text corpus without legacy revisions."""
    ch=fresh_project["bootstrap"]["chapters"][0]
    with storage_mutations.tx() as con:
        pid=con.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
        con.execute("DELETE FROM manuscript_blocks WHERE chapter_id=?",(ch["id"],))
        now=storage_values.now();rows=[]
        for i in range(10000):
            text=f"Paragraph {i}: " + ("ordinary manuscript prose "*3)
            rows.append((f"scale_{i}",pid,ch["tab_id"],ch["id"],i,"paragraph","body",text,text,"",storage_values.block_content_hash(text,text,''),now,now))
        con.executemany("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,image_path,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    base=storage_history.capture_local_revision(label="Scale base",creation_reason="scale",force_checkpoint=True)
    assert base['revision_id']
    with storage_mutations.tx() as con:
        text='Changed one paragraph';con.execute("UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id='scale_5000'",(text,text,storage_values.block_content_hash(text,text,''),storage_values.now()))
    delta=storage_history.capture_local_revision(label="Scale changed",creation_reason="scale")
    verified=storage_history.local_revision_store().verify_revision(delta['revision_id'])
    assert int(verified['revision']['generation_end']) > int(base['generation_end'])
    assert verified['revision']['expected_state_hash'] != base['expected_state_hash']
    with storage_mutations.tx(track_generation=False) as con:
        assert con.execute("SELECT COUNT(*) FROM manuscript_blocks WHERE project_id=?",(pid,)).fetchone()[0] >= 10000
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='revisions'").fetchone()[0] == 0
