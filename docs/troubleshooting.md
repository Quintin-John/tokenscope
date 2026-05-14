# Troubleshooting

Everything in tokenscope runs in `docker compose` — one command starts the whole stack. When something goes wrong, the issue is usually in one of three layers:

1. **`tokenscope-collector` container** — reads `~/.claude/projects/` via the bind mount, parses session logs, publishes OTLP.
2. **`otel-collector` container** — receives OTLP, exports a Prometheus scrape endpoint.
3. **`prometheus` / `grafana` containers** — scrape, query, render.

Knowing which layer's logs to check first cuts most issues in half.

## Quick-look ports

| Port | Container | Purpose | Published to host? |
|---|---|---|---|
| 4317 | otel-collector | OTLP gRPC ingest | **No** — internal only |
| 4318 | otel-collector | OTLP HTTP ingest | **No** |
| 8888 | otel-collector | Self-metrics (Prometheus scrapes) | **No** |
| 8889 | otel-collector | Prometheus exporter (Prometheus scrapes) | **No** |
| 9090 | prometheus | UI + API | **No** |
| 3000 | grafana | UI | **Yes** — `http://localhost:3000` |

Only Grafana is reachable from your host. To reach Prometheus or the OTEL Collector directly for debugging, either `docker compose exec` into a container or temporarily publish their ports in `docker-compose.override.yml`.

## Default Grafana credentials

| Field | Default | Override via |
|---|---|---|
| URL | <http://localhost:3000> | — |
| Username | `admin` | `TOKENSCOPE_GRAFANA_USER` env (or `.env`) |
| Password | `tokenscope` | `TOKENSCOPE_GRAFANA_PASSWORD` env (or `.env`) |

Grafana prompts for a password change on first login. Use a real password if anyone else might reach your machine. Anonymous access is disabled.

## "Permission denied" on session log reads

**Symptom**: `tokenscope-collector` logs `IOException: Permission denied` or `Access to the path '/data/claude-logs/...' is denied` when scanning logs.

**Cause**: The container runs as UID 10001 (non-root). It reads `~/.claude/projects/` via a read-only bind mount and relies on world-readable permissions on those files. If a directory or file under `~/.claude/projects/` has had its `o` (other) bit cleared, the container can't read it.

**Fix**:
```sh
chmod -R o+rX ~/.claude/projects
```

This grants read permission for "others" (the container user) and traverse permission for directories. **Safe** — these are your own logs, not system files. The only consumer added by tokenscope is a container running on the same machine.

To verify the assumption holds before running the stack:
```sh
ls -la ~/.claude/projects/ | head
# Permissions should look like drwxr-xr-x (755) or similar.
```

The alternative (UID mapping the container to the host user) introduces platform-specific docker-compose invocations that break on Windows Docker Desktop. Fixed UID + world-readable logs is the simpler portable contract.

## "Permission denied" writing to state

**Symptom**: `tokenscope-collector` logs `Access to '/data/state/seen.json' is denied`.

**Cause**: This should only happen if you manually swapped the `tokenscope-state` named volume for a host bind mount. Named volumes are created with container-user ownership on first run; host bind mounts adopt host UID ownership and clash with UID 10001.

**Fix**: Use the named volume (the default). If you have a specific reason to bind-mount the state path, either chown the host directory to 10001 or run the container as the host user (`user: "${UID}:${GID}"` — Linux only; breaks on Windows Docker Desktop).

## FileSystemWatcher events not firing on macOS

**Status**: Polling is **enabled by default** in `docker-compose.yml`
(`TOKENSCOPE_SESSION_LOGS__USE_POLLING=true`) — Phase 6 smoke testing
on macOS confirmed that the host's gRPC FUSE bind-mount doesn't
propagate `inotify` events to the Linux container reliably. The
collector polls every 5 seconds (override via
`TOKENSCOPE_SESSION_LOGS__POLLING_INTERVAL_SECONDS`).

**If you're on Linux native** and want lower latency, set
`TOKENSCOPE_SESSION_LOGS__USE_POLLING=false` in your `.env`. Native
`inotify` works directly without the FUSE layer.

**If polling is on but events still don't appear**, check:
1. `docker compose logs tokenscope-collector | grep -i polling`
   should show "FileSystemWatcher disabled (use_polling=true).
   Polling every 5s." early in startup.
