# Evaluate Raptor Desk in five minutes

One section per DOGFOOD scoring criterion, each with the fastest way to check it.
Start the portal first:

```bash
docker compose up        # wait for "serving on http://localhost:8080"
```

The same path, as clickable links: http://localhost:8080/tour. The sign-in page
has a button for each demo account. The shared demo password is `raptor-demo-2026`.

---

## Tier Completion & Correctness (40%)

```bash
python3 tools/run.py .dogfood.toml --fixtures data/fixtures.json
```

Expected result: 7 × PASS and `claimed T1 T2, verified T1 T2`. The committed
`acceptance-report.txt` is the same output.

To see it in the browser:
- The public gallery at http://localhost:8080/events/evt_01/projects lists the 40 fixture projects in order, with live search.
- A late submission is refused with the reason:
  ```bash
  curl -i -X POST http://localhost:8080/api/events/evt_01/submissions \
    -H "Authorization: Bearer rd_seed_prt_priya1_2e88" -H "Content-Type: application/json" \
    -d '{"title":"late","summary":"probe"}'
  ```
  This returns `409 {"error":{"code":"submissions_closed", ...}}`.
- Sign in as `priya1@example.org` and open **DOGFOOD 2026 (live demo)**, which is open for submissions. Choose **Form a team**, then **Your submission**: save a draft, submit it, edit it and resubmit. You get a new version, not a new project.

## Judging Integrity (25%)

**Isolation, with curl:**
```bash
# judge_b asks for judge_a's scores: 403
curl -i http://localhost:8080/api/judges/jdg_26/scores -H "Authorization: Bearer rd_seed_jdg24_44de"
# a participant asks for judge scores: 403; anonymous: 401 (never a redirect)
curl -i http://localhost:8080/api/judge/scores -H "Authorization: Bearer rd_seed_prt_priya1_2e88"
curl -i http://localhost:8080/api/judge/scores
```

The full picture is in [ACCESS-MATRIX.md](ACCESS-MATRIX.md), generated from real
responses for eight identities.

**Normalization:** sign in as `organizer@raptor-desk.local`, then open **Organize → Sample Hack 2026 → Results**. Look at:
- the raw-mean tie for first, resolved
- prj_10 dropping from 3rd to 7th: it drew a lenient judge
- places 3 to 5 flagged as close calls
- judge jdg_07 marked "identical scores everywhere: excluded"

**The close-call loop** is on the same page, under "Close calls". It lists the projects
whose prize place the data does not settle, and proposes which judge should review which
of them, and why. **Ask these judges** assigns and emails them. To see a whole round,
run `docker compose exec app python src/manage.py demo_loop`: it enters synthetic reviews
labelled `[demo]` and prints how each chance moved.

The method, its formulas, the fixture numbers and a simulation proof are in [JUDGING.md](JUDGING.md).

**From ranking to published result** (as the Organizer, on Sample Hack 2026):
1. **Overview:** move the event to *deliberation*.
2. **Deliberation:** see the undecided close calls. Record a decision with a reason, then **Freeze and sign**.
3. **Overview:** move to *published*. Preflight blocks this while close calls on the prize places are undecided or the snapshot is out of date.
4. Open http://localhost:8080/events/evt_01/results: the ranking, the decisions and the method card.
5. Sign in as the Participant and open the Glass Signal project for their feedback report. Judges are unnamed. Use **Something factually wrong?** to send a query; it appears on the organizer's **Queries** tab.

To start again, run `docker compose down -v && docker compose up`.

**Tamper evidence:** every submitted score is in a hash-chained, signed ledger. The Judging
tab verifies it on every load. To see a direct database edit caught, run:
```bash
docker compose exec app python src/manage.py demo_tamper
```
It changes one score with a raw SQL `UPDATE`, shows verification failing at that exact entry,
and puts the score back.

**Signed paperwork:**
1. Move Sample Hack 2026 to *deliberation*: every judge gets a signed protocol.
2. Sign in as Judge A: **Judge** lists it, with the ledger entry behind each score.
3. After publishing, judges get certificates and team members get participation records.
4. Paste any of them at http://localhost:8080/verify, or check one offline:
   ```bash
   python3 tools/verify.py protocol.json --portal http://localhost:8080
   ```
   A single changed character makes it fail.

**Audit trail:** the same event's **Audit** tab shows every change, filterable, with CSV download. Edits to it are rejected by the database.

## Adoptability & Operability (20%)

- One command, no `.env`, offline after the first build. The Docker healthcheck reports readiness.
- The data you get in: the official fixtures, imported with every awkward case named at boot.
- The data you get out: **Exports** gives CSV for results, reviews, assignments, teams, submissions, registrations and audit.
- Your event format is preloaded: open **DOGFOOD 2026 (live demo)**, then **Rubric**. The event page shows the published rubric.
- `RD_PROFILE=production` refuses demo accounts and fixed tokens.
- Run it like a service:
  - `make doctor` gives a health report that says what to fix.
  - `make backup` takes a consistent backup while the portal runs, and `make restore` verifies checksums before replacing anything.
  - Logs are JSON, with no tokens.
  - Admins get Prometheus `/metrics`.
  - See [OPERATIONS.md](OPERATIONS.md).

## Code Quality & Innovation (15%)

- `make check` runs lint, formatting, import-linter architecture contracts (the scoring engine may not import Django), a migration check and the full test suite.
- [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/DECISION-LOG.md](docs/DECISION-LOG.md) explain the design and what was rejected.
- `tools/verify.py` checks signed documents and ledger exports with only the Python standard library. Its tests include RFC 8032 test vector 1.

**Decisions worth a look:**
- The duplicate submission becomes versions of one project (a database trigger keeps submitted versions immutable).
- Per-event roles.
- UI/API parity enforced by a test.
- Flat-liners are recognised from per-criterion vectors, so equal totals are not mistaken for rubber-stamping.
