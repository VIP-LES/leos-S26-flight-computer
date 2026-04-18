"""
SQLite Log Writer
=================
Owns an SQLite database connection and an in-memory bounded queue of records.
A background task flushes the queue when:
    - accumulated bytes >= LOG_BATCH_BYTES (default 256 KiB), or
    - LOG_FLUSH_MS have elapsed since last flush (default 200 ms).

Drop policy under extreme backpressure:
    - drops oldest non-LowRateAggregate records first,
    - drops oldest record if all queued records are critical.
"""

import asyncio
import os
import sqlite3
import time
import zlib
from collections import deque
from typing import Optional

from fc.config import (
    LOG_DIR,
    LOG_BATCH_BYTES,
    LOG_FLUSH_MS,
    RECORD_KIND_LOW_AGG,
)

# Max queue depth (records, not bytes) — safety net
_MAX_QUEUE_RECORDS = 8192


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rx_mono_ns INTEGER NOT NULL,
    record_kind INTEGER NOT NULL,
    port_id INTEGER NOT NULL,
    payload BLOB NOT NULL,
    payload_len INTEGER NOT NULL,
    payload_crc32 INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_rx_mono_ns ON records(rx_mono_ns);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(record_kind);
"""


class LogWriter:
    """Async SQLite log writer with batched flushing."""

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._log_dir = log_dir or LOG_DIR
        self._conn: Optional[sqlite3.Connection] = None
        self._queue: deque[tuple[int, int, int, bytes, int, int]] = deque()
        # (kind, port_id, rx_ns, payload, payload_len, payload_crc32)
        self._queue_bytes = 0
        self._flush_task: Optional[asyncio.Task] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def open(self) -> None:
        """Create a new SQLite log database and initialize schema."""
        os.makedirs(self._log_dir, exist_ok=True)
        filename = time.strftime("leos_%Y%m%d_%H%M%S.sqlite3")
        path = os.path.join(self._log_dir, filename)

        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
            [
                ("schema_version", "1"),
                ("start_mono_ns", str(time.monotonic_ns())),
            ],
        )
        self._conn.commit()

        # start background flusher
        self._flush_task = asyncio.create_task(self._flush_loop())
        print(f"[log_writer] Logging to {path}")

    async def close(self) -> None:
        """Flush remaining data and close the file."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        self._flush_now()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── public API ───────────────────────────────────────────────────────

    def write(self, *, kind: int, port_id: int, payload: bytes) -> None:
        """
        Enqueue a log record.  Non-blocking; the background task flushes.
        """
        rx_ns = time.monotonic_ns()
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        payload_len = len(payload)

        # Drop policy under overload
        if len(self._queue) >= _MAX_QUEUE_RECORDS:
            self._drop_oldest_non_critical()

        self._queue.append((kind, port_id, rx_ns, payload, payload_len, crc))
        # Approximate queued bytes as payload + small per-record overhead.
        self._queue_bytes += payload_len + 32

    # ── internals ────────────────────────────────────────────────────────

    def _drop_oldest_non_critical(self) -> None:
        """Drop the oldest non-LowRateAggregate record if possible."""
        for i, (kind, _, _, _, payload_len, _) in enumerate(self._queue):
            if kind != RECORD_KIND_LOW_AGG:
                del self._queue[i]
                self._queue_bytes -= payload_len + 32
                return
        # everything is critical — drop oldest anyway
        if self._queue:
            _, _, _, _, payload_len, _ = self._queue.popleft()
            self._queue_bytes -= payload_len + 32

    def _flush_now(self) -> None:
        """Bulk-insert all queued records into SQLite in one transaction."""
        if not self._queue or self._conn is None:
            return

        rows = [
            (rx_ns, kind, port_id, payload, payload_len, crc)
            for (kind, port_id, rx_ns, payload, payload_len, crc) in self._queue
        ]
        self._conn.executemany(
            """
            INSERT INTO records(
                rx_mono_ns,
                record_kind,
                port_id,
                payload,
                payload_len,
                payload_crc32
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()

        self._queue.clear()
        self._queue_bytes = 0

    async def _flush_loop(self) -> None:
        """Background task: flush on size threshold or timer."""
        interval = LOG_FLUSH_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            if self._queue_bytes >= LOG_BATCH_BYTES or self._queue:
                self._flush_now()