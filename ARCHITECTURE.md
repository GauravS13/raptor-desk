# Architecture

Raptor Desk is a **modular monolith**: one Django application with clear
internal boundaries, a background worker using the same image, and a local mail
catcher. One deployable is the easiest thing for a small organizing team to run,
and it gives one place to enforce access rules.

```
                       ┌──────────────────────── app container (gunicorn) ─────────────────────────┐
 browser ── HTML ────► │  pages (templates + htmx)             REST API (/api, django-ninja)       │
 curl / checker ─────► │           │                                     │                          │
                       │           └──► PrincipalMiddleware ◄────────────┘                          │
                       │                 session cookie (pages) · bearer token (API only)           │
                       │                         │                                                 │
                       │                 @policy gate: default deny, rules per event               │
                       │                         │                                                 │
                       │   apps: accounts · events · teams · submissions · judging · seed          │
                       │         services (rules, transactions, audit) → repositories (scoped)     │
                       │                         │                                                 │
                       │   scoring_engine: pure Python + numpy (weighting, assignment, normalization)│
                       │   core: ids · clock · http errors · policy · audit · outbox · signing ·   │
                       │         rate limits · UI/API action registry                              │
                       └─────────────────────────┬─────────────────────────────────────────────────┘
        worker container: outbox (emails, …)     │      SQLite (WAL) on the `data` volume
                                                 └──►   Mailpit (SMTP 1025, inbox 8025)
```

## Request pipeline

1. **Principal.** `PrincipalMiddleware` reads a bearer token (anywhere) or the session cookie (pages only), and loads the user's per-event role grants.
2. **Policy.** Every view and API operation carries `@policy("key")`. The rule says who may use it: public, signed in, or a role in the event named by the URL. A system check refuses to boot the portal if any route lacks a policy, and it runs in the container entrypoint.
3. **Service.** The business rules: deadlines, phases, validation, conflicts. Each state change writes an audit entry, and any side effect goes into the outbox, in the same transaction.
4. **Repository.** Reads of sensitive data, such as reviews, go through functions scoped to the caller. A judge's query can only return that judge's rows.
5. **Response.** API errors are JSON with a real 4xx status and never a redirect. Page errors render as 403/404 pages.

## Why these boundaries

| Boundary | Rule | Enforced by |
|---|---|---|
| `scoring_engine` | No Django, no database, no app code | import-linter contract in CI |
| `core` | Shared infrastructure; never imports the apps | import-linter contract in CI |
| apps | Pages and API call the same services | UI/API parity test (`core/actions.py`) |
| API | Bearer tokens only (ninja views are CSRF-exempt) | Middleware + test |
| Data | Audit log, phase history, submitted versions, deliberation decisions, results snapshots, publications, the score ledger and signed documents cannot be rewritten | Database triggers (SQLite and PostgreSQL), checked by `manage.py doctor` |
| Records | Everything published or issued is signed canonical JSON, checkable offline | `core/signing.py`, `tools/verify.py` |

## Apps

| App | Owns |
|---|---|
| `accounts` | Users (email sign-in), hashed API tokens, magic links, per-event role grants, judge profiles |
| `events` | Events, tracks, prizes, rubric criteria, custom questions, required artifacts, lifecycle and preflight, templates, organizer console, audit page |
| `teams` | Teams, members (one team per person per event), invite links |
| `submissions` | Projects and immutable versions, answers, artifacts, public gallery and event pages |
| `judging` | Judge tracks, assignments, reviews and scores, conflicts, progress, results, close-call loop, deliberation, signed snapshots and publishing, feedback reports and queries, score ledger, protocols, certificates, passport, CSV exports, judge console |
| `seed` | The fixture loader, the data hygiene report, demo accounts and the live DOGFOOD event |
| `ops` | Backup and verified restore, the `doctor` health report, admin-only Prometheus metrics |

## Operations

- **One image** (`python:3.12-slim`, non-root, healthcheck) runs both the web app and the worker. Dependencies are installed at build time from `uv.lock`.
- **Boot:** `check` (the policy gate), then `migrate`, then `seed` (idempotent), then gunicorn.
- **SQLite in WAL mode** with IMMEDIATE transactions on the `data` volume, which also holds the secret key and the signing key. A backup is a copy of one directory. PostgreSQL works through the same ORM, and its trigger variants are included.
- **Offline.** Every asset is vendored (no CDN, no web fonts), and email goes to the bundled Mailpit.
- **Strict CSP.** No inline scripts or styles, no eval, no third-party hosts. A test fails if a template breaks this.
- **Logs** are one JSON line per request, by route pattern and user id. URL tokens never reach them. See [OPERATIONS.md](OPERATIONS.md).

## Testing

`make check` runs:
- ruff
- formatting
- the import-linter contracts
- a migration drift check
- the test suite: unit and property tests for the engine, golden tests on the official fixtures, integration tests per app, the access matrix, and a crawl of every route as every role

CI also builds and boots the stack with `docker compose up`, and builds the image for arm64 on demand.

Decisions and their alternatives are recorded in [docs/DECISION-LOG.md](docs/DECISION-LOG.md).
