"""SQLite 持久化：设置 / 记录（去重） / 每日计数。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path


class Store:
    def __init__(self, db_path: str):
        parent = Path(db_path).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records(
                    task_id TEXT PRIMARY KEY,
                    source_url TEXT,
                    source_msg_id INTEGER,
                    status TEXT,
                    target_msg_id INTEGER,
                    error TEXT,
                    created_at REAL,
                    done_at REAL
                );
                CREATE TABLE IF NOT EXISTS daily_counts(
                    day TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_records_url ON records(source_url, status);
                """
            )

    # ---------- settings ----------
    def get_setting(self, key: str, default=None):
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    # ---------- records / dedup ----------
    def already_done(self, source_url: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT 1 FROM records WHERE source_url=? AND status='done' LIMIT 1",
                (source_url,),
            ).fetchone()
        return row is not None

    def add_record(self, task_id: str, source_url: str, source_msg_id=None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO records(task_id, source_url, source_msg_id, status, created_at) "
                "VALUES(?,?,?,?,?)",
                (task_id, source_url, source_msg_id, "pending", time.time()),
            )

    def mark_done(self, task_id: str, target_msg_id=None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE records SET status='done', target_msg_id=?, done_at=? WHERE task_id=?",
                (target_msg_id, time.time(), task_id),
            )

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE records SET status='failed', error=?, done_at=? WHERE task_id=?",
                (str(error)[:500], time.time(), task_id),
            )

    # ---------- daily counter ----------
    def bump_today(self, n: int = 1) -> int:
        today = date.today().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO daily_counts(day,count) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET count=count+excluded.count",
                (today, n),
            )
            row = self._conn.execute(
                "SELECT count FROM daily_counts WHERE day=?", (today,)
            ).fetchone()
        return row["count"] if row else 0

    def today_count(self) -> int:
        today = date.today().isoformat()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT count FROM daily_counts WHERE day=?", (today,)
            ).fetchone()
        return row["count"] if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
