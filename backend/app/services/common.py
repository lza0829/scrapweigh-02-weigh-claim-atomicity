from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException

GATE_SELECT = """
SELECT scale_id, site_id, code, physical_status, site_status,
       fault, legacy_busy, held, hold_ticket_id, held_at, status, can_accept
FROM v_scale_gate
"""

TICKET_SELECT = """
SELECT t.id AS 编号,
       t.serial AS 磅条号,
       t.site_id AS 回收点编号,
       st.name AS 回收点,
       t.scale_id AS 地磅编号,
       sc.code AS 地磅,
       g.status AS 地磅状态,
       g.can_accept AS 可接新磅,
       CASE WHEN h.ticket_id IS NULL THEN '未钉住' ELSE '已钉住' END AS 钉住,
       h.held_at AS 钉住时间,
       t.shift_id AS 班次编号,
       sh.business_day AS 营业日,
       sh.name AS 班次,
       sh.status AS 班次状态,
       t.status AS 状态,
       t.gross_kg AS 毛重,
       t.tare_kg AS 皮重,
       t.deduct_kg AS 扣杂,
       t.net_kg AS 净重,
       t.grade_id AS 品级编号,
       gd.category AS 品类,
       gd.grade_name AS 品级,
       t.locked_price AS 锁价,
       t.amount AS 金额,
       t.note AS 备注,
       t.created_at AS 入站时间,
       t.settled_at AS 结价时间,
       t.voided_at AS 销单时间
FROM tickets t
JOIN sites st ON st.id = t.site_id
JOIN scales sc ON sc.id = t.scale_id
JOIN v_scale_gate g ON g.scale_id = t.scale_id
LEFT JOIN scale_holds h ON h.ticket_id = t.id
JOIN shifts sh ON sh.id = t.shift_id
LEFT JOIN grades gd ON gd.id = t.grade_id
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def audit(conn, kind: str, ref_id: int | None, action: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, action, now_str(), note),
    )


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def current_shift(conn):
    row = conn.execute(
        "SELECT * FROM shifts WHERE status='未落印' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise HTTPException(400, "无未落印班次")
    return row


def assert_shift_writable(conn, shift_id: int) -> None:
    row = conn.execute("SELECT status FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not row:
        raise HTTPException(400, "班次不存在")
    if row["status"] != "未落印":
        raise HTTPException(400, "班次已落印，禁止改磅条")


def next_serial(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"磅{day}"
    row = conn.execute(
        "SELECT serial FROM tickets WHERE serial LIKE ? ORDER BY serial DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = 1
    if row and len(row["serial"]) >= 4:
        try:
            n = int(row["serial"][-4:]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


def ticket_view(conn, ticket_id: int) -> dict | None:
    row = conn.execute(TICKET_SELECT + " WHERE t.id=?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def require_ticket(conn, ticket_id: int):
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "磅条不存在")
    return row


# ---------------------------------------------------------------- 秤位钉住
# 读路径（列表、回执、总览、对照）与写路径（过磅落条）共用同一份谓词：
# v_scale_gate.can_accept。任何地方都不得另写一套「空闲即可用」的判断。
# ---------------------------------------------------------------------------

def scale_gate(conn, scale_id: int) -> dict:
    row = conn.execute(GATE_SELECT + " WHERE scale_id=?", (scale_id,)).fetchone()
    if not row:
        raise HTTPException(404, "地磅不存在")
    return dict(row)


def gate_reject_reason(gate: dict) -> str:
    if gate["fault"]:
        return "地磅故障，不能过磅"
    if gate["held"] or gate["legacy_busy"]:
        return "地磅占用中，已有在途磅条"
    if gate["site_status"] != "营业中":
        return f"回收点{gate['site_status']}，不能过磅"
    return "地磅不可占用"


def pin_scale(conn, scale_id: int) -> None:
    """写侧 CAS：以 v_scale_gate.can_accept 为条件把秤位钉到占用。

    与在读路径上的判断共用同一视图，保证「读得出可接」与「写得进」不会分叉。
    调用方必须在同一事务内随后写入磅条与钉住见证；否则本 CAS 本身就是半截。
    """
    cur = conn.execute(
        """
        UPDATE scales SET status='占用'
        WHERE id=? AND (SELECT can_accept FROM v_scale_gate WHERE scale_id=scales.id)
        """,
        (scale_id,),
    )
    if cur.rowcount == 1:
        return
    gate = scale_gate(conn, scale_id)
    # 争用/已钉住 → 409；业务规则（故障、回收点停收）→ 400。
    code = 409 if (gate["held"] or gate["legacy_busy"]) else 400
    raise HTTPException(code, gate_reject_reason(gate))


def hold_scale(conn, scale_id: int, ticket_id: int) -> None:
    """在途钉住见证；与磅条同一提交写入。"""
    conn.execute(
        "INSERT INTO scale_holds(scale_id, ticket_id, held_at) VALUES (?, ?, ?)",
        (scale_id, ticket_id, now_str()),
    )


def unpin_ticket(conn, ticket_id: int) -> None:
    """结价/销单时解钉。只在与该磅条状态变更同一事务内调用。

    若同秤仍有其它未结磅条（旧库脏数据），把钉住转交给最早那一条，
    使「占用 ⇔ 有在途钉住」不因解钉而被破坏。
    """
    hold = conn.execute(
        "SELECT scale_id FROM scale_holds WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    if not hold:
        return
    scale_id = hold["scale_id"]
    conn.execute("DELETE FROM scale_holds WHERE ticket_id=?", (ticket_id,))
    successor = conn.execute(
        """
        SELECT t.id, t.created_at FROM tickets t
        WHERE t.scale_id=? AND t.status IN ('待定级','待结价')
        ORDER BY t.id LIMIT 1
        """,
        (scale_id,),
    ).fetchone()
    if successor:
        conn.execute(
            "INSERT INTO scale_holds(scale_id, ticket_id, held_at) VALUES (?, ?, ?)",
            (scale_id, successor["id"], successor["created_at"]),
        )
        return
    conn.execute(
        "UPDATE scales SET status='空闲' WHERE id=? AND status='占用'", (scale_id,)
    )
