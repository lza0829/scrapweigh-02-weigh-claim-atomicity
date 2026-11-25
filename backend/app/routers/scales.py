from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, db_read, rows_to_list, row_to_dict
from app.services.common import audit, scale_gate

router = APIRouter(tags=["地磅"])

SCALE_SQL = """
SELECT g.scale_id AS 编号, g.code AS 地磅, g.status AS 状态,
       g.can_accept AS 可接新磅,
       CASE WHEN g.held THEN '已钉住' ELSE '未钉住' END AS 钉住,
       g.hold_ticket_id AS 在途磅条编号, g.held_at AS 钉住时间,
       g.site_id AS 回收点编号, st.name AS 回收点, g.site_status AS 回收点状态,
       sc.note AS 备注
FROM v_scale_gate g
JOIN scales sc ON sc.id = g.scale_id
JOIN sites st ON st.id = g.site_id
"""


class ScaleIn(BaseModel):
    回收点编号: int
    地磅: str = Field(min_length=1)
    备注: str = ""


class StatusIn(BaseModel):
    状态: str


def _scale(conn, scale_id: int):
    return row_to_dict(conn.execute(SCALE_SQL + " WHERE g.scale_id=?", (scale_id,)).fetchone())


@router.get("/地磅")
def list_scales():
    with db_read() as conn:
        return rows_to_list(conn.execute(SCALE_SQL + " ORDER BY g.site_id, g.scale_id").fetchall())


@router.get("/地磅/{scale_id}/钉住态")
def scale_gate_view(scale_id: int):
    """单秤「是否可接新磅」——由 v_scale_gate 直接给出，供前端与对照使用。"""
    with db_read() as conn:
        gate = scale_gate(conn, scale_id)
        ticket = None
        if gate["hold_ticket_id"] is not None:
            ticket = row_to_dict(conn.execute(
                "SELECT id AS 编号, serial AS 磅条号, status AS 状态, net_kg AS 净重"
                " FROM tickets WHERE id=?",
                (gate["hold_ticket_id"],),
            ).fetchone())
        return {**gate, "ticket": ticket}


@router.post("/地磅")
def create_scale(body: ScaleIn):
    with db() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id=?", (body.回收点编号,)).fetchone()
        if not site:
            raise HTTPException(404, "回收点不存在")
        if site["status"] == "已注销":
            raise HTTPException(400, "已注销回收点不能新增地磅")
        try:
            conn.execute(
                "INSERT INTO scales(site_id, code, status, note) VALUES (?, ?, '空闲', ?)",
                (body.回收点编号, body.地磅, body.备注),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute(
            "SELECT id FROM scales WHERE site_id=? AND code=?",
            (body.回收点编号, body.地磅),
        ).fetchone()
        audit(conn, "地磅", row["id"], "新建", body.地磅)
        return _scale(conn, row["id"])


@router.post("/地磅/{scale_id}/状态")
def set_scale_status(scale_id: int, body: StatusIn):
    if body.状态 not in ("空闲", "故障"):
        raise HTTPException(400, "只能改为空闲或故障")
    with db() as conn:
        gate = scale_gate(conn, scale_id)
        if gate["held"]:
            raise HTTPException(400, "地磅已有在途磅条，不能改状态")
        if body.状态 == "故障" and gate["status"] != "空闲":
            raise HTTPException(400, "仅空闲可标故障")
        if body.状态 == "空闲" and gate["status"] != "故障":
            raise HTTPException(400, "仅故障可恢复空闲")
        conn.execute("UPDATE scales SET status=? WHERE id=?", (body.状态, scale_id))
        audit(conn, "地磅", scale_id, "改状态", body.状态)
        return _scale(conn, scale_id)
