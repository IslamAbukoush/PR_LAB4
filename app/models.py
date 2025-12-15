from __future__ import annotations

from pydantic import BaseModel, Field


class PutRequest(BaseModel):
    value: str = Field(..., min_length=0)


class PutResponse(BaseModel):
    key: str
    value: str
    seq: int
    acks: int
    quorum: int
    replicated_to: int


class GetResponse(BaseModel):
    key: str
    value: str | None
    seq: int | None


class ReplicateRequest(BaseModel):
    key: str
    value: str
    seq: int


class ReplicateResponse(BaseModel):
    applied: bool
