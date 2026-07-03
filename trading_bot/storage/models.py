"""Хранилище на SQLite: сделки, сигналы, снимки equity.

На старте — SQLite (стандартная библиотека, без внешних зависимостей). Схема
готова к расширению; переход на PostgreSQL возможен позже (раздел 2 ТЗ).

Логируем не только сделки, но и КАЖДОЕ решение стратегии (сигналы) — для
последующего разбора (раздел 10 ТЗ).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..logger import get_logger

log = get_logger("storage.models")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms        INTEGER NOT NULL,        -- время решения (мс)
    symbol       TEXT    NOT NULL,
    side         TEXT,                    -- Buy | Sell | None (нет входа)
    score        REAL,                    -- суммарный confluence-скор
    threshold    REAL,
    entered      INTEGER NOT NULL DEFAULT 0,  -- 0/1: привёл ли к входу
    details_json TEXT                     -- разбор очков/индикаторов
);

CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open_ms     INTEGER NOT NULL,
    ts_close_ms    INTEGER,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL,      -- Buy | Sell
    qty            REAL    NOT NULL,
    entry_price    REAL    NOT NULL,
    stop_price     REAL,
    take_price     REAL,
    exit_price     REAL,
    leverage       REAL,
    liq_price      REAL,
    pnl            REAL,
    fees           REAL,
    funding        REAL,
    status         TEXT    NOT NULL DEFAULT 'open',  -- open | closed | cancelled
    exchange_order_id TEXT,
    signal_id      INTEGER,
    details_json   TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms     INTEGER NOT NULL,
    equity    REAL    NOT NULL,
    coin      TEXT    NOT NULL DEFAULT 'USDT',
    source    TEXT                                  -- 'rest' | 'ws' | 'backtest'
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts_ms);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(ts_ms);
"""


class Storage:
    """Тонкий слой над sqlite3 с типовыми операциями бота."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("Storage готов: %s", db_path)

    # --- signals ---
    def record_signal(self, ts_ms: int, symbol: str, side: Optional[str],
                      score: Optional[float], threshold: Optional[float],
                      entered: bool, details: Optional[dict[str, Any]] = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO signals (ts_ms, symbol, side, score, threshold, entered, details_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (ts_ms, symbol, side, score, threshold, int(entered),
             json.dumps(details, ensure_ascii=False, default=str) if details else None),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # --- trades ---
    def record_trade_open(self, **fields: Any) -> int:
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        cur = self._conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def close_trade(self, trade_id: int, **fields: Any) -> None:
        fields.setdefault("status", "closed")
        assignments = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(
            f"UPDATE trades SET {assignments} WHERE id=?",
            (*fields.values(), trade_id),
        )
        self._conn.commit()

    def open_trades(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM trades WHERE status='open'"
        ).fetchall()

    # --- equity ---
    def record_equity(self, ts_ms: int, equity: float,
                      coin: str = "USDT", source: str = "rest") -> int:
        cur = self._conn.execute(
            "INSERT INTO equity_snapshots (ts_ms, equity, coin, source) VALUES (?,?,?,?)",
            (ts_ms, equity, coin, source),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def peak_equity(self, coin: str = "USDT") -> Optional[float]:
        row = self._conn.execute(
            "SELECT MAX(equity) AS peak FROM equity_snapshots WHERE coin=?", (coin,)
        ).fetchone()
        return row["peak"] if row and row["peak"] is not None else None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
