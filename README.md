<p align="center">
  <img src="docs/img/banner.svg" alt="Raptor Desk: the hackathon judging desk for DOGFOOD 2026" width="100%">
</p>

<p align="center">
  <a href="https://github.com/GauravS13/raptor-desk/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/GauravS13/raptor-desk/actions/workflows/ci.yml/badge.svg"></a>
  <a href="acceptance-report.txt"><img alt="Official checker 7/7" src="https://img.shields.io/badge/official%20checker-7%2F7%20PASS-2ea44f"></a>
  <a href="acceptance-extended-report.txt"><img alt="Extended checker 32/32" src="https://img.shields.io/badge/extended%20checker-32%2F32%20PASS-2ea44f"></a>
  <a href="#bonus-challenges"><img alt="Bonus challenges 4 of 4" src="https://img.shields.io/badge/bonus%20challenges-4%2F4-7c3aed"></a>
  <a href="https://codespaces.new/GauravS13/raptor-desk?quickstart=1"><img alt="Open in GitHub Codespaces" src="https://img.shields.io/badge/try%20it-GitHub%20Codespaces-24292f?logo=github"></a>
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-lightgrey"></a>
</p>

<p align="center">
  <b>From uncertainty to a signed decision.</b><br>
  Submissions, fair judging and a public vote in one self-hosted portal. It finds the prize places the data can't settle,
  asks the right judges, records what people decided and why, and publishes a signed result anyone can verify offline.
</p>

<p align="center">
  <a href="#the-scorecard">Scorecard</a> ·
  <a href="#only-in-raptor-desk">Only here</a> ·
  <a href="#run-it">Run it</a> ·
  <a href="#evaluate-in-five-minutes">Evaluate in 5 minutes</a> ·
  <a href="#tiers">Tiers</a> ·
  <a href="#against-the-dogfood-scoring-criteria">Criteria</a> ·
  <a href="#bonus-challenges">Bonuses</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#known-limitations">Limitations</a>
</p>

<!-- demo-video: thumbnail and link go here -->

