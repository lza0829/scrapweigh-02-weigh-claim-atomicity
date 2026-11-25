from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '营业中' CHECK(status IN ('营业中','停收','已注销')),
  address TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲' CHECK(status IN ('空闲','占用','故障')),
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(site_id, code),
  FOREIGN KEY(site_id) REFERENCES sites(id)
);
CREATE TABLE IF NOT EXISTS grades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  grade_name TEXT NOT NULL,
  unit_price REAL NOT NULL,
  UNIQUE(category, grade_name)
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '未落印' CHECK(status IN ('未落印','已落印')),
  stamped_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  site_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  shift_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待定级' CHECK(status IN ('待定级','待结价','已结价','已销')),
  gross_kg REAL NOT NULL,
  tare_kg REAL NOT NULL,
  deduct_kg REAL NOT NULL DEFAULT 0,
  net_kg REAL NOT NULL,
  grade_id INTEGER,
  locked_price REAL,
  amount REAL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  settled_at TEXT,
  voided_at TEXT,
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(grade_id) REFERENCES grades(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);

-- 秤位钉住见证：一个地磅至多一条在途钉住（scale_id 主键），
-- 且必须指向一条真实磅条（ticket_id 非空唯一 + 外键）。
-- 「钉住」的定义就是本表存在这一行；没有行就是没钉住。
-- 不存在「只有钉住、还没有磅条」的在途凭证形态。
CREATE TABLE IF NOT EXISTS scale_holds (
  scale_id INTEGER PRIMARY KEY,
  ticket_id INTEGER NOT NULL UNIQUE,
  held_at TEXT NOT NULL,
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);

-- 秤位钉住谓词。写入侧 CAS 与读侧「是否可接新磅」共用本视图，
-- 不允许另写一份判断。
--   can_accept ⇔ 地磅物理状态空闲 且 回收点营业中 且 无在途钉住。
-- 「占用」是派生态：有在途钉住，或旧库遗留的占用标记。
CREATE VIEW IF NOT EXISTS v_scale_gate AS
SELECT sc.id AS scale_id,
       sc.site_id AS site_id,
       sc.code AS code,
       sc.status AS physical_status,
       st.status AS site_status,
       (sc.status = '故障') AS fault,
       (sc.status = '占用') AS legacy_busy,
       (h.ticket_id IS NOT NULL) AS held,
       h.ticket_id AS hold_ticket_id,
       h.held_at AS held_at,
       CASE WHEN sc.status = '故障' THEN '故障'
            WHEN h.ticket_id IS NOT NULL OR sc.status = '占用' THEN '占用'
            ELSE '空闲' END AS status,
       (sc.status = '空闲'
        AND st.status = '营业中'
        AND h.ticket_id IS NULL) AS can_accept
FROM scales sc
JOIN sites st ON st.id = sc.site_id
LEFT JOIN scale_holds h ON h.scale_id = sc.id;

-- 对照视图：任何「秤钉无条」或「条在秤空」都必须在此暴露出来。
-- 正常的在途态 ⇔ 每个未结磅条恰有一条同秤钉住，且每个钉住恰好对应一条未结磅条。
CREATE VIEW IF NOT EXISTS v_weight_consistency AS
SELECT '秤钉无条' AS 异常,
       g.scale_id AS 地磅编号, g.code AS 地磅, st.name AS 回收点,
       NULL AS 磅条编号, NULL AS 磅条号,
       '钉住行指向的磅条缺失、已结或不在本秤' AS 说明
FROM v_scale_gate g
JOIN sites st ON st.id = g.site_id
LEFT JOIN tickets t ON t.id = g.hold_ticket_id
WHERE g.held
  AND (t.id IS NULL OR t.status NOT IN ('待定级','待结价') OR t.scale_id != g.scale_id)
UNION ALL
SELECT '条在秤空',
       t.scale_id, g.code, st.name, t.id, t.serial,
       '未结磅条没有对应的在途钉住'
FROM tickets t
JOIN v_scale_gate g ON g.scale_id = t.scale_id
JOIN sites st ON st.id = g.site_id
LEFT JOIN scale_holds h ON h.ticket_id = t.id
WHERE t.status IN ('待定级','待结价')
  AND (h.ticket_id IS NULL OR h.scale_id != t.scale_id)
UNION ALL
SELECT '占用无凭',
       g.scale_id, g.code, st.name, NULL, NULL,
       '旧占用标记没有在途钉住，也不指向任何未结磅条'
FROM v_scale_gate g
JOIN sites st ON st.id = g.site_id
WHERE g.legacy_busy AND NOT g.held;
"""

TICKET_STATUSES = ("待定级", "待结价", "已结价", "已销")
SCALE_STATUSES = ("空闲", "占用", "故障")
SITE_STATUSES = ("营业中", "停收", "已注销")
SHIFT_STATUSES = ("未落印", "已落印")

BUSY_TIMEOUT_MS = 15000


def connect() -> sqlite3.Connection:
    """每次调用一个连接；写入事务一律 BEGIN IMMEDIATE，杜绝读后升级锁。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    """写事务：进入即拿写锁，正常退出提交，任何异常整笔回滚。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def db_read():
    """读路径：单一只读事务，块内多条查询共享同一快照，避免并发写把对照读花。"""
    conn = connect()
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _backfill_holds(conn)
    finally:
        conn.close()

def _backfill_holds(conn) -> None:
    """为旧库里已存在但尚无钉住见证的未结磅条补见证行。

    只补「未结磅条 → 钉住」方向，且同秤仅取最早一条；
    不会把「占用标记无磅条」静默修掉 —— 那是脏状态，留给对照视图暴露。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.scale_id, t.created_at
            FROM tickets t
            LEFT JOIN scale_holds h ON h.ticket_id = t.id
            WHERE t.status IN ('待定级','待结价') AND h.ticket_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM scale_holds x WHERE x.scale_id = t.scale_id)
            ORDER BY t.scale_id, t.id
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT OR IGNORE INTO scale_holds(scale_id, ticket_id, held_at) VALUES (?, ?, ?)",
                (row["scale_id"], row["id"], row["created_at"]),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
