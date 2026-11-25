from __future__ import annotations

import sqlite3

import pytest
from concurrent.futures import ThreadPoolExecutor

from conftest import counts, scale_by_code

WEIGH = {"毛重": 1200.0, "皮重": 800.0, "扣杂": 0.0}


def test_success_pins_scale_and_lands_ticket_in_one_commit(client, db_path):
    scale = scale_by_code(client, "甲磅")
    r = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["钉住"] == "已钉住"
    assert body["磅条号"]
    assert body["毛重"] == 1200.0 and body["皮重"] == 800.0 and body["净重"] == 400.0
    assert body["可接新磅"] in (0, False)

    gate = client.get(f"/api/地磅/{scale['编号']}/钉住态").json()
    assert gate["can_accept"] in (0, False)
    assert gate["ticket"]["磅条号"] == body["磅条号"]

    compare = client.get("/api/过磅对照").json()
    assert compare["一致"] is True
    assert compare["异常"] == []
    assert compare["在途钉住数"] == compare["未结磅条数"] == 1

    c = counts(db_path)
    assert c == {"tickets": 1, "holds": 1, "audit": 1}


def test_read_gate_matches_write_gate(client):
    """读路径「可接新磅」必须与写入侧同一谓词：读说不行，写就必须被拒。"""
    scale = scale_by_code(client, "甲磅")
    assert scale["可接新磅"] in (1, True)
    assert client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]}).status_code == 200

    scale = scale_by_code(client, "甲磅")
    assert scale["可接新磅"] in (0, False)  # 读侧已判不可接
    r = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]})
    assert r.status_code == 409  # 写侧同谓词拒绝


def test_site_closed_blocks_write_but_read_agrees(client, db_path):
    """停收回收点：读侧 可接新磅=False，写侧同样拒绝且不留痕。"""
    west = scale_by_code(client, "甲磅", site="河西旧物点")
    assert west["可接新磅"] in (0, False)
    assert west["回收点状态"] == "停收"
    before = counts(db_path)
    r = client.post("/api/过磅", json={**WEIGH, "地磅编号": west["编号"]})
    assert r.status_code == 400
    assert counts(db_path) == before


def test_validation_failure_leaves_no_trace(client, db_path):
    """半截失败（如净重<=0）后：秤未钉、无条、无审计。"""
    scale = scale_by_code(client, "甲磅")
    before = counts(db_path)
    r = client.post("/api/过磅", json={"地磅编号": scale["编号"], "毛重": 500.0, "皮重": 500.0})
    assert r.status_code == 400
    assert counts(db_path) == before
    gate = client.get(f"/api/地磅/{scale['编号']}/钉住态").json()
    assert gate["can_accept"] in (1, True)
    assert gate["held"] in (0, False)


def test_rolled_back_failure_then_success_is_consistent(client, db_path):
    """失败整笔回到过磅前快照，随后成功仍能钉住并落条。"""
    scale = scale_by_code(client, "甲磅")
    assert client.post("/api/过磅", json={"地磅编号": scale["编号"], "毛重": 100.0, "皮重": 100.0}).status_code == 400
    assert client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]}).status_code == 200
    c = counts(db_path)
    assert c == {"tickets": 1, "holds": 1, "audit": 1}
    assert client.get("/api/过磅对照").json()["一致"] is True


def test_concurrent_grab_for_last_free_scale(client, db_path):
    """并发抢最后一杆空秤：仅一笔成功，其余按同谓词被拒。"""
    scale = scale_by_code(client, "甲磅")

    def attempt(i):
        return client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"], "备注": f"并发{i}"}).status_code

    with ThreadPoolExecutor(max_workers=6) as ex:
        codes = list(ex.map(attempt, range(6)))

    assert codes.count(200) == 1, codes
    assert all(c in (200, 409) for c in codes), codes
    assert all(c != 500 for c in codes), codes

    compare = client.get("/api/过磅对照").json()
    assert compare["一致"] is True
    assert compare["在途钉住数"] == compare["未结磅条数"] == 1
    assert counts(db_path) == {"tickets": 1, "holds": 1, "audit": 1}


def test_receipt_success_moves_pending_section(client):
    """回执成功 → 待结段与地磅钉住态同时反映，不会「回执成功却态势不涨」。"""
    scale = scale_by_code(client, "甲磅")
    r = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]})
    assert r.status_code == 200
    serial = r.json()["磅条号"]

    overview = client.get("/api/总览").json()
    assert overview["分布"]["待定级"] == 1
    assert any(t["磅条号"] == serial and t["钉住"] == "已钉住" for t in overview["待结段"])
    assert any(s["编号"] == scale["编号"] and s["钉住"] == "已钉住" and s["在途磅条编号"] for s in overview["地磅"])


