from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .models import ReplicateRequest


@dataclass(frozen=True)
class ReplicationResult:
    acks: int
    attempted: int
    latency_ms: float


class ReplicationManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.replicate_timeout_ms / 1000)
        self._background_tasks: set[asyncio.Task] = set()

    async def aclose(self) -> None:
        for t in list(self._background_tasks):
            t.cancel()
        await self._client.aclose()

    def _keep_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(lambda t: self._background_tasks.discard(t))

    async def _replicate_one(self, follower_url: str, payload: ReplicateRequest) -> bool:
        delay_ms = 0
        if self._settings.max_delay_ms > 0 or self._settings.min_delay_ms > 0:
            delay_ms = random.randint(self._settings.min_delay_ms, self._settings.max_delay_ms)
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        # In semi-synchronous replication, an ACK means the follower has *received*
        # the write (typically appended to its log). Whether it was immediately
        # applied can legitimately be false under reordering/lag.
        try:
            r = await self._client.post(f"{follower_url}/internal/replicate", json=payload.model_dump())
            r.raise_for_status()
            return True
        except Exception:
            return False

    async def replicate_and_wait_quorum(self, follower_urls: list[str], payload: ReplicateRequest) -> ReplicationResult:
        """Semi-synchronous replication.

        - Replication to followers is initiated concurrently.
        - The write is acknowledged to the client once `write_quorum` followers confirm.
        - Replication continues to remaining followers in the background.
        """
        start = time.perf_counter()

        if not follower_urls:
            end = time.perf_counter()
            return ReplicationResult(acks=0, attempted=0, latency_ms=(end - start) * 1000)

        tasks = [asyncio.create_task(self._replicate_one(url, payload)) for url in follower_urls]

        # Wait for quorum (or until we run out of tasks/time).
        required = max(0, min(self._settings.write_quorum, len(tasks)))
        acks = 0

        async def wait_for_quorum() -> None:
            nonlocal acks
            for t in asyncio.as_completed(tasks):
                ok = await t
                if ok:
                    acks += 1
                    if acks >= required:
                        return

        try:
            await asyncio.wait_for(wait_for_quorum(), timeout=self._settings.replicate_timeout_ms / 1000)
        except asyncio.TimeoutError:
            pass

        # Keep the remaining tasks running to completion in background.
        for t in tasks:
            if not t.done():
                self._keep_task(t)

        end = time.perf_counter()
        return ReplicationResult(acks=acks, attempted=len(tasks), latency_ms=(end - start) * 1000)
