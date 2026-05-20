# file_api production operations

Desktop agents call `https://app.emarvault.com/last_time` (and related routes). Nginx proxies those paths to this service on `127.0.0.1:${FILE_API_PORT:-33000}`.

When `file_api` is down, nginx returns **502 Bad Gateway** and the eMAR Vault dashboard shows all computers/locations offline.

## Quick recovery

On the production host, from the `web/` directory (where `docker-compose.yml` lives):

```bash
docker compose ps file_api
docker compose logs file_api --tail 200
docker compose up -d --build file_api
curl -s http://127.0.0.1:33000/health
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://app.emarvault.com/last_time \
  -H "Content-Type: application/json" \
  -d '{"computer_name":"test","identifier_key":"test"}'
```

Expected:

- `curl .../health` → `{"status":"ok","database":"connected"}`
- Public `/last_time` → **400** (unknown computer), not **502**

Then refresh the dashboard; counts recover as agents heartbeat (5–10 min) and download backups (~1.5 h).

## Common failure causes

| Symptom | Likely cause |
|--------|----------------|
| 502 from nginx | Container not running, wrong `FILE_API_PORT`, or process crash on startup |
| Container exits immediately | Missing `DATABASE_URL`, or `pino-pretty` worker crash (set `USE_PRETTY_LOGS=false`) |
| Health 503 | Postgres unreachable; check `DATABASE_URL` uses host `db` inside compose |
| 200 on health but agents still offline | Nginx not proxying `/last_time` to `file_api`; check site config |

## Nginx (example)

Agent routes must reach `file_api`, not Flask:

```nginx
location ~ ^/(last_time|get_credentials|download_status|download_from_pcc|printer_info|get_telemetry_info|health)$ {
    proxy_pass http://127.0.0.1:33000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Adjust port if `FILE_API_PORT` is not 33000.

## Environment

| Variable | Required | Notes |
|----------|----------|--------|
| `DATABASE_URL` | Yes | Same Postgres as Flask app, e.g. `postgresql://user:pass@db:5432/db` |
| `USE_PRETTY_LOGS` | No | Set `false` in production (default when `NODE_ENV=production`) |
| `NODE_ENV` | No | `production` in docker-compose |
| `FILE_API_PORT` | No | Host port mapping, default 33000 |
| `FLASK_INTERNAL_URL` | No | Default `http://app:5000` (Docker service name) |
| `SERVER_NAME` / `FLASK_INTERNAL_HOST` | Yes (in `.env`) | Host header for internal Flask calls; must match Flask `SERVER_NAME` (e.g. `app.emarvault.com`) |

## Flask internal API (backup log sync)

`file_api` calls Flask `POST /sync_backup_log` after successful downloads. Production sets `SERVER_NAME=app.emarvault.com`, so Werkzeug **rejects** requests whose `Host` is `127.0.0.1` or `app` (404 HTML). Use Docker DNS `http://app:5000` plus a `Host` header matching `SERVER_NAME` — do **not** use `http://app.emarvault.com:5000` unless that name resolves inside the compose network (often it hits public DNS and connection is refused).

**Verify from `file_api`:**

```bash
docker compose exec file_api bun -e "
fetch('http://app:5000/sync_backup_log', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Host: 'app.emarvault.com',
  },
  body: JSON.stringify({ identifier_key: 'YOUR-UUID-HERE' }),
}).then(async r => console.log(r.status, (await r.text()).slice(0, 120)))
"
```

Expect **200** and `{\"status\":\"success\"}`.

Then run `docker compose exec app flask repair-backup-logs` and `flask update-cl-stat`.

## PCC backup failures (`downloadFromPCC`)

Nginx sends `POST /pcc_api/download_backup` to `file_api` `/download_from_pcc`.

| Log | Meaning |
|-----|---------|
| `PCC request returned error status` + `401` | Stale token; `file_api` deletes the cached token and retries once automatically |
| `PCC response after retry` still `401` | PCC credentials/certs or app not authorized |
| status `500` on PCC response | PointClickCare backup-files API error (vendor); check PCC status |
| `PCC request failed (network or TLS)` | Certs missing or wrong path in `file_api` container |

Manual token reset (if many agents fail at once with 401):

```bash
docker compose exec db psql -U postgres -d db -c "DELETE FROM pcc_access_tokens;"
```
