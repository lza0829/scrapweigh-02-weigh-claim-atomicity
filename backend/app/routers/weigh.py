from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, db_read, rows_to_list
from app.services.common import (
    TICKET_SELECT,
    audit,
    current_shift,
    get_setting,
    hold_scale,
    next_serial,
    now_str,
    pin_scale,
    ticket_view,
)

router = APIRouter(tags=["过磅"])


class WeighIn(BaseModel):
    地磅编号: int
    回收点编号: int | None = None
    毛重: float = Field(gt=0)
    皮重: float = Field(ge=0)
    扣杂: float | None = None
    备注: str = ""


@router.get("/过磅")
def list_open_weighs():
    with db_read() as conn:
        return rows_to_list(conn.execute(
            TICKET_SELECT + " WHERE t.status IN ('待定级','待结价') ORDER BY t.id DESC LIMIT 100"
        ).fetchall())


@router.post("/过磅")
def admit(body: WeighIn):
    """过磅入站：钉住秤位 + 落磅条 + 重量列 + 审计同笔成败。

    同一 BEGIN IMMEDIATE 事务内依次：校验收抬点/重量 → 以共享谓词 CAS 钉住 →
    插入磅条 → 写钉住见证 → 审计 → 自检对照视图 → 提交。
    任一步失败，整笔回滚：秤回到过磅前快照，无磅条、无审计、无钉住。
    """
    with db() as conn:
        shift = current_shift(conn)

        # —— 快照：用于失败时自证「整笔没留下东西」。事务回滚本就保证，
        #    这里再读一次只为了让错误信息能说清回到了什么状态。
        scale = conn.execute("SELECT * FROM scales WHERE id=?", (body.地磅编号,)).fetchone()
        if not scale:
            raise HTTPException(404, "地磅不存在")
        site = conn.execute("SELECT * FROM sites WHERE id=?", (scale["site_id"],)).fetchone()
        if not site:
            raise HTTPException(400, "回收点不存在")
        if body.回收点编号 is not None and body.回收点编号 != site["id"]:
            raise HTTPException(400, "地磅不属于该回收点")
        if body.皮重 >= body.毛重:
            raise HTTPException(400, "皮重须小于毛重")
        try:
            cap = float(get_setting(conn, "毛重软上限", "40000"))
        except ValueError:
            cap = 40000.0
        if body.毛重 > cap:
            raise HTTPException(400, "毛重超过软上限")
        if body.扣杂 is None:
            try:
                deduct = float(get_setting(conn, "扣杂默认", "0"))
            except ValueError:
                deduct = 0.0
        else:
            deduct = body.扣杂
        if deduct < 0:
            raise HTTPException(400, "扣杂不能为负")
        net = round(body.毛重 - body.皮重 - deduct, 3)
        if net <= 0:
            raise HTTPException(400, "净重须大于零")

        # —— 钉住：写侧 CAS，谓词来自 v_scale_gate.can_accept，
        #    与读路径「是否可接新磅」同源。
        pin_scale(conn, scale["id"])

        serial = next_serial(conn)
        conn.execute(
            """
            INSERT INTO tickets(
              serial, site_id, scale_id, shift_id, status,
              gross_kg, tare_kg, deduct_kg, net_kg, note, created_at
            ) VALUES (?, ?, ?, ?, '待定级', ?, ?, ?, ?, ?, ?)
            """,
            (
                serial, site["id"], scale["id"], shift["id"],
                body.毛重, body.皮重, deduct, net, body.备注, now_str(),
            ),
        )
        row = conn.execute("SELECT id FROM tickets WHERE serial=?", (serial,)).fetchone()
        hold_scale(conn, scale["id"], row["id"])
        audit(conn, "磅条", row["id"], "过磅入站", serial)

        # —— 提交前自检：钉住与磅条必须正好一一对应，否则整笔回滚。
        bad = conn.execute(
            "SELECT 异常, 说明 FROM v_weight_consistency WHERE 地磅编号=? LIMIT 1",
            (scale["id"],),
        ).fetchone()
        if bad:
            raise HTTPException(500, f"过磅未落稳（{bad['异常']}：{bad['说明']}），已整笔回滚")

        return ticket_view(conn, row["id"])
