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

## D-022 · The desk proposes where judges' time goes
- **Decided:** close calls on prize cutoffs (a chance of finishing inside between 0.2 and 0.8) drive a proposal of extra reviews within an organizer-set budget. The proposal prefers judges who reviewed the neighbouring close calls, and names the projects no eligible judge can settle.
- **Rejected:** only reporting uncertainty; assigning more reviews everywhere.
- **Why:** more evidence helps only where a prize depends on it, and judge time is the scarce resource. The demo shows chances moving away from a coin flip after one round.
- **Where:** `src/scoring_engine/ask.py`, `src/apps/judging/close_calls.py` · 7d921df, 113ca75, ed4e418

## D-023 · Canonical input order in the engine
- **Decided:** `evaluate()` sorts observations before bootstrapping.
- **Why:** the seeded bootstrap depended on the order reviews arrived in, so the portal and JUDGING.md disagreed slightly on the same data. JUDGING.md's tables are now generated and checked in CI.
- **Where:** `src/scoring_engine/normalization.py`, `tools/simulate_proof.py` · 3e9d1f5

## D-024 · Decisions and snapshots are append-only; publishing is a checked phase change
- **Decided:** deliberation decisions need a written reason and are never edited (a new decision supersedes an old one). Results are frozen into numbered, signed snapshots. Publishing is the phase change to *published*, with preflight checks and a recorded publication.
- **Rejected:** editing the ranking in place; a separate "publish" button outside the lifecycle.
- **Why:** after a prize is announced, the question "who decided this, and why" must have a permanent answer, and the published document must be checkable by anyone with the public key.
- **Where:** `src/apps/judging/deliberation.py`, `snapshots.py`, `publishing.py`, `src/apps/events/hooks.py` · da6fe06, 3ec0cbf, c779bad

## D-025 · The full ranking is public; judges stay anonymous
- **Decided:** published results show the whole ranking with intervals and the organizers' decisions. Feedback reports show every comment without the judge's name, in a fixed shuffled order.
- **Rejected:** publishing winners only; naming judges in feedback.
- **Why:** a ranking that shows its math is only credible if all of it is visible. Naming judges would invite pressure on them and would soften their feedback.
- **Where:** `src/apps/judging/templates/judging/public_results.html`, `src/apps/judging/feedback.py` · c779bad, 35a79b2

## D-026 · A ledger checked against the live scores
- **Decided:** every submitted score goes into a hash-chained, signed, append-only ledger. Verification also compares the live scores and feedback with the latest entry for each review. Upgraded deployments are backfilled by a data migration.
- **Rejected:** a hash chain on its own; relying on the audit log.
- **Why:** the realistic attack is an edit to the scores table, not to the ledger. A chain that is never compared with the live data proves only that the chain is intact.
- **Where:** `src/apps/judging/ledger.py`, `migrations/0011_backfill_score_ledger.py` · e656be3, e068e34

## D-027 · Public records are found by random code; the passport is opt-in
- **Decided:** certificates and participation records carry a display number, but are reached only through a random code. They contain no scores or places. A judge's cross-event passport is off until the judge switches it on.
- **Rejected:** sequential public URLs; public-by-default passports.
- **Why:** sequential numbers would let anyone list every participant's name, which the UK GDPR and plain courtesy argue against. The person decides who sees their record.
- **Where:** `src/apps/judging/credentials.py`, `passport.py` · 8fbfbac, b7945c3

## D-028 · Verification pins the key
- **Decided:** a signed document is genuine only if its hash matches, its signature holds, and the signing key is this deployment's (`core.signing.check_document`). `tools/verify.py` does the same with `--portal` or `--public-key`, using only the standard library.
- **Rejected:** trusting the key carried inside the document.
- **Why:** a forger can re-sign altered content with their own key, and the result is self-consistent. Only a pinned key tells a genuine document from a well-made fake.
- **Where:** `src/core/signing.py`, `tools/verify.py` · 4d913cb, 018c074

## D-029 · Three voting channels; anti-cheat without punishing a crowded room
- **Decided:** ballots come from minted links, from emailed links (one per normalised address, kept as a keyed hash), or from accounts. Each ballot is shuffled with its own stored seed. Failed attempts are rate-limited per network, valid votes are not, and bursts are held for review. Refusals are audited after the transaction, so a rollback cannot erase them.
- **Rejected:** rate-limiting every vote per IP address; captcha; showing live tallies.
- **Why:** at a hackathon hundreds of real voters share one address. Limiting failures stops guessing without locking out a hall. Holding bursts for a human keeps the decision with someone who knows the room.
- **Where:** `src/apps/voting/`

## D-030 · T3 and T4 are claimed on the strength of our own checker
- **Decided:** `.dogfood.toml` claims T1 to T4. The official checker verifies T1 and T2; T3 and T4 are verified by `tools/run_extended.py`: 32 checks, the same report format, redirects never followed, a positive control for every rejection, re-runnable. The README truth table shows both reports and explains why the official one lists T3 and T4 as not verified.
- **Rejected:** claiming only what the official checker can see.
- **Why:** the claim is on our honour and checked by the judges. A second, readable report they can re-run is better evidence than a paragraph.
- **Where:** `tools/run_extended.py`, `.dogfood-extended.toml`, `tools/truth_table.py`

## D-031 · Event bundles with a checked manifest; the demo archive is made with them
- **Decided:** a whole event exports to one JSON bundle with a SHA-256 manifest. Import checks the manifest, creates new ids, matches people by email and writes imported reviews into the new event's ledger. The demo's published archive event is created at boot by exporting and importing the fixture event, then deliberating and publishing through the normal services.
- **Why:** organizers can move, archive and reuse events. Building the demo through the same code means the demo proves that code.
- **Where:** `src/apps/integrations/bundles.py`, `src/apps/seed/demo.py`

## D-032 · Webhooks are signed, go through the outbox, and carry no secrets
- **Decided:** HMAC-SHA256 over the exact body in `X-Dogfood-Signature`, one secret per webhook shown once, and delivery by the outbox worker with retries. Payloads carry ids and names, never scores, tallies or emails.
- **Rejected:** unsigned webhooks; sending from the web request.
- **Why:** a receiver must be able to prove a delivery is ours, and a slow receiver must never slow down a voter or a judge.
- **Where:** `src/apps/integrations/webhooks.py`

