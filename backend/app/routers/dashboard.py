from __future__ import annotations
from fastapi import APIRouter
from app.db import db_read, rows_to_list, row_to_dict

router = APIRouter(tags=["总览"])

TICKET_STATUS = ("待定级", "待结价", "已结价", "已销")


@router.get("/总览")
def overview():
    with db_read() as conn:
        shift = conn.execute(
            """
            SELECT id AS 编号, business_day AS 营业日, name AS 班次名,
                   status AS 状态, stamped_at AS 落印时间
            FROM shifts ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        counts = {}
        for name in TICKET_STATUS:
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status=?", (name,)
            ).fetchone()["c"]
        # 待结段：过磅后尚未走完结价/销单的磅条，照出在途钉住。
        pending = rows_to_list(conn.execute(
            """
            SELECT t.id AS 编号, t.serial AS 磅条号, sc.code AS 地磅,
                   st.name AS 回收点, t.status AS 状态, t.net_kg AS 净重,
                   CASE WHEN h.ticket_id IS NULL THEN '未钉住' ELSE '已钉住' END AS 钉住
            FROM tickets t
            JOIN scales sc ON sc.id = t.scale_id
            JOIN sites st ON st.id = t.site_id
            LEFT JOIN scale_holds h ON h.ticket_id = t.id
            WHERE t.status IN ('待定级','待结价')
            ORDER BY t.id DESC LIMIT 50
            """
        ).fetchall())
        scales = rows_to_list(conn.execute(
            """
            SELECT g.scale_id AS 编号, g.code AS 地磅, g.status AS 状态,
                   g.can_accept AS 可接新磅,
                   CASE WHEN g.held THEN '已钉住' ELSE '未钉住' END AS 钉住,
                   g.hold_ticket_id AS 在途磅条编号,
                   st.name AS 回收点, st.status AS 回收点状态
            FROM v_scale_gate g JOIN sites st ON st.id = g.site_id
            ORDER BY g.site_id, g.scale_id
            """
        ).fetchall())
        return {"班次": row_to_dict(shift), "分布": counts, "待结段": pending, "地磅": scales}
