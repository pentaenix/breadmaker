from __future__ import annotations
from conftest import assert_ok


def test_nested_canvases_reparent_children_on_delete_and_prevent_cycles(client, fresh_project):
    a=assert_ok(client.post("/api/canvases",json={"name":"A"})); b=assert_ok(client.post("/api/canvases",json={"name":"B","parent_id":a["id"]})); c=assert_ok(client.post("/api/canvases",json={"name":"C","parent_id":b["id"]}))
    cycle=client.patch(f"/api/canvases/{a['id']}",json={"parent_id":c["id"]})
    assert cycle.status_code==400
    assert_ok(client.delete(f"/api/canvases/{b['id']}"))
    boot=assert_ok(client.get("/api/bootstrap")); cnow=next(x for x in boot["canvases"] if x["id"]==c["id"])
    assert cnow["parent_id"]==a["id"]


def test_canvas_nodes_shapes_colors_ports_and_links(client, fresh_project):
    cv=assert_ok(client.post("/api/canvases",json={"name":"Plot"}))
    n1=assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={"shape":"rect","title":"Maybe","color":"#112233","text_color":"#fefefe","icon":"?"}))
    n2=assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={"shape":"text","title":"Result","x":400}))
    edge=assert_ok(client.post(f"/api/canvases/{cv['id']}/edges",json={"from_node_id":n1["id"],"to_node_id":n2["id"],"from_port":"right","to_port":"left"}))
    assert edge["from_port"]=="right" and edge["to_port"]=="left"
    bad=client.post(f"/api/canvases/{cv['id']}/edges",json={"from_node_id":n1["id"],"to_node_id":n2["id"],"from_port":"corner","to_port":"left"})
    assert bad.status_code==400
    data=assert_ok(client.get(f"/api/canvases/{cv['id']}")); got=next(n for n in data["nodes"] if n["id"]==n1["id"])
    assert got["shape"]=="rect" and got["text_color"]=="#fefefe"


def test_maps_derive_chronology_from_scene_reference(client, fresh_project):
    boot=fresh_project["bootstrap"]; ch=boot["chapters"][0]
    ids=assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"text_content":"Opening","content_html":"Opening"}]}))["ids"]
    scene=assert_ok(client.post("/api/scenes",json={"title":"Opening scene","block_ids":ids}))
    story_map=assert_ok(client.post("/api/maps",json={"name":"Main Story"}))
    line=assert_ok(client.post(f"/api/maps/{story_map['id']}/lines",json={"name":"Main Plot"}))
    stop=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={"title":"Catalyst","start_anchor_type":"scene","start_anchor_id":str(scene["id"])}))
    data=assert_ok(client.get(f"/api/maps/{story_map['id']}"))
    saved=next(x for x in data["lines"][0]["items"] if x["id"]==stop["id"])
    assert saved["linked"] is True and saved["start_source"]=="manuscript"
    assert saved["start_anchor_type"]=="scene" and int(saved["start_anchor_id"])==scene["id"]