2. Verify the host file is visible in the container:
   `docker compose exec tokenscope-collector ls /data/claude-logs/`.
3. Wait at least one polling interval (default 5s) plus one Prometheus
   scrape interval (15s) after appending a new event — ~30 seconds
   max for an event to reach Grafana.

## "Connection refused" in the collector logs on first startup

**Symptom**: First 5–10 seconds of `tokenscope-collector` logs show OTLP errors like `Connection refused` or `Failed to send metrics`.

**Cause**: `depends_on:` in compose only waits for the OTEL Collector's *container* to start, not for it to *finish initializing*. The tokenscope collector may attempt its first OTLP export before the OTEL Collector is listening on `:4317`.

**Fix**: None required — the OTEL .NET exporter retries automatically and recovers. Errors during the first ~10s of startup are **expected and self-healing**. If they persist past 30 seconds, check `docker compose logs otel-collector` for a config error.

## Prometheus shows no `tokenscope_*` metrics

1. **No log activity.** Confirm `~/.claude/projects/` actually contains `.jsonl` files. With `initial_scan_enabled: true` (default), the collector processes existing files on startup.
2. **Collector container not running.** `docker compose ps` should show `tokenscope-collector` as `Up`. If it's restarting, `docker compose logs tokenscope-collector` reveals the error.
3. **Scrape interval too long.** Prometheus is configured for 15s. Wait 30s after startup before declaring metrics missing.
4. **OTEL Collector pipeline error.** `docker compose logs otel-collector` shows any malformed input.

Layer-by-layer verification:
```sh
# Did the collector emit anything?
docker compose logs tokenscope-collector | grep -i 'metric\|otlp\|recorded'

# Did the OTEL Collector receive?
docker compose exec otel-collector wget -qO- http://localhost:8889/metrics | grep '^tokenscope_'

# Did Prometheus scrape it?
docker compose exec prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=count(count%20by(__name__)%20({__name__=~%22tokenscope_.*%22}))'
```

## Grafana shows "No data"

1. **Datasource not provisioned.** Visit <http://localhost:3000/datasources> — `Prometheus` (uid `prometheus`) should be listed. If it isn't, `docker compose logs grafana` reveals why (usually a bind-mount permission issue on the provisioning files).
2. **Time range too narrow / too far past.** The stack-health dashboard defaults to `now-30m`; widen if the stack just started.
3. **Series count is zero.** See "Prometheus shows no `tokenscope_*` metrics" above. Grafana can't show data Prometheus doesn't have.
4. **Wrong dashboard.** The stack-health dashboard at *tokenscope/tokenscope — stack health* is the Phase 6 deliverable. Real dashboards arrive in Phase 7.

## Port conflicts on `docker compose up`

**Symptom**: `bind: address already in use` on port 3000.

**Cause**: Another tokenscope stack already running, OR another tool using port 3000 (commonly a dev server).

**Fix**:
```sh
docker compose down
# Or, find the conflicting process:
lsof -nP -iTCP:3000 -sTCP:LISTEN
```

If the conflict is persistent (e.g., you really do want app-dev on 3000), change Grafana's host port mapping in `docker-compose.yml`: `"3001:3000"`.

## Windows: `${HOME}` doesn't resolve to my Claude Code logs

**Symptom**: On Windows Docker Desktop, `tokenscope-collector` reports "session_logs path does not exist" or processes an empty directory.

**Cause**: `${HOME}` in `docker-compose.yml` should resolve to the WSL2 user's home (where Claude Code stores logs), not the Windows user profile.

**Fix**: Run `docker compose` from a WSL2 shell:
```sh
wsl -d Ubuntu
cd ~/path/to/tokenscope
docker compose up
```

If you must run from PowerShell:
```powershell
$env:HOME = "/home/<your-wsl-user>"
docker compose up
```

The `.env.example` documents this. Linux and macOS users don't hit this — `${HOME}` resolves correctly natively.

## Hot-reload of `pricing.json` doesn't seem to work

**Symptom**: You edit `config/pricing.json` and the collector keeps using the old rates.

