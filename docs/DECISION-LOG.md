# Decision log

Each entry records what we decided, what we rejected, and why. Entries are
added when the decision is made, next to the commit that implements it.

---

## D-001 · Django 5.2 LTS, django-ninja, HTMX, SQLite
- **Decided:** a server-rendered Django application with a django-ninja REST API, HTMX for interactivity and SQLite for storage.
- **Rejected:** a single-page app with a separate API server; FastAPI; Node.
- **Why:** authentication, sessions, CSRF protection, migrations, database constraints and an admin come built in. django-ninja generates the OpenAPI document from typed code. One authorization path serves both pages and API. 5.2 is a long-term-support release for an organizer who will run this for years.
- **Where:** `pyproject.toml`, `src/config/` · 9f8a329

## D-002 · SQLite in WAL mode with IMMEDIATE transactions
- **Decided:** SQLite on a Docker volume, `journal_mode=WAL`, `busy_timeout=5000`, `transaction_mode="IMMEDIATE"`.
- **Rejected:** PostgreSQL as the default.
- **Why:** zero operations and a one-file backup; the portal must start offline with one command. IMMEDIATE takes the write lock at the start of a transaction, avoiding "database is locked" errors when a read upgrades to a write during vote bursts. PostgreSQL remains a supported path (the append-only triggers ship for both).
- **Where:** `src/config/settings.py` · 9f8a329

## D-003 · Prefixed, time-sortable ids; fixture ids kept verbatim
- **Decided:** every table uses a string primary key `<prefix>_<ulid>`; records loaded from the official fixtures keep their ids (`evt_01`, `jdg_26`).
- **Rejected:** integer keys; UUID4.
- **Why:** routes, CSVs and exports line up with the official dataset, ids say what they refer to, and ULIDs sort by creation time.
- **Where:** `src/core/ids.py` · 1cc1506

## D-004 · The API accepts bearer tokens only, and never redirects
- **Decided:** `/api/...` authenticates with `Authorization: Bearer <token>` and ignores the session cookie. Errors are JSON with a real 4xx status.
- **Rejected:** accepting the session cookie on the API; redirecting unauthenticated API calls to the login page.
- **Why:** django-ninja views are CSRF-exempt, so honouring cookies there would allow cross-site requests on a signed-in user's behalf. The acceptance checker follows redirects, so a redirect to a login page would read as a 200.
- **Where:** `src/apps/accounts/principal.py`, `src/core/http.py` · 67f21e4

## D-005 · Default-deny access policy, enforced at boot
- **Decided:** every page and API operation carries `@policy("key")`; rules are evaluated per event from the URL; a system check refuses to start the portal if any route lacks a policy.
- **Rejected:** ad-hoc permission checks inside views; template-level hiding.
- **Why:** a forgotten check must fail loudly at boot, not leak data quietly. Isolation lives in the backend, where curl arrives.
- **Where:** `src/core/policy.py`, `src/core/checks.py` · 67f21e4, fix b4b53a9

## D-006 · Append-only audit trail enforced by the database
- **Decided:** audit entries are readable sentences; database triggers reject UPDATE and DELETE; the actor is stored by id only and the client IP as a salted hash.
- **Rejected:** a mutable log table; copying names or emails into each entry.
- **Why:** an audit trail anyone can rewrite is not an audit trail. Keeping personal data out of the log lets a user be anonymised without touching history.
- **Where:** `src/core/models.py`, `src/core/dbguards.py` · 58a3300

## D-007 · Transactional outbox and a separate worker container
- **Decided:** side effects (email, webhooks, snapshots) are queued in the same transaction as the change and delivered by a `worker` service with claiming, exponential backoff and a failure cap.
- **Rejected:** Celery with Redis; a thread inside the web server.
- **Why:** no message is lost or sent for a rolled-back change, and there is no extra infrastructure. A thread per web process would duplicate jobs.
- **Where:** `src/core/outbox.py`, `docker-compose.yml` · 6f17a0f

## D-008 · One deployment signing key; canonical JSON
- **Decided:** records are signed with one Ed25519 key created on first boot in the data volume; payloads are canonical JSON (sorted keys, compact separators, UTF-8). The public key is served at `/.well-known/raptor-desk-key`.
- **Rejected:** per-judge private keys held by the server; HMAC.
- **Why:** a signature proves "issued by this deployment and unaltered". Custodial per-judge keys would prove nothing more. Canonical JSON lets any external verifier reproduce the signed bytes.
- **Where:** `src/core/signing.py` · 36359c8

## D-009 · Rate limits counted in the database
- **Decided:** fixed-window counters in a table, keyed by a salted hash of the identity.
- **Rejected:** Redis; per-process memory counters.
- **Why:** limits must hold across every web process without extra infrastructure, and the table must not store IPs or emails.
- **Where:** `src/core/ratelimit.py` · d59b581

## D-010 · Vendored assets, strict Content-Security-Policy, no Alpine.js
- **Decided:** htmx, Pico CSS and SortableJS are committed in the repository; the CSP forbids inline scripts, inline styles, eval and third-party hosts; htmx runs with `allowEval: false`.
- **Rejected:** CDN links; Alpine.js (its standard build needs `unsafe-eval`).
- **Why:** the portal must render fully with the network off, and a strict CSP limits the damage of any injection. A test fails if a template loads an external asset or uses inline styles.
- **Where:** `src/static/vendor/`, `src/config/security.py` · 9fd0cfa

