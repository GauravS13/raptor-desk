# Operating Raptor Desk

Everything an organizer needs on the day of the event and after it. Commands
are shown with `make`; each one is a thin wrapper around the `docker compose`
line printed next to it.

## Start, stop, reset

| | |
|---|---|
| Start | `docker compose up -d` |
| Stop, keeping data | `docker compose down` |
| Start over (deletes all data) | `docker compose down -v` |
| Is it up? | `docker compose ps`: the `app` service reports `healthy` once `/healthz` answers |

All data lives in the `data` volume:

| Path | What it is |
|---|---|
| `/data/db.sqlite3` | the database |
| `/data/media` | uploads |
| `/data/secret_key` | the session secret |
| `/data/keys` | the signing key |
| `/data/backups` | backups |

## Health report

```bash
make doctor        # docker compose exec app python src/manage.py doctor
```

```
raptor-desk 0.1.0 doctor · 2026-09-27 14:43 UTC · profile demo

  ok    database         SQLite, WAL, integrity ok, 972.1 KB
  ok    migrations       all 32 applied
  ok    access policies  every route declares who may use it
  ok    tamper guards    audit trail, phase history and submissions append-only
  ok    data volume      writable, 941.7 GB free
  ok    keys             secret key from data volume; signing key is created on first use
  ok    outbox           0 pending, none overdue, none failed
  ok    email            mail server reachable at mailpit:1025
  info  profile          demo: fixed test tokens and demo passwords are active
  warn  backups          no backup yet
                         fix: make backup (or: python src/manage.py backup)

0 failed, 1 need attention
```

Every warning or failure says what to do. The command exits with 1 if
anything failed, so it can gate a deploy script. `--json` gives
machine-readable output.

## Backups

```bash
make backup        # docker compose exec app python src/manage.py backup --keep 14
```

- **It is safe while the portal runs.** The database is copied with SQLite's online backup API, so the copy is consistent even during judging.
- **One archive holds everything needed to come back:** the database, uploads, the secret key, the signing key (so restored signatures still verify), and a manifest with a SHA-256 for every file.
- `--keep 14` keeps the 14 newest backups.
- **Treat a backup like a password:** it contains the keys. To copy one off the machine:
  ```bash
  docker compose cp app:/data/backups ./backups
  ```
- `make doctor` warns when the newest backup is more than 24 hours old.
- **Schedule it** on the host, for example with cron:
  ```
  0 * * * * cd /srv/raptor-desk && docker compose exec -T app python src/manage.py backup --keep 48
  ```

## Restore

```bash
make restore FILE=raptor-desk-20260927-144352.tar.gz
```

This stops the app and worker, restores, then starts them again. Before it
changes anything, restore:

1. checks that every file in the archive is expected (no stray or traversing paths) and that every checksum matches;
2. refuses a backup made by a newer version of Raptor Desk;
3. saves the current state as `raptor-desk-<time>-pre-restore.tar.gz`, so a restore can be undone.

It then restores, runs an integrity check, and records the restore in the audit
trail. Without `--yes`, `manage.py restore <file>` only verifies the archive,
which is a quick way to test a backup.

To restore on a new machine:
1. Clone the repository and run `docker compose up -d`.
2. Copy the archive in with `docker compose cp <file> app:/data/backups/`.
3. Run `make restore FILE=<file>`.

## Logs

```bash
docker compose logs -f app
```

- Each request is one JSON line: route pattern, status, duration, request id and user id.
- A request id sent in `X-Request-ID` is kept, so proxy logs line up with the portal's.
- **Raw URLs are never logged:** invite and sign-in links carry secret tokens. A filter removes those tokens from any other log line, and a test fails if a new token route is added without being covered.
- Set `RD_LOG_FORMAT=plain` for text logs, and `RD_LOG_LEVEL=DEBUG` to include health probes.

## Metrics

`GET /metrics` returns Prometheus text for administrators. Get a token for an
admin account, then give it to Prometheus:

```bash
curl -s -X POST http://localhost:8080/api/auth/token -H "Content-Type: application/json"   -d '{"email": "<admin email>", "password": "<password>", "label": "prometheus"}'
```

```yaml
scrape_configs:
  - job_name: raptor-desk
    authorization: { credentials: <token> }
    static_configs: [{ targets: ["app:8080"] }]   # from a container on the same network
```

- **Numbers:** events by phase; projects, assignments and reviews per event and status; outbox state and `raptor_desk_outbox_oldest_due_seconds`, which grows if the worker stops; audit entries; database size; free space; and the time of the last backup.
- **Consistent across web processes:** the numbers are read from the database.
- **Nothing personal:** no names, emails, titles or scores.

## Configuration

Everything has a safe default. Set these in `docker-compose.yml` or an override file:

| Variable | Default | Purpose |
|---|---|---|
| `RD_PROFILE` | `demo` | `production` creates no demo data, accounts or fixed tokens |
| `RD_BASE_URL` | `http://localhost:8080` | public address used in emailed links |
| `RD_ALLOWED_HOSTS` | local names | host names the portal answers to |
| `RD_CSRF_TRUSTED_ORIGINS` | Codespaces | the portal's `https://` origin when a proxy terminates TLS |
| `RD_SECURE_COOKIES` | off | turn on behind HTTPS |
| `RD_EMAIL_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_USE_TLS` / `_FROM` | Mailpit | a real mail server |
| `RD_SECRET_KEY` | created in the volume | supply your own secret instead |
| `RD_WEB_WORKERS` | `3` | gunicorn worker processes |
| `RD_LOG_FORMAT` / `RD_LOG_LEVEL` | `json` / `INFO` | logging |
| `RD_WEBHOOK_ALLOW_PRIVATE` | on in `demo`, off in `production` | let webhooks reach private or loopback addresses |

Per event, in **Settings**:
- the community vote's burst threshold
- trusted venue networks (CIDR), whose votes are never held
- the email domains allowed to request voting links

## Going to production

1. Start from an empty volume with `RD_PROFILE=production`. The portal refuses to start in this profile while demo credentials are in the database (fixed seed tokens, or demo accounts that still accept the public demo password), so a volume seeded for the demo cannot go live by accident. `python src/manage.py safety_gate` runs the same check by hand.
2. Create the first administrator:
   ```bash
   docker compose exec app python src/manage.py createsuperuser
   ```
3. Put the portal behind HTTPS. Set `RD_BASE_URL`, `RD_ALLOWED_HOSTS`, `RD_CSRF_TRUSTED_ORIGINS` (for example `https://judging.example.org`), `RD_SECURE_COOKIES=1` and the `RD_EMAIL_*` variables.
4. Run `make doctor` until it reports no failures, then take the first backup with `make backup`.