**Diagnosis**:
1. The collector logs a `PricingReloadEvent.Loaded` line on every successful reload. Check `docker compose logs tokenscope-collector` after saving the file.
2. If your editor writes via a temp file + rename (atomic save), the `FileSystemWatcher` may see a delete + create rather than a change. Either pattern triggers reload, but takes ~1s due to the debouncer.
3. If the file change is invalid (e.g., negative rates, future `effective_date`), the collector logs a `PricingReloadEvent.ValidationFailed` warning and keeps using the previous good table. Fix the JSON and save again.

The `FileSystemWatcher` works reliably over bind mounts for `pricing.json` specifically — it's a single file. The macOS-bind-mount caveat for `~/.claude/projects` doesn't apply to single-file bind mounts.

## Dashboard edits don't survive `docker compose down`

**Symptom**: I made a dashboard change in the Grafana UI, ran
`docker compose down && up`, and my change is gone.

**Cause**: Grafana's provisioning files are bind-mounted **read-only**
into the container. Provisioning re-applies the on-disk JSON every 30
seconds (the `updateIntervalSeconds` setting in
`docker/grafana/provisioning/dashboards/dashboards.yaml`). UI edits
live only in the Grafana-internal database, which is wiped when the
container is recreated without a persistent volume.

**Fix — for changes you want to keep**:

1. Edit the dashboard in the UI.
2. Open the dashboard's **Share → Export → Save to file**.
3. Replace `docker/grafana/dashboards/tokenscope.json` with the
   exported file.
4. Commit to git.

The next provisioning cycle (≤ 30s) re-loads your version. The Grafana
UI's dashboard list shows the dashboard with your changes.

**For ad-hoc exploration**: edits in the UI are fine — they just won't
be the source of truth. Don't try to make the running Grafana the
source of truth; the repo's `docker/grafana/dashboards/` is.

## Resetting the stack

Stop everything and remove data:
```sh
docker compose down --volumes
```

This deletes all three named volumes: `tokenscope-state`, `tokenscope-prometheus-data`, `tokenscope-grafana-data`. The repo's config files and your `~/.claude/projects/` are untouched.

To reset only the collector's resume state (force a full re-scan next start) without deleting Prometheus history:
```sh
docker compose down
docker volume rm tokenscope-state
docker compose up
```

## Upgrading Grafana past 12.x

The stack pins `grafana/grafana:12.4.3` deliberately (see [`architecture.md`](./architecture.md) for the rationale).

Grafana 13.x introduces **unified storage** migration on first start: the embedded SQLite database is migrated to a new format that is **not backwards-compatible** with v12. Practical impact:

- A v12 stack whose volume has been rewritten by a v13 run cannot be downgraded to v12 without restoring the volume from a backup.
- For tokenscope's provisioned datasource + dashboards setup, this is not catastrophic — the source of truth is the repo, not Grafana's internal DB — but any dashboard edits saved through the UI, user accounts, and starred dashboards live in that DB.

Safe upgrade path:
1. `docker compose down` and back up the `tokenscope-grafana-data` named volume:
   ```sh
   docker run --rm -v tokenscope-grafana-data:/data -v $(pwd):/backup alpine \
     tar -czf /backup/grafana-data.tgz -C /data .
   ```
2. Try the upgrade in a test compose project first.
3. If it works, run the upgrade in your real environment.
4. If it doesn't, restore:
   ```sh
   docker volume rm tokenscope-grafana-data
   docker volume create tokenscope-grafana-data
   docker run --rm -v tokenscope-grafana-data:/data -v $(pwd):/backup alpine \
     tar -xzf /backup/grafana-data.tgz -C /data
   ```

Not scary, just deliberate. tokenscope can pick up Grafana 13 as a considered version bump in Phase 9+.

## Getting a useful bug report

When something goes wrong, the following capture is usually enough:

```sh
docker compose ps                                                > diag-ps.txt
docker compose logs --tail 200                                   > diag-stack.log
docker compose exec tokenscope-collector ls -la /data/state      > diag-state.txt
ls -la ~/.claude/projects/                                       > diag-projects.txt
cat tokenscope.yaml 2>/dev/null                                  > diag-config.yaml
cat .env 2>/dev/null | grep -v PASSWORD                          > diag-env.txt
```

Plus `docker compose logs tokenscope-collector --tail 200` separately. Attach all of those when filing an issue.