## D-011 · UI and API parity by construction
- **Decided:** state-changing views and API operations declare the action they perform; a test fails if any UI action has no API operation.
- **Rejected:** keeping parity by review.
- **Why:** "every action the UI can take is in the API" stays true as the code grows.
- **Where:** `src/core/actions.py` · 7a8db56

## D-012 · Architecture boundaries checked in CI
- **Decided:** import-linter contracts: `scoring_engine` may not import Django or app code; `core` may not import the apps.
- **Why:** the scoring maths stays a pure, independently testable library, and shared infrastructure stays free of business logic.
- **Where:** `pyproject.toml` · 7a8db56

## D-013 · A resubmission is a new version of the same project
- **Decided:** editing a submitted project before the deadline creates version n+1; submitted versions are immutable (database trigger); reviews reference the version judged.
- **Rejected:** allowing a second project per team; overwriting the submitted content.
- **Why:** the fixture's "Dry Harbour" was submitted twice by the same team, three minutes before the close. As two projects it ranks #9 and #31 and could take two prizes; as versions it is one entry, and what a judge saw is never rewritten.
- **Where:** `src/apps/submissions/`, `src/apps/seed/fixtures.py` · abb1729, a90d27b

## D-014 · A flat-liner is recognised by identical per-criterion scores
- **Decided:** a judge is excluded from the bias fit only if every review carries the same per-criterion scores; equal composites alone are not enough.
- **Rejected:** zero variance of the composite.
- **Why:** after the resubmission merge, jdg_19's three composites are equal (5+4+2 and 4+3+4 both total 11). Those are genuine judgements that happen to tie, not rubber-stamping; only jdg_07 (4/4/4 everywhere) is.
- **Where:** `src/scoring_engine/normalization.py` · 9b85e16

## D-015 · Additive judge-severity model as the default estimator
- **Decided:** rank by a ridge-regularised additive model; show the shrunken z-score and the raw mean beside it; flag disagreement and close calls from a bootstrap.
- **Rejected:** the per-judge z-score (NaN for 7 fixture projects); raw averages (rewards lenient judges).
- **Why:** it never divides by a judge's spread, handles judges with one review through the ridge term, recovers a known truth best in simulation (Spearman 0.813 vs 0.697), and its uncertainty tells the organizer where a human decision is needed.
- **Where:** `src/scoring_engine/normalization.py`, `JUDGING.md` · 9b85e16, 2a01e31

## D-016 · Queue order is random per judge, and stored
- **Decided:** each assignment gets a random queue position when created.
- **Why:** judges drift over a session (fatigue, calibration); a shared order would give the same projects the same advantage for every judge.
- **Where:** `src/apps/judging/services.py` · dc97265

## D-017 · Demo data only in the demo profile
- **Decided:** fixed checker tokens, demo passwords and fixture data are created only when `RD_PROFILE=demo` (the compose default); `production` refuses them.
- **Why:** the acceptance checker needs fixed tokens, but a real deployment must never have them.
- **Where:** `src/apps/seed/demo.py` · a90d27b

## D-018 · Isolation proven by a generated matrix
- **Decided:** a test requests every sensitive resource as eight identities and compares with expected statuses; the markdown table is generated from those responses; the same test crawls every GET route as every role looking for server errors or API redirects.
- **Why:** "judges cannot see each other's scores" should be shown, not asserted.
- **Where:** `tests/test_access_matrix.py`, `ACCESS-MATRIX.md` · 39d5c6e

## D-019 · Log requests by route pattern, never by raw URL
- **Decided:** one JSON line per request with the route pattern (`/join/<str:token>`), status, duration, request id and user id; gunicorn's access log is off; a filter redacts URL tokens from every other log line, and a test fails if a new token route is not covered.
- **Rejected:** gunicorn's access log; logging emails or IPs.
- **Why:** invite links and sign-in links carry secrets in the path. Anyone with log access could have joined a team or signed in as someone else.
- **Where:** `src/core/logs.py`, `scripts/entrypoint.sh` · 9947a85, 4a182a9

## D-020 · Online backups; restore verifies before it touches anything
- **Decided:** one archive with a database copy made by SQLite's online backup API, the uploads, both keys and a checksummed manifest. Restore checks paths, checksums and the schema version, saves the current state first, and records itself in the audit trail.
- **Rejected:** copying `db.sqlite3` with `cp` (can capture a half-written WAL state); backing up the database without the keys (restored signatures would no longer verify).
- **Why:** the moment someone restores is the worst moment to discover a damaged or incompatible backup.
- **Where:** `src/apps/ops/backups.py` · 8d7a4b0

## D-021 · Metrics read from the database
- **Decided:** `/metrics` computes counts and ages from the database on each scrape; admins only; no personal data.
- **Rejected:** in-process request counters.
- **Why:** with several gunicorn processes, per-process counters give a different answer on every scrape. The numbers an organizer needs (reviews done, outbox stuck, last backup) are already in the database.
- **Where:** `src/apps/ops/metrics.py` · 089f926

