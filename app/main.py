from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config import Settings
from .models import (
    GetResponse,
    PutRequest,
    PutResponse,
    ReplicateRequest,
    ReplicateResponse,
)
from .replication import ReplicationManager
from .store import KvStore


def create_app() -> FastAPI:
    settings = Settings.from_env()
    store = KvStore()

    replication: ReplicationManager | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal replication
        if settings.role == "leader":
            replication = ReplicationManager(settings)
        yield
        if replication is not None:
            await replication.aclose()

    app = FastAPI(title=f"kv-{settings.role}", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True, "role": settings.role, "node_id": settings.node_id}

    @app.get("/kv/{key}", response_model=GetResponse)
    async def get_key(key: str):
        item = await store.get(key)
        if item is None:
            return GetResponse(key=key, value=None, seq=None)
        return GetResponse(key=key, value=item.value, seq=item.seq)

    @app.get("/dump")
    async def dump():
        return {
            "role": settings.role,
            "node_id": settings.node_id,
            "data": await store.dump(),
        }

    @app.put("/kv/{key}", response_model=PutResponse)
    async def put_key(key: str, body: PutRequest):
        if settings.role != "leader":
            raise HTTPException(status_code=403, detail="Writes must go to leader")

        seq = await store.next_seq()
        await store.apply(key=key, value=body.value, seq=seq)

        payload = ReplicateRequest(key=key, value=body.value, seq=seq)

        if replication is None:
            raise HTTPException(status_code=500, detail="Replication manager not initialized")

        result = await replication.replicate_and_wait_quorum(settings.follower_urls, payload)

        if result.acks < max(0, min(settings.write_quorum, len(settings.follower_urls))):
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "write failed to reach quorum",
                    "acks": result.acks,
                    "quorum": settings.write_quorum,
                    "replicated_to": result.attempted,
                },
            )

        return PutResponse(
            key=key,
            value=body.value,
            seq=seq,
            acks=result.acks,
            quorum=settings.write_quorum,
            replicated_to=result.attempted,
        )

    @app.post("/internal/replicate", response_model=ReplicateResponse)
    async def replicate(req: ReplicateRequest):
        if settings.role != "follower":
            raise HTTPException(status_code=403, detail="Leader does not accept replicate calls")

        applied = await store.apply(key=req.key, value=req.value, seq=req.seq)
        # Simulate concurrent handling by yielding occasionally.
        await asyncio.sleep(0)
        return ReplicateResponse(applied=applied)

    return app


app = create_app()
