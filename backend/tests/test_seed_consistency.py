from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db as dbmod
from app.seed import seed_if_empty


@pytest.fixture()
def fresh_app(tmp_path, monkeypatch):
    """全新空库，跑一遍种子，用来验证种子自身不产生半截。"""
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "seed.db")
    seed_if_empty()
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_seed_pins_and_ticket_agree(fresh_app):
    compare = fresh_app.get("/api/过磅对照").json()
    assert compare["一致"] is True, compare["异常"]
    assert compare["在途钉住数"] == compare["未结磅条数"] == 1
    overview = fresh_app.get("/api/总览").json()
    assert overview["分布"]["待定级"] == 1
    assert any(s["钉住"] == "已钉住" and s["在途磅条编号"] for s in overview["地磅"])
