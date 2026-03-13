"""
Binary Log Writer
==================
Owns a file descriptor and an in-memory bounded queue of record bytes.
A background task flushes the queue when:
  • accumulated bytes ≥ LOG_BATCH_BYTES  (default 256 KiB), **or**
  • LOG_FLUSH_MS have elapsed since last flush (default 200 ms).

Record format on disk
---------------------
Header (written once at file creation):
    magic          : 8 bytes   b"LEOSLOG1"
    version        : uint16
    start_mono_ns  : uint64

Record (repeated):
    rx_mono_ns     : uint64    monotonic time when the RPi received/built it
    record_kind    : uint16    (1 = LowRateAggregate, 2 = EFM, 3 = status …)
    port_id        : uint16
    payload_len    : uint32
    payload_bytes  : <payload_len bytes>
    crc32          : uint32

Drop policy — under extreme backpressure the writer:
  • drops oldest EFM records first,
  • only drops LowRateAggregate records after the queue is completely overloaded.
"""

import asyncio
import os
import struct
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

# ── Wire format constants ───────────────────────────────────────────────────
_MAGIC = b"LEOSLOG1"
_HEADER_VERSION = 1
_HEADER_STRUCT = struct.Struct("<8sHQ")          # magic(8) + version(2) + start_ns(8)
_RECORD_HEAD   = struct.Struct("<QHHI")          # rx_ns(8) + kind(2) + port(2) + len(4)
_CRC_STRUCT    = struct.Struct("<I")             # crc32(4)

# Max queue depth (records, not bytes) — safety net
_MAX_QUEUE_RECORDS = 8192


class LogWriter:
    """Async binary log writer with batched flushing."""

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._log_dir = log_dir or LOG_DIR
        self._fd: Optional[int] = None
        self._queue: deque[tuple[int, bytes]] = deque()  # (kind, raw_record)
        self._queue_bytes = 0
        self._flush_task: Optional[asyncio.Task] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def open(self) -> None:
        """Create (or append to) the log file and write the header."""
        os.makedirs(self._log_dir, exist_ok=True)
        filename = time.strftime("leos_%Y%m%d_%H%M%S.bin")
        path = os.path.join(self._log_dir, filename)

        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        header = _HEADER_STRUCT.pack(_MAGIC, _HEADER_VERSION, time.monotonic_ns())
        os.write(self._fd, header)

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
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    # ── public API ───────────────────────────────────────────────────────

    def write(self, *, kind: int, port_id: int, payload: bytes) -> None:
        """
        Enqueue a log record.  Non-blocking; the background task flushes.
        """
        rx_ns = time.monotonic_ns()
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        raw = (
            _RECORD_HEAD.pack(rx_ns, kind, port_id, len(payload))
            + payload
            + _CRC_STRUCT.pack(crc)
        )

        # Drop policy under overload
        if len(self._queue) >= _MAX_QUEUE_RECORDS:
            self._drop_oldest_non_critical()

        self._queue.append((kind, raw))
        self._queue_bytes += len(raw)

    # ── internals ────────────────────────────────────────────────────────

    def _drop_oldest_non_critical(self) -> None:
        """Drop the oldest non-LowRateAggregate record if possible."""
        for i, (kind, raw) in enumerate(self._queue):
            if kind != RECORD_KIND_LOW_AGG:
                del self._queue[i]
                self._queue_bytes -= len(raw)
                return
        # everything is critical — drop oldest anyway
        if self._queue:
            _, raw = self._queue.popleft()
            self._queue_bytes -= len(raw)

    def _flush_now(self) -> None:
        """Write all queued records to disk in one syscall batch."""
        if not self._queue or self._fd is None:
            return
        buf = b"".join(raw for _, raw in self._queue)
        os.write(self._fd, buf)
        self._queue.clear()
        self._queue_bytes = 0

    async def _flush_loop(self) -> None:
        """Background task: flush on size threshold or timer."""
        interval = LOG_FLUSH_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            if self._queue_bytes >= LOG_BATCH_BYTES or self._queue:
                self._flush_now()