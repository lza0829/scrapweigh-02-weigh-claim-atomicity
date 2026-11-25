from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    import app.db as dbmod

    path = tmp_path / "scrapweigh.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    return path


@pytest.fixture()
def client(db_path):
    """干净的基线：保留回收点/地磅/班次/品级/设置，清掉磅条、钉住与审计。"""
    import app.db as dbmod
    from app.seed import seed_if_empty

    seed_if_empty()
    conn = dbmod.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM scale_holds")
        conn.execute("DELETE FROM audit_events")
        conn.execute("DELETE FROM tickets")
        conn.execute("UPDATE scales SET status='空闲' WHERE status='占用'")
        conn.execute("COMMIT")
    finally:
        conn.close()

    from app.main import app

    with TestClient(app) as c:
        yield c


def counts(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return {
            "tickets": conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
            "holds": conn.execute("SELECT COUNT(*) FROM scale_holds").fetchone()[0],
            "audit": conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
        }
    finally:
        conn.close()


def scale_by_code(client, code, site="城南旧物点"):
    for s in client.get("/api/地磅").json():
        if s["地磅"] == code and s["回收点"] == site:
            return s
    raise AssertionError(f"找不到地磅 {site}/{code}")
