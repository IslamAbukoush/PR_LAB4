import os
import subprocess
import time

import httpx
import pytest


COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")


def _run_compose(args: list[str], env: dict[str, str] | None = None) -> None:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=True,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _try_compose(args: list[str]) -> None:
    try:
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


async def _wait_ok(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.json().get("ok") is True:
                    return
            except Exception:
                pass
            await asyncio_sleep(0.25)

    raise AssertionError(f"Service did not become healthy: {url}")


async def asyncio_sleep(seconds: float) -> None:
    # Avoid importing asyncio at module import time (keeps test output cleaner on some setups)
    import asyncio

    await asyncio.sleep(seconds)


@pytest.fixture(scope="session", autouse=True)
def _compose_stack():
    if not _docker_available():
        pytest.skip("Docker engine not available (is Docker Desktop running?)")

    # Clean slate
    _try_compose(["down", "-v"])

    # Bring up with fast replication for tests
    _run_compose(
        ["up", "-d", "--build"],
        env={
            "WRITE_QUORUM": "3",
            "MIN_DELAY_MS": "0",
            "MAX_DELAY_MS": "50",
            "REPLICATE_TIMEOUT_MS": "2000",
        },
    )

    yield

    _try_compose(["down", "-v"])  # cleanup


@pytest.mark.asyncio
async def test_quorum_and_replication_converges():
    # Wait for leader + followers
    await _wait_ok("http://localhost:8000/health")
    for port in [8001, 8002, 8003, 8004, 8005]:
        await _wait_ok(f"http://localhost:{port}/health")

    async with httpx.AsyncClient(timeout=5.0) as client:
        # With quorum=3, write should succeed even if one follower is stopped
        subprocess.run(["docker", "stop", "kv-follower5"], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        r = await client.put("http://localhost:8000/kv/a", json={"value": "v1"})
        assert r.status_code == 200
        body = r.json()
        assert body["acks"] >= 3
        assert body["quorum"] == 3

        # Give replication time to catch remaining followers
        await asyncio_sleep(1.0)

        # Followers 1-4 should have the value
        for port in [8001, 8002, 8003, 8004]:
            gr = await client.get(f"http://localhost:{port}/kv/a")
            assert gr.status_code == 200
            assert gr.json()["value"] == "v1"

        # Switch leader quorum to 5 (env-var-driven) and ensure write fails while follower5 is down
        _run_compose(
            ["up", "-d", "--no-deps", "--force-recreate", "leader"],
            env={
                "WRITE_QUORUM": "5",
                "MIN_DELAY_MS": "0",
                "MAX_DELAY_MS": "50",
                "REPLICATE_TIMEOUT_MS": "1500",
            },
        )
        await _wait_ok("http://localhost:8000/health")

        r2 = await client.put("http://localhost:8000/kv/b", json={"value": "v2"})
        assert r2.status_code == 503

        # Bring follower5 back and the write should succeed again
        subprocess.run(["docker", "start", "kv-follower5"], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        await _wait_ok("http://localhost:8005/health")

        r3 = await client.put("http://localhost:8000/kv/b", json={"value": "v2"})
        assert r3.status_code == 200
        assert r3.json()["acks"] >= 5

        # Eventually all followers converge
        await asyncio_sleep(1.5)
        leader_dump = (await client.get("http://localhost:8000/dump")).json()["data"]
        for port in [8001, 8002, 8003, 8004, 8005]:
            fdump = (await client.get(f"http://localhost:{port}/dump")).json()["data"]
            assert fdump == leader_dump
