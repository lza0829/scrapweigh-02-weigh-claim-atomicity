from __future__ import annotations
from fastapi import APIRouter
from app.db import db_read, rows_to_list

router = APIRouter(tags=["诊断"])

# 对照面：把「过磅落条」「秤位钉住」「磅条册」「重量列」「回收态势」摆在一起，
# 任何半截脏写都会以 异常 行出现，正常时为空。
COMPARE_SQL = """
SELECT g.scale_id AS 地磅编号, g.code AS 地磅,
       g.site_id AS 回收点编号, st.name AS 回收点, g.site_status AS 回收点状态,
       g.status AS 秤位态, g.can_accept AS 可接新磅,
       g.held AS 有钉住,
       h.ticket_id AS 在途磅条编号,
       t.serial AS 磅条号, t.status AS 磅条状态,
       sh.business_day AS 营业日, sh.name AS 班次, sh.status AS 班次状态,
       t.gross_kg AS 毛重, t.tare_kg AS 皮重, t.deduct_kg AS 扣杂, t.net_kg AS 净重,
       (SELECT COUNT(*) FROM audit_events a WHERE a.kind='磅条' AND a.ref_id=h.ticket_id) AS 审计条数
FROM v_scale_gate g
JOIN sites st ON st.id = g.site_id
LEFT JOIN scale_holds h ON h.scale_id = g.scale_id
LEFT JOIN tickets t ON t.id = h.ticket_id
LEFT JOIN shifts sh ON sh.id = t.shift_id
ORDER BY g.site_id, g.scale_id
"""


@router.get("/过磅对照")
def compare():
    with db_read() as conn:
        bad = rows_to_list(conn.execute("SELECT * FROM v_weight_consistency ORDER BY 地磅编号").fetchall())
        scales = rows_to_list(conn.execute(COMPARE_SQL).fetchall())
        open_holds = conn.execute(
            "SELECT COUNT(*) AS c FROM scale_holds"
        ).fetchone()["c"]
        open_tickets = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE status IN ('待定级','待结价')"
        ).fetchone()["c"]
        return {
            "一致": len(bad) == 0 and open_holds == open_tickets,
            "在途钉住数": open_holds,
            "未结磅条数": open_tickets,
            "异常": bad,
            "秤位": scales,
        }
