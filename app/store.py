from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredValue:
    value: str
    seq: int


class KvStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._data: dict[str, StoredValue] = {}
        self._last_seq: int = 0

    async def get(self, key: str) -> StoredValue | None:
        async with self._lock:
            return self._data.get(key)

    async def dump(self) -> dict[str, dict]:
        async with self._lock:
            return {k: {"value": v.value, "seq": v.seq} for k, v in self._data.items()}

    async def next_seq(self) -> int:
        async with self._lock:
            self._last_seq += 1
            return self._last_seq

    async def apply(self, key: str, value: str, seq: int) -> bool:
        """Apply a replication event.

        Returns True if applied; False if ignored as stale.
        """
        async with self._lock:
            current = self._data.get(key)
            if current is not None and seq < current.seq:
                return False
            self._data[key] = StoredValue(value=value, seq=seq)
            if seq > self._last_seq:
                self._last_seq = seq
            return True
