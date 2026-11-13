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
import json
from collections import deque
from typing import Optional

from fc.config import (
    LOG_DIR,
    LOG_BATCH_SIZE,
    LOG_FLUSH_MS,
    RECORD_KIND_LOW_AGG,
    MAX_QUEUE_RECORDS
)

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
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_rx_mono_ns ON records(rx_mono_ns);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(record_kind);
"""


class LogWriter:
    """Async SQLite log writer with batched flushing."""

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._log_dir = log_dir or LOG_DIR
        self._conn: Optional[sqlite3.Connection] = None
        self._queue: deque[tuple[int, int, int, dict[str, any]]] = deque()
        # (kind, port_id, rx_ns, payload)
        self._flush_task: Optional[asyncio.Task] = None
        self._notify_event: Optional[asyncio.Event] = None
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
        self._notify_event = asyncio.Event()
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._flush_task.add_done_callback(self._handle_task_exception)
        print(f"[log_writer] Logging to {path}")

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Callback to handle background task results and raise exceptions."""
        try:
            # If the task failed with an exception, this raises it
            task.result()
        except asyncio.CancelledError:
            pass  # Expected when close() cancels the task
        except Exception as e:
            print(f"\n[log_writer] CRITICAL: Background task failed!", flush=True)
            # This re-raises the exception on the main event loop
            raise e

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

    def write(self, *, kind: int, port_id: int, payload: dict[str, any]) -> None:
        """
        Enqueue a log record.  Non-blocking; the background task flushes.
        """
        rx_ns = time.monotonic_ns()

        # Drop policy under overload
        if len(self._queue) >= MAX_QUEUE_RECORDS:
            self._drop_oldest_non_critical()

        if len(self._queue) >= LOG_BATCH_SIZE:
            self._notify_event.set()

        self._queue.append((kind, port_id, rx_ns, payload))

    # ── internals ────────────────────────────────────────────────────────

    def _drop_oldest_non_critical(self) -> None:
        """Drop the oldest non-LowRateAggregate record if possible."""
        for i, (kind, _, _, _) in enumerate(self._queue):
            if kind != RECORD_KIND_LOW_AGG:
                del self._queue[i]
                return
        # everything is critical — drop oldest anyway
        if self._queue:
            self._queue.popleft()

    def _flush_now(self) -> None:
        """Bulk-insert all queued records into SQLite in one transaction."""
        if not self._queue or self._conn is None:
            return

        rows = [
            (rx_ns, kind, port_id, json.dumps(payload))
            for (kind, port_id, rx_ns, payload) in self._queue
        ]
        self._conn.executemany(
            """
            INSERT INTO records(
                rx_mono_ns,
                record_kind,
                port_id,
                payload
            ) VALUES (?, ?, ?, jsonb(?))
            """,
            rows,
        )
        self._conn.commit()

        self._queue.clear()
        self._queue_bytes = 0

    async def _flush_loop(self) -> None:
        """Background task: flush on size notification or timer timeout."""
        timeout_seconds = LOG_FLUSH_MS / 1000.0
        print(f"Timeout {timeout_seconds}")
        while True:
            try:
                # Wait for write() to trigger the event, or hit the flush timer
                async with asyncio.timeout(timeout_seconds):
                    await self._notify_event.wait()
            except TimeoutError:
                # Timer elapsed without hitting batch size
                pass
            
            self._notify_event.clear() 
            print(f"[log_writer] Flushing data...")
            self._flush_now()