> [!IMPORTANT]
> **This README cannot overclaim.** The tier table is generated from the two committed checker reports by [`tools/truth_table.py`](tools/truth_table.py), and CI fails if the README and the reports disagree. CI also re-runs the official checker against a fresh `docker compose up` and fails if its output differs from the committed report by a single byte. The official checker and fixtures are byte-for-byte the published files: their SHA-256 hashes are in the [tier table](#tiers).

---

## The scorecard

DOGFOOD is scored on four criteria. Here is each one: what we claim, and how to check it yourself.

| Criterion | Weight | What we claim | Check it yourself |
|---|:---:|---|---|
| **Tier completion and correctness** | 40% | T1 to T4 claimed. T1 and T2: **official checker 7/7**. T3 and T4: **our extended checker 32/32**, with a positive control for every rejection. Every trap in the fixtures is named at boot | `python3 tools/run.py .dogfood.toml --fixtures data/fixtures.json`<br>`python3 tools/run_extended.py .dogfood-extended.toml`<br>[Fixture traps](#every-trap-in-the-fixtures) |
| **Judging integrity** | 25% | Isolation enforced in the API, proven with curl. An additive judge-bias model that beats plain averaging in **96% of 200** simulated events. Close calls and kingmaker judges found, and settled with extra reviews or a recorded decision. Calibration published. Every score in a signed hash chain | [Curl proof](#isolation-with-curl)<br>Organizer → Sample Hack 2026 → **Results**<br>`GET /api/events/evt_01/kingmakers`<br>`manage.py demo_tamper` |
| **Adoptability and operability** | 20% | `docker compose up`, offline, no `.env`. DOGFOOD 2026 itself is preloaded. Health report, verified backup and restore, CSV and whole-event export and import | `docker compose up`<br>`make doctor`<br>[OPERATIONS.md](OPERATIONS.md) |
| **Code quality and innovation** | 15% | 369 tests, an architecture contract, a UI/API parity test, every decision written down with the alternatives rejected. The Measure → Doubt → Ask loop | `make check`<br>[DECISION-LOG.md](docs/DECISION-LOG.md) |
| *Bonus challenges* (tie-breaks) | – | 4 of 4: normalization proof, pairwise Bradley–Terry, threat model, API first | [Bonus table](#bonus-challenges) |

<p align="center">
  <img src="docs/img/close-call-loop.svg" alt="The close-call loop on the official fixtures: corrected scores with 90% intervals, five projects around the prize line flagged as close calls, extra reviews asked for, and the chances moving" width="100%">
</p>

<p align="center"><sub>The judging engine on the official fixtures. It measures, doubts its own ranking, asks for the reviews that would settle it, and leaves the rest to people, on the record.</sub></p>

---

## Only in Raptor Desk

Most judging portals stop at a ranking. Raptor Desk carries it all the way to a decision anyone can check:

```mermaid
flowchart LR
    M["Measure<br/>bias-corrected scores"] --> D["Doubt<br/>P(top k), close calls,<br/>kingmaker check"]
    D --> A["Ask<br/>the right judges,<br/>within a budget"]
    A --> M
    D --> L["Deliberate<br/>decisions with<br/>written reasons"]
    L --> F["Freeze and sign<br/>the snapshot"]
    F --> P["Publish<br/>method card,<br/>offline verifier"]
```

| | What | Where to see it |
|---|---|---|
| 1 | **Deliberation board.** Append-only decisions, each with a written reason, bonus tie-break notes and a line under the prize places. Publishing is blocked while a prize close call is undecided | Organizer → **Deliberation**, [JUDGING.md §6b](JUDGING.md#6b-from-ranking-to-published-result) |
| 2 | **Public method card.** The published results state the method, the evidence, the parameters, the snapshot hash and the signing key | Any published results page |
| 3 | **Judge passport.** An opt-in page listing a judge's signed certificates across events | `/judges/<judge>/passport` |
| 4 | **Refuses to boot with an unguarded route.** A system check fails start-up if any page or API operation lacks an access policy | [ARCHITECTURE.md](ARCHITECTURE.md), `core/policy.py` |
| 5 | **Score ledger checked against the live database,** with a tamper demo that proves it | [Tamper-evident](#tamper-evident) |
| 6 | **A health report and a verified restore.** Checksums and schema version are checked before anything is replaced, and a safety backup is taken first | `make doctor`, [OPERATIONS.md](OPERATIONS.md#restore) |
| 7 | **No URL token in any log line,** with a test that covers every token route | [THREAT-MODEL.md](THREAT-MODEL.md) |
| 8 | **Pairwise comparisons aimed at the close calls.** The "A or B?" pairs that decide a prize place come first | [JUDGING.md §6c](JUDGING.md#6c-pairwise-mode-bradleyterry) |
| 9 | **Trusted venue networks per event,** so a room full of voters on one Wi-Fi is not mistaken for ballot stuffing | [THREAT-MODEL.md](THREAT-MODEL.md) |
| 10 | **A README that cannot overclaim.** The tier table is generated from both checker reports, the JUDGING.md tables from the engine, and CI fails if either drifts | [Tiers](#tiers), [JUDGING.md](JUDGING.md) |

---

## Run it

```bash
docker compose up
```

That's the whole setup: no `.env`, no cloud account, no external service, and it runs with the network off after the first build. It boots seeded with the official DOGFOOD fixtures, plus three demo events.

> [!TIP]
> No Docker at hand? [Open it in GitHub Codespaces](https://codespaces.new/GauravS13/raptor-desk?quickstart=1). The dev container runs the same `docker compose up` and forwards both ports.

| Open | For |
|---|---|
| **http://localhost:8080/tour** | A five-minute tour: every scoring criterion, linked to the page that shows it |
| http://localhost:8080 | The portal |
| http://localhost:8025 | The local mail inbox (invitations, voting links, sign-in links) |

**Demo accounts.** The sign-in page has a one-click button for each; the password is `raptor-demo-2026`.

| Role | Email | Try this |
|---|---|---|
| Organizer | `organizer@raptor-desk.local` | Sample Hack 2026 → **Results**, **Deliberation**, **Voting** |
| Judge A | `jonas.vogel@example.org` | **Judge** → score with the keyboard, then **Compare pairs** |
| Judge B | `diego.herrera@example.org` | Try to read Judge A's scores through the API (refused) |
| Participant | `priya1@example.org` | Form a team in DOGFOOD 2026, submit, edit, resubmit |
| Admin | `admin@raptor-desk.local` | `/metrics`, and every event |

<details>
<summary><b>What the portal prints when it boots</b>: the checker's headers and a data hygiene report on the fixtures</summary>

```
seeded. test logins:
  organizer    Authorization: Bearer rd_seed_org_7f2a
  judge_a      Authorization: Bearer rd_seed_jdg26_91bc
  judge_b      Authorization: Bearer rd_seed_jdg24_44de
  participant  Authorization: Bearer rd_seed_prt_priya1_2e88
  participant_b Authorization: Bearer rd_seed_prt_lena2_61d0  (extended checker)

data hygiene report for Sample Hack 2026 (evt_01):
  [resubmission] 'Dry Harbour' was submitted 2 times (prj_07, prj_41); kept as one project with versions.
  [flat_liner] Judge jdg_07 gave identical scores to all 3 projects they reviewed.
  [single_review_judges] Judges with a single review cannot be calibrated: jdg_01, jdg_23.
  [under_reviewed] 8 project(s) have fewer than 3 reviews.
  [weak_evidence] Projects with fewer than 2 informative reviews: prj_19 (1)
  [missing_feedback] 49 of 123 reviews have no written comment.
  [thin_tracks] Developer tools (3), Open hardware (3)
```

The fixed tokens and demo passwords exist only in the default `demo` profile. `RD_PROFILE=production` creates none of them, and refuses to start while any are still in the database.
</details>

### Evaluate in five minutes

- [ ] **Start:** `docker compose up`, then open http://localhost:8080/tour.
- [ ] **Official checker:** `python3 tools/run.py .dogfood.toml --fixtures data/fixtures.json` gives 7 × PASS and `verified T1 T2`.
- [ ] **Extended checker:** `python3 tools/run_extended.py .dogfood-extended.toml` gives 32 × PASS and `verified T3 T4`.
- [ ] **Isolation:** run the [three curl calls](#isolation-with-curl). You get 403, 403 and 401.
- [ ] **Normalization:** as the Organizer, open Sample Hack 2026 → **Results**. The raw-mean tie for first is resolved, prj_10 drops from 3rd to 7th, and the undecided prize places are flagged as close calls, with the judges to ask. Further down, the kingmaker check shows that `jdg_04` and `jdg_15` each decide first place on their own.
- [ ] **Judge console:** as Judge A, score with the number keys, submit with <kbd>Ctrl</kbd>+<kbd>Enter</kbd>, then **Compare pairs** with <kbd>←</kbd> <kbd>→</kbd> <kbd>↓</kbd>.
- [ ] **Tamper evidence:** `docker compose exec app python src/manage.py demo_tamper` shows a direct database edit being caught.
- [ ] **Participant:** as Priya, open **DOGFOOD 2026 (live demo)**, form a team, submit, then resubmit: you get version 2 of the same project.
- [ ] **Community vote:** open http://localhost:8080/events/evt_vote, ask for an emailed link, and find it in the inbox at http://localhost:8025.

Every step, with what to look for: [EVALUATE.md](EVALUATE.md).

---

## Tiers

<!-- truth-table:begin (generated by tools/truth_table.py; do not edit by hand) -->

| Report | Written by | Claim line |
|---|---|---|
| [`acceptance-report.txt`](acceptance-report.txt) | the organizers' `tools/run.py`, unmodified | `claimed T1 T2 T3 T4, verified T1 T2` |
| [`acceptance-extended-report.txt`](acceptance-extended-report.txt) | this team, `tools/run_extended.py` | `claimed T3 T4, verified T3 T4` |

**How T3 and T4 are claimed.** The official checker contains checks for T1 and T2 only, so its report lists T3 and T4 as "claimed but not verified" for every team. They are verified by the team-written extended checker instead: one file, standard library only, the same report format, no redirects followed, and a positive control for every rejection. Re-run it with `python3 tools/run_extended.py .dogfood-extended.toml`.

| Tier | Claimed | Official checks | Extended checks | Status |
|---|---|---|---|---|
| T1 Core | yes | 3/3 pass | none | **verified** |
| T2 Judging | yes | 4/4 pass | none | **verified** |
| T3 Public | yes | none | 16/16 pass | **verified** |
| T4 Stretch | yes | none | 16/16 pass | **verified** |

<details>
<summary><b>Every check, one by one</b>: 39 of 39 pass</summary>

| Check | Report | Result |
|---|---|---|
| T1 gallery is public | official | PASS |
| T1 project from fixtures shown | official | PASS |
| T1 closed event refuses submissions | official | PASS |
| T2 judge sees own scores | official | PASS |
| T2 judge cannot see peer scores | official | PASS |
| T2 participant blocked | official | PASS |
| T2 csv export works | official | PASS |
| T3 results hidden from visitors during voting | extended | PASS |
| T3 results hidden from participants during voting | extended | PASS |
| T3 organizers can see results during voting | extended | PASS |
| T3 valid ballot token can vote | extended | PASS |
| T3 vote response does not leak tallies | extended | PASS |
| T3 same token cannot vote twice | extended | PASS |
| T3 forged token rejected | extended | PASS |
| T3 cannot vote for own team | extended | PASS |
| T3 duplicate identity detected | extended | PASS |
| T3 email-gated vote works end to end | extended | PASS |
| T3 ballot order randomized per voter | extended | PASS |
| T3 gallery NOT randomized | extended | PASS |
| T3 comments require identity | extended | PASS |
| T3 comments are escaped | extended | PASS |
| T3 audit trail records voting | extended | PASS |
| T3 rate limit on voting | extended | PASS |
| T4 OpenAPI served | extended | PASS |
| T4 OpenAPI matches reality | extended | PASS |
| T4 API rejects missing auth | extended | PASS |
| T4 API enforces role | extended | PASS |
| T4 webhook registration | extended | PASS |
| T4 webhook fires and is signed | extended | PASS |
| T4 certificate verifies | extended | PASS |
| T4 forged certificate rejected | extended | PASS |
| T4 judge record is signed | extended | PASS |
| T4 tampered record rejected | extended | PASS |
| T4 public record leaks no scores | extended | PASS |
| T4 embed widget frameable | extended | PASS |
| T4 private pages NOT frameable | extended | PASS |
| T4 export bundle | extended | PASS |
| T4 import round-trip | extended | PASS |
| T4 import rejects corrupted bundle | extended | PASS |

</details>

| Official file | SHA-256 | |
|---|---|---|
| `tools/run.py` | `aa98963841bc8e18e8e5d76f0499697c093dd3c0055f9d73a459f592f4dcf09d` | unmodified |
| `data/fixtures.json` | `252896bc45d49fca69ad413be40c6bfde9d9b9f9dd8db702b3ff74eaaa181121` | unmodified |

<!-- truth-table:end -->

<details>
<summary><b>What each tier contains</b></summary>

| Tier | Official requirement | Where it lives |
|---|---|---|
| **T1 Core** | Login, roles, create an event, form a team, submit, edit until the deadline, the deadline stops submissions, public gallery | Email and password or email-link sign-in; roles granted per event; invite-link teams; a resubmission becomes version 2 of the same project; the deadline is enforced in the API (409 with the close time); gallery with search and filters |
| **T2 Judging** | Invite and assign judges, a weighted rubric, judges cannot see each other's work, organizer progress view, a documented way to even out harsh and generous judges, CSV export | Invitations by email; planned assignment by track, load and conflict; percent or multiplier weights, gates and tie-break bonuses; isolation in the backend ([ACCESS-MATRIX.md](ACCESS-MATRIX.md)); progress dashboard; additive judge-bias model ([JUDGING.md](JUDGING.md)); CSV for results, reviews, assignments, teams, submissions, registrations and audit |
| **T3 Public** | Community voting, comments, results hidden until the window closes, ballots in random order, an answer to people trying to cheat | Vote by minted link, emailed link or account; comments; tallies for organizers only until close; each voter's own stable shuffle; own-team block, one vote per ballot, one link per person, rate limits, burst quarantine, full audit |
| **T4 Stretch** | REST API, webhooks, certificates, verifiable judge records, an embeddable gallery, bulk import and export | OpenAPI at `/api/docs` covering every console action; HMAC-signed webhooks; signed certificates, participation records and judge protocols, verifiable at `/verify` and offline; embeddable gallery; whole-event export and import with a checked manifest |
</details>

---

## Against the DOGFOOD scoring criteria

### Tier completion and correctness (40%)

- **Both reports are committed, and CI re-runs both checkers on every push:** [`acceptance-report.txt`](acceptance-report.txt) (official, which must match the fresh run byte for byte) and [`acceptance-extended-report.txt`](acceptance-extended-report.txt) (ours, for T3 and T4).
- **The extended checker is built to be doubted.** It is one file, standard library only, in the official report format, and says on its first line that it is ours. It follows no redirects, and every "refused" check has a matching "allowed" check, so a portal that refuses everything cannot pass.

#### Every trap in the fixtures

The official `fixtures.json` hides awkward cases. Each one is named in the boot log and handled on purpose:

| Trap in the fixtures | Boot log | What the desk does |
|---|---|---|
| "Dry Harbour" submitted twice (prj_07, prj_41), 3 minutes before the close | `[resubmission]` | One project with two versions; the later one counts, and each judge counts once |
| jdg_07 gives 4/4/4 to every project | `[flat_liner]` | Recognised from the per-criterion scores, not the totals, and left out of the fit |
| jdg_19's totals tie by coincidence (5+4+2 and 4+3+4) | none | **Not** flagged: equal totals are not rubber-stamping |
| jdg_01 and jdg_23 have one review each | `[single_review_judges]` | Kept, with their leniency shrunk toward zero |
| 8 projects have 2 reviews instead of 3 | `[under_reviewed]` | Reported, and their intervals are wider, so the doubt shows |
| prj_19 rests on one informative review | `[weak_evidence]` | Flagged on the ranking |
| 49 of 123 reviews have no comment | `[missing_feedback]` | Reported; new reviews need written feedback |
| Two tracks have only three judges | `[thin_tracks]` | The close-call loop says when no eligible judge is left, instead of breaking the track rule |
| "StillTrail" is the name of three different teams | none | Each team keeps its id and gets a unique display name |

### Judging integrity (25%)

#### Isolation, with curl

The rules disqualify "role checks only in the frontend", and ask for a curl test that fails. Here are three, against the running portal:

```console
$ curl -s -w ' → %{http_code}\n' localhost:8080/api/judges/jdg_26/scores -H "Authorization: Bearer rd_seed_jdg24_44de"
{"error": {"code": "forbidden", "message": "Judges can only read their own scores.", "details": {}}} → 403

$ curl -s -w ' → %{http_code}\n' localhost:8080/api/judge/scores -H "Authorization: Bearer rd_seed_prt_priya1_2e88"
{"error": {"code": "forbidden", "message": "You do not have access to this resource.", "details": {}}} → 403

$ curl -s -w ' → %{http_code}\n' localhost:8080/api/judge/scores
{"error": {"code": "unauthorized", "message": "Authentication required.", "details": {}}} → 401
```

Every route has a default-deny policy, and the portal refuses to boot if one is missing. The [access matrix](ACCESS-MATRIX.md) is generated from real responses for eight identities.

#### Normalization you can defend

Each score is modelled as the project's quality plus the judge's leniency plus noise, fitted by ridge-regularised alternating least squares:

$$s_{jp} = q_p + b_j + \varepsilon_{jp}$$

$b_j$ is the judge's leniency: positive is generous, negative is harsh. It never divides by a judge's spread, so the flat-liner and single-review judges cannot break it. The textbook z-score gives 7 fixture projects a `NaN`.

<table>
  <tr>
    <td width="45%"><img src="docs/img/rank-movement.svg" alt="Rank movement on the fixtures from the raw mean to the bias-corrected ranking"></td>
    <td width="55%"><img src="docs/img/normalization-proof.svg" alt="Mean Spearman correlation with the true order over 200 simulated events: raw 0.697, shrunken z 0.778, additive 0.813"><br><br>
      <b>On the fixtures:</b> the raw average ties for first, and the model resolves it. prj_10 ranked 3rd only because it drew a generous judge; corrected, it is 7th.<br><br>
      <b>Against a known truth:</b> in 200 simulated events, the model beats plain averaging in 96% of runs. The numbers are generated, and checked in CI: <a href="JUDGING.md">JUDGING.md</a>.
    </td>
  </tr>
</table>

#### Doubt becomes action

The bootstrap gives each project its chance of finishing in every prize place. Chances between 20% and 80% are close calls. The desk proposes which judge should review which project, and why, within a budget the organizer sets. One click assigns and emails them. What is still close goes to deliberation, where each decision needs a written reason. A model never silently decides prize money.

**Kingmaker check.** The ranking is refitted without each judge in turn. Any judge whose removal alone changes who holds a prize place, or who comes first, is listed on the results page and in the API (`/api/events/{id}/kingmakers`). On the fixtures, 9 of 30 judges move a top-5 place, and two of them alone decide first place: something the bootstrap does not see. [JUDGING.md §9b](JUDGING.md#9b-kingmaker-check-does-one-judge-decide-a-prize)

**Calibration, published whatever it says.** Over 200 simulated events with a known truth, the "90% intervals" contain the true quality only 52% of the time, and a stated 95%+ chance is right about four times in five. The chances still beat the base rate clearly, and half of the projects ranked on the wrong side of the prize line were flagged as close calls beforehand. That is why chances only point people at the doubt, and never decide. [JUDGING.md §8b](JUDGING.md#8b-calibration-is-the-stated-uncertainty-honest)

#### Tamper-evident

Every submitted score is in a hash-chained, signed ledger, checked against the live database. `demo_tamper` edits one score with raw SQL:

```console
$ docker compose exec app python src/manage.py demo_tamper
before: valid, 126 entries
tampered: review rev_01m3nre17gmd5tmnvq1gqap5v3 (jdg_18 on prj_39), functionality 3 -> 5, by a direct SQL UPDATE
after:  INVALID at entry 119: the scores of review rev_01m3nre17gmd5tmnvq1gqap5v3 were changed outside the portal
restored: valid, 126 entries
```

Results are frozen into signed snapshots. Judges get signed protocols and certificates, teams get participation records, and anyone can check them at `/verify` or offline with `tools/verify.py`.

#### Assignment that respects people

Track coverage, load caps and conflicts are planned. Judges can recuse themselves in one click, and a replacement is proposed. Each judge sees their queue in a random order.

### Adoptability and operability (20%)

- **DOGFOOD itself is preloaded.** The live demo event is built from the `dogfood-2026` template: the T1 gate, 40/25/20/15 weights, and bonuses that only break ties. Templates for three other Raptors events are included.
- **Built to run for real:**
  - `manage.py doctor` gives a health report that says what to fix
  - online backup, and a restore that verifies checksums before replacing anything
  - JSON logs that never contain URL tokens
  - admin-only Prometheus metrics
  - a production profile with no demo data, which refuses to start if demo tokens or passwords are still in the database
- **Data in and out:** CSV exports at every stage, whole-event bundles for archive and migration, and signed webhooks for everything else.

### Code quality and innovation (15%)

- **Checks on every commit:** an architecture contract (the scoring engine cannot import the web framework), a test that every UI action exists in the API, and 369 tests.
- **Every design choice written down,** with the alternatives rejected: [docs/DECISION-LOG.md](docs/DECISION-LOG.md).
- **Innovation:** the Measure → Doubt → Ask loop, pairwise mode feeding the close calls, and records anyone can verify offline.

---

## Bonus challenges

| Bonus | Status | Evidence |
|---|---|---|
| **Normalization Proof** (hard) | ✅ Done | [JUDGING.md](JUDGING.md): method, fixture results, a 200-run simulation against a known truth, sensitivity analysis. The tables are generated and checked in CI |
| **Pairwise Mode** (hard) | ✅ Done | Judges answer "A or B?" with <kbd>←</kbd> <kbd>→</kbd>, or <kbd>↓</kbd> for too close to call. A Bradley–Terry estimator (MM algorithm, bootstrap intervals) ranks them beside the scores; the pairs that decide close calls come first. [JUDGING.md §6c](JUDGING.md) |
| **Threat Model** (medium) | ✅ Done | [THREAT-MODEL.md](THREAT-MODEL.md): Sybil votes, ballot stuffing, collusion, tampering and more, with a ranked list of what is **not** stopped |
| **API First** (medium) | ✅ Done | Every console action is in the REST API, and a test enforces it. OpenAPI at `/api/docs` and `/api/openapi.json` |

---

## Screenshots

<p align="center">
  <img src="docs/img/screens/results-close-calls.png" alt="Organizer results: bias-corrected ranking with the close calls the desk proposes to settle" width="92%"><br>
  <sub><b>Organizer results:</b> the corrected ranking, each project's chances, and the close calls with the judges to ask</sub>
</p>

| | |
|---|---|
| <img src="docs/img/screens/judge-review.png" alt="Judge review console"><br>**Judge console:** number keys per criterion, <kbd>Ctrl</kbd>+<kbd>Enter</kbd> to submit, autosave, one-click recusal | <img src="docs/img/screens/pairwise.png" alt="Pairwise mode"><br>**Pairwise mode:** A or B, with <kbd>←</kbd> <kbd>→</kbd> |
| <img src="docs/img/screens/deliberation.png" alt="Deliberation board"><br>**Deliberation:** decisions with written reasons, a line under the prize places | <img src="docs/img/screens/public-results.png" alt="Public results with method card"><br>**Published results:** the method card with the hash and signing key |
| <img src="docs/img/screens/ballot.png" alt="Community vote ballot"><br>**Community vote:** each voter's own order, no own-team votes | <img src="docs/img/screens/verify.png" alt="Certificate verification"><br>**Verify:** anyone can check a certificate or record |
| <img src="docs/img/screens/home.png" alt="Home page with a published ranking"><br>**Home:** a real published ranking, with its chances and signature | <img src="docs/img/screens/tour.png" alt="Five-minute tour"><br>**Tour:** each scoring criterion, one click away |

---

## How it is built

```mermaid
flowchart TB
    subgraph compose["docker compose up"]
        app["app: Django + django-ninja<br/>pages (HTMX) and REST API"]
        worker["worker: outbox<br/>email, webhooks"]
        mail["Mailpit<br/>local inbox"]
        db[("SQLite (WAL)<br/>on the data volume")]
    end
    app --> db
    worker --> db
    worker --> mail
```

<details>
<summary><b>Details</b></summary>

- **Stack:** Django 5.2 LTS, django-ninja for the API, HTMX for the pages, SQLite in WAL mode.
- **Scoring engine:** a pure Python package with no web framework in it, enforced by an architecture contract.
- **Append-only tables:** the audit log, phase history, submitted versions, decisions, snapshots, the score ledger, signed documents and pairwise comparisons. Database triggers enforce it, and `manage.py doctor` checks the triggers.
- **Walkthroughs:** [ARCHITECTURE.md](ARCHITECTURE.md) and [DATA-MODEL.md](DATA-MODEL.md).
</details>

---

## Documentation

| Document | What it answers |
|---|---|
| [EVALUATE.md](EVALUATE.md) | How to check every scoring criterion in five minutes, including both checkers |
| [JUDGING.md](JUDGING.md) | Assignment, scoring, normalization with proof, uncertainty and its calibration, the close-call loop, the kingmaker check, pairwise mode, deliberation and publishing |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system fits together, and why |
| [DATA-MODEL.md](DATA-MODEL.md) | Every table and its invariants, and how the fixtures are loaded |
| [ACCESS-MATRIX.md](ACCESS-MATRIX.md) | Who can read what, generated from real responses |
| [THREAT-MODEL.md](THREAT-MODEL.md) | What each defence stops, and what it does not |
| [OPERATIONS.md](OPERATIONS.md) | Backups, health report, logs, metrics, configuration, going to production |
| [docs/DECISION-LOG.md](docs/DECISION-LOG.md) | Every design decision and the alternatives rejected |

<details>
<summary><b>Commands for developers</b></summary>

```bash
uv sync                                                  # Python 3.12, dependencies from uv.lock
make check                                               # lint, contracts, migrations, tests, generated-doc checks
python3 tools/run.py .dogfood.toml --fixtures data/fixtures.json   # official checker (T1, T2)
python3 tools/run_extended.py .dogfood-extended.toml               # extended checker (T3, T4)
python3 tools/verify.py document.json --portal http://localhost:8080   # check any signed record offline
```
</details>

---

## Known limitations

The gaps we know of, in our own words:

- **T3 and T4 are verified by our extended checker, not the official one,** which contains checks for T1 and T2 only. The tier table shows both, and anyone can re-run ours.
- **Burst detection groups votes by network address.** A venue where everyone shares one address looks like a burst, so an organizer reviews held votes instead of the portal discarding them.
- **The leniency model corrects each judge's level, not their scale.** Judges' review time is measured in the browser, so it is an estimate. See [JUDGING.md §10](JUDGING.md).
- **The 90% intervals are too narrow.** In simulation they contain the truth 52% of the time, because resampling two or three reviews underestimates how much another judge could disagree. The chances are used to find doubt, never to decide. See [JUDGING.md §8b](JUDGING.md#8b-calibration-is-the-stated-uncertainty-honest).
- **Two extra reviews rarely settle a close call on their own.** The loop is meant to run again as reviews arrive; whatever is still close goes to deliberation.
- **Some threats are not stopped:** dishonest organizers, sock-puppet email addresses and judge collusion. See [THREAT-MODEL.md](THREAT-MODEL.md).

---

<p align="center">
  Built for <b>DOGFOOD 2026</b> by <a href="https://github.com/GauravS13">@GauravS13</a> · MIT licensed · not an official Raptors product
</p>
