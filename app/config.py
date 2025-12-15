from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    role: str  # leader | follower
    node_id: str

    # Network
    host: str = "0.0.0.0"
    port: int = 8000

    # Leader-only
    follower_urls: list[str] = []
    write_quorum: int = 1
    replicate_timeout_ms: int = 2000

    min_delay_ms: int = 0
    max_delay_ms: int = 0

    @staticmethod
    def from_env() -> "Settings":
        import os

        role = os.environ.get("ROLE", "leader").strip().lower()
        node_id = os.environ.get("NODE_ID", role).strip()

        host = os.environ.get("HOST", "0.0.0.0").strip()
        port = int(os.environ.get("PORT", "8000"))

        follower_urls_raw = os.environ.get("FOLLOWER_URLS", "").strip()
        follower_urls = [u.strip() for u in follower_urls_raw.split(",") if u.strip()]

        write_quorum = int(os.environ.get("WRITE_QUORUM", "1"))
        replicate_timeout_ms = int(os.environ.get("REPLICATE_TIMEOUT_MS", "2000"))

        min_delay_ms = int(os.environ.get("MIN_DELAY_MS", "0"))
        max_delay_ms = int(os.environ.get("MAX_DELAY_MS", "0"))

        return Settings(
            role=role,
            node_id=node_id,
            host=host,
            port=port,
            follower_urls=follower_urls,
            write_quorum=write_quorum,
            replicate_timeout_ms=replicate_timeout_ms,
            min_delay_ms=min_delay_ms,
            max_delay_ms=max_delay_ms,
        )
