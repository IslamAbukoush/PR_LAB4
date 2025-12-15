import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass

import httpx
import matplotlib.pyplot as plt


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COMPOSE_FILE = os.path.join(ROOT, "docker-compose.yml")
ARTIFACTS_DIR = os.path.join(ROOT, "artifacts")

LEADER_BASE = "http://localhost:8000"
FOLLOWER_PORTS = [8001, 8002, 8003, 8004, 8005]
FOLLOWER_SERVICES = ["follower1", "follower2", "follower3", "follower4", "follower5"]


def run_compose(args: list[str], extra_env: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, *args], check=True, env=env)


def try_compose(args: list[str]) -> None:
    try:
        subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, *args], check=False)
    except Exception:
        pass


async def wait_health(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.json().get("ok") is True:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.25)
    raise RuntimeError(f"Service not healthy: {url}")


@dataclass
class RunResult:
    quorum: int
    avg_latency_ms: float
    immediate_mismatching_followers: int
    settled_mismatching_followers: int


async def workload(quorum: int, min_delay_ms: int, max_delay_ms: int, replicate_timeout_ms: int) -> RunResult:
    # Recreate leader *and followers*.
    #
    # Important: followers keep in-memory state across benchmark runs if we only
    # restart the leader. That makes the dump equality checks (mismatch_*) measure
    # cross-run residue rather than replication correctness.
    run_compose(
        ["up", "-d", "--no-deps", "--force-recreate", "leader", *FOLLOWER_SERVICES],
        extra_env={
            "WRITE_QUORUM": str(quorum),
            "MIN_DELAY_MS": str(min_delay_ms),
            "MAX_DELAY_MS": str(max_delay_ms),
            "REPLICATE_TIMEOUT_MS": str(replicate_timeout_ms),
        },
    )

    await wait_health(f"{LEADER_BASE}/health")
    for port in FOLLOWER_PORTS:
        await wait_health(f"http://localhost:{port}/health")

    sem = asyncio.Semaphore(10)  # 10 at a time
    latencies: list[float] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        async def one_put(i: int) -> None:
            key = f"k{i % 10}"
            value = f"v{i}"

            async with sem:
                t0 = time.perf_counter()
                r = await client.put(f"{LEADER_BASE}/kv/{key}", json={"value": value})
                t1 = time.perf_counter()

            if r.status_code != 200:
                raise RuntimeError(f"PUT failed (quorum={quorum}): {r.status_code} {r.text}")

            latencies.append((t1 - t0) * 1000.0)

        await asyncio.gather(*[one_put(i) for i in range(100)])

        leader_dump = (await client.get(f"{LEADER_BASE}/dump")).json()["data"]

        follower_dumps = []
        for port in FOLLOWER_PORTS:
            follower_dumps.append((await client.get(f"http://localhost:{port}/dump")).json()["data"])

        immediate_mismatch = sum(1 for d in follower_dumps if d != leader_dump)

        # Give replication time to finish to all followers
        await asyncio.sleep(max_delay_ms / 1000.0 + 1.0)

        leader_dump2 = (await client.get(f"{LEADER_BASE}/dump")).json()["data"]
        follower_dumps2 = []
        for port in FOLLOWER_PORTS:
            follower_dumps2.append((await client.get(f"http://localhost:{port}/dump")).json()["data"])
        settled_mismatch = sum(1 for d in follower_dumps2 if d != leader_dump2)

    avg_latency = sum(latencies) / len(latencies)
    return RunResult(
        quorum=quorum,
        avg_latency_ms=avg_latency,
        immediate_mismatching_followers=immediate_mismatch,
        settled_mismatching_followers=settled_mismatch,
    )


async def main() -> None:
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    # Clean + start stack once (followers constant across runs)
    try_compose(["down", "-v"])
    run_compose(["up", "-d", "--build"], extra_env={"WRITE_QUORUM": "1", "MIN_DELAY_MS": "0", "MAX_DELAY_MS": "0"})

    for port in [8001, 8002, 8003, 8004, 8005]:
        await wait_health(f"http://localhost:{port}/health")

    results: list[RunResult] = []

    # Use noticeable delay to see effect in latency curve
    min_delay_ms = int(os.environ.get("MIN_DELAY_MS", "0"))
    max_delay_ms = int(os.environ.get("MAX_DELAY_MS", "1000"))
    replicate_timeout_ms = int(os.environ.get("REPLICATE_TIMEOUT_MS", "5000"))

    for q in [1, 2, 3, 4, 5]:
        print(f"Running quorum={q} ...")
        results.append(await workload(q, min_delay_ms, max_delay_ms, replicate_timeout_ms))

    # Save JSON
    out_json = os.path.join(ARTIFACTS_DIR, "benchmark_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)

    # Plot
    xs = [r.quorum for r in results]
    ys = [r.avg_latency_ms for r in results]

    plt.figure(figsize=(7, 4))
    plt.plot(xs, ys, marker="o")
    plt.xticks(xs)
    plt.xlabel("Write quorum")
    plt.ylabel("Average write latency (ms)")
    plt.title("Quorum vs Average Write Latency")
    plt.grid(True, alpha=0.3)

    out_png = os.path.join(ARTIFACTS_DIR, "quorum_latency.png")
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)

    print("\nResults:")
    for r in results:
        print(
            f"quorum={r.quorum} avg={r.avg_latency_ms:.1f}ms "
            f"mismatch_immediate={r.immediate_mismatching_followers}/5 "
            f"mismatch_settled={r.settled_mismatching_followers}/5"
        )

    print(f"\nWrote {out_png}")
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
