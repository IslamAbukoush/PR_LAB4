## Single-leader replicated key-value store (lab)

This repo implements a small in-memory key-value store with:

- **Single-leader replication**: only the leader accepts client writes.
- **Semi-synchronous replication**: the leader waits for a configurable number of follower acknowledgements (**write quorum**) before returning success.
- **Concurrent request handling**: leader replicates to followers concurrently.
- **Network lag simulation**: randomized per-follower delay on the leader side.

Useful docs:

- Theory background: [THEORY_BACKGROUND.md](THEORY_BACKGROUND.md)
- Presentation script / demo flow: [PRESENTATION_GUIDE.md](PRESENTATION_GUIDE.md)

---

## Architecture (what runs)

Docker Compose starts 6 containers:

- Leader HTTP API: http://localhost:8000
- 5 follower HTTP APIs: http://localhost:8001 .. http://localhost:8005

Only the leader accepts `PUT` writes. Followers accept replication calls from the leader.

---

## API

Client-facing endpoints:

- `GET /health`
- `GET /kv/{key}`
- `PUT /kv/{key}` JSON body: `{ "value": "..." }` (leader only)
- `GET /dump` (returns full in-memory state)

Follower-only internal endpoint:

- `POST /internal/replicate` (leader calls this)

---

## Quickstart (Windows / PowerShell)

### Prerequisites

- Docker Desktop (Linux containers)
- Python 3.12+ (only needed for tests/benchmark)

### Start the stack

From the repo root:

```powershell
docker compose up -d --build
```

### Health checks

```powershell
curl http://localhost:8000/health
curl http://localhost:8001/health
```

### Write + read a key (PowerShell-safe curl)

PowerShell has tricky quoting rules for JSON. The most reliable `curl.exe` form is to use `--%` (stop parsing) so the JSON reaches curl unchanged:

```powershell
# Write (leader only)
curl.exe --% -X PUT http://localhost:8000/kv/demo -H "Content-Type: application/json" -d "{\"value\":\"hello\"}"

# Read from leader
curl http://localhost:8000/kv/demo

# Read from a follower (may lag briefly depending on delays/quorum)
curl http://localhost:8002/kv/demo
```

PowerShell-native alternative (also reliable):

```powershell
Invoke-RestMethod -Method Put -Uri "http://localhost:8000/kv/demo" -ContentType "application/json" -Body (@{ value = "hello" } | ConvertTo-Json)
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/kv/demo"
```

### Show “leader-only writes”

```powershell
# This succeeds
curl.exe --% -X PUT http://localhost:8000/kv/onlyleader -H "Content-Type: application/json" -d "{\"value\":\"ok\"}"

# This fails (writes must go to leader)
curl.exe --% -X PUT http://localhost:8001/kv/onlyleader -H "Content-Type: application/json" -d "{\"value\":\"should-fail\"}"
```

---

## Configuration (semi-sync + lag)

All configuration is via environment variables consumed by [docker-compose.yml](docker-compose.yml).

- `WRITE_QUORUM` (1..5): number of follower acks required before the leader returns success
- `MIN_DELAY_MS`, `MAX_DELAY_MS`: randomized per-follower delay before the leader sends a replication request
- `REPLICATE_TIMEOUT_MS`: leader wait timeout for reaching quorum (after which it returns `503`)

Example (PowerShell):

```powershell
$env:WRITE_QUORUM = 3
$env:MIN_DELAY_MS = 0
$env:MAX_DELAY_MS = 1000
$env:REPLICATE_TIMEOUT_MS = 5000

docker compose up -d --build
```

---

## Demonstrate quorum behavior

### Case A — quorum=3, one follower down ⇒ write succeeds

```powershell
docker stop kv-follower5

$env:WRITE_QUORUM = 3
docker compose up -d --no-deps --force-recreate leader

curl.exe --% -X PUT http://localhost:8000/kv/qtest -H "Content-Type: application/json" -d "{\"value\":\"ok-with-3\"}"
```

### Case B — quorum=5, one follower down ⇒ write fails (503)

```powershell
$env:WRITE_QUORUM = 5
docker compose up -d --no-deps --force-recreate leader

curl.exe --% -X PUT http://localhost:8000/kv/qtest -H "Content-Type: application/json" -d "{\"value\":\"should-fail\"}"
```

Bring the follower back and retry:

```powershell
docker start kv-follower5
curl.exe --% -X PUT http://localhost:8000/kv/qtest -H "Content-Type: application/json" -d "{\"value\":\"ok-with-5\"}"
```

---

## Demonstrate “replicas match the leader” (after benchmark)

The benchmark explicitly checks equality of leader vs follower state by comparing `GET /dump` results.

What to show:

1. Immediately after the writes finish, some followers may not match (because the leader can acknowledge after quorum while remaining replications are still in-flight).
2. After a short settle delay (roughly `MAX_DELAY_MS + 1s`), followers should converge and match the leader (unless a follower is unhealthy or timeouts are too small).

The benchmark records both counts in [artifacts/benchmark_results.json](artifacts/benchmark_results.json):

- `immediate_mismatching_followers`: number of followers whose `/dump` differed right after the workload
- `settled_mismatching_followers`: number of followers whose `/dump` still differed after the settle delay

Manual spot-check (optional):

```powershell
curl http://localhost:8000/dump
curl http://localhost:8001/dump
```

---

## Tests (integration)

The integration test boots the compose stack, validates quorum success/failure cases, and checks eventual convergence.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

pytest -q
```

---

## Benchmark (quorum vs latency + replica convergence)

Runs 100 writes total with 10 concurrent in-flight requests across 10 keys (`k0..k9`) for quorums 1..5.

```powershell
.\.venv\Scripts\Activate.ps1

# Optional: tune delay range + timeout for the experiment
$env:MIN_DELAY_MS = 0
$env:MAX_DELAY_MS = 1000
$env:REPLICATE_TIMEOUT_MS = 5000

python .\scripts\benchmark.py
```

Outputs:

- `artifacts/quorum_latency.png`
- `artifacts/benchmark_results.json`

How to explain the results:

- Higher quorum generally increases latency: `quorum=1` is closer to “wait for the fastest follower”; `quorum=5` is closer to “wait for the slowest follower”.
- Immediate mismatches can occur because the leader acknowledges after quorum while replication continues in the background.
- After settling, followers should converge unless a follower is down/unhealthy or `REPLICATE_TIMEOUT_MS` is too small.

---

## Troubleshooting (Windows curl + JSON)

If you see errors like `JSON decode error` or `Expecting property name enclosed in double quotes`, PowerShell is usually altering your JSON arguments.

Recommended fixes:

- Use `curl.exe` (not the `curl` alias) and add `--%`:

```powershell
curl.exe --% -X PUT http://localhost:8000/kv/demo -H "Content-Type: application/json" -d "{\"value\":\"hello\"}"
```

- Or use `Invoke-RestMethod` with `ConvertTo-Json`:

```powershell
Invoke-RestMethod -Method Put -Uri "http://localhost:8000/kv/demo" -ContentType "application/json" -Body (@{ value = "hello" } | ConvertTo-Json)
```

---

## Clean up

```powershell
docker compose down -v
```