def test_settle_unpins_in_same_commit(client, db_path):
    scale = scale_by_code(client, "甲磅")
    tid = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]}).json()["编号"]
    grade = client.get("/api/品级").json()[0]
    assert client.post(f"/api/定级/{tid}", json={"品级编号": grade["编号"]}).status_code == 200
    r = client.post(f"/api/结价/{tid}")
    assert r.status_code == 200
    assert r.json()["钉住"] == "未钉住"

    gate = client.get(f"/api/地磅/{scale['编号']}/钉住态").json()
    assert gate["can_accept"] in (1, True)
    compare = client.get("/api/过磅对照").json()
    assert compare["一致"] is True
    assert compare["在途钉住数"] == compare["未结磅条数"] == 0


def test_void_unpins_in_same_commit(client):
    scale = scale_by_code(client, "甲磅")
    tid = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]}).json()["编号"]
    r = client.post(f"/api/销单/{tid}")
    assert r.status_code == 200
    assert r.json()["钉住"] == "未钉住"
    assert client.get(f"/api/地磅/{scale['编号']}/钉住态").json()["can_accept"] in (1, True)
    assert client.get("/api/过磅对照").json()["一致"] is True


def test_injected_failure_after_pin_rolls_back_everything(client, db_path, monkeypatch):
    """钉住已经写下去、后面一步炸了：整笔回滚，秤钉无条与审计空白都不许留下。"""
    import app.routers.weigh as weigh_mod

    def boom(*a, **k):
        raise RuntimeError("模拟落条途中失败")

    monkeypatch.setattr(weigh_mod, "hold_scale", boom)
    scale = scale_by_code(client, "甲磅")
    before = counts(db_path)
    with pytest.raises(RuntimeError):
        client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]})

    assert counts(db_path) == before
    gate = client.get(f"/api/地磅/{scale['编号']}/钉住态").json()
    assert gate["can_accept"] in (1, True)  # 秤回到过磅前快照
    assert gate["held"] in (0, False)
    assert client.get("/api/过磅对照").json()["异常"] == []
    assert client.get("/api/审计").json() == []


def test_read_gate_same_predicate_as_write_for_every_scale(client, db_path):
    """逐杆秤重放：读侧说可接 ⇔ 写侧真的能接；读侧说不可接 ⇔ 写侧必被拒。"""
    for scale in client.get("/api/地磅").json():
        readable = bool(scale["可接新磅"])
        before = counts(db_path)
        r = client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"], "备注": f"探针{scale['编号']}"})
        if readable:
            assert r.status_code == 200, (scale, r.text)
        else:
            assert r.status_code in (400, 409), (scale, r.text)
            assert counts(db_path) == before, scale
    assert client.get("/api/过磅对照").json()["一致"] is True


def test_consistency_view_flags_half_writes(client, db_path):
    """直接制造脏写：钉住无条 / 条在秤空，对照必须各自报出。"""
    scale = scale_by_code(client, "甲磅")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 「条在秤空」：一条未结磅条，但没有钉住
        conn.execute(
            """INSERT INTO tickets(serial, site_id, scale_id, shift_id, status,
                    gross_kg, tare_kg, deduct_kg, net_kg, note, created_at)
               VALUES ('磅脏0001', ?, ?, 1, '待定级', 10, 5, 0, 5, '', '2026-01-01 00:00:00')""",
            (scale["回收点编号"], scale["编号"]),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    compare = client.get("/api/过磅对照").json()
    assert compare["一致"] is False
    kinds = {b["异常"] for b in compare["异常"]}
    assert "条在秤空" in kinds

    # 「占用无凭」：把秤标占用但不给钉住
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM tickets WHERE serial='磅脏0001'")
        conn.execute("UPDATE scales SET status='占用' WHERE id=?", (scale["编号"],))
        conn.execute("COMMIT")
    finally:
        conn.close()
    kinds = {b["异常"] for b in client.get("/api/过磅对照").json()["异常"]}
    assert "占用无凭" in kinds
    # 扫描后仍可被写侧 CAS 拒绝（同谓词）
    assert client.post("/api/过磅", json={**WEIGH, "地磅编号": scale["编号"]}).status_code == 409
