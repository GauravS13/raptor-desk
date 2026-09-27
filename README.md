# Raptor Desk

**The hackathon judging desk an organizer can run on Monday: their own event comes preloaded, every judge's work is accounted for, and every ranking shows its math and flags its own doubt.**

Built for DOGFOOD 2026 by Hackathon Raptors; not an official Raptors product. MIT licensed.

---

## Run it

```bash
docker compose up
```

That's it: no `.env`, no cloud account, no external service. After the first build, it runs with the network off.

| | |
|---|---|
| Portal | http://localhost:8080 |
| Local mail inbox (invites, sign-in links) | http://localhost:8025 |

On boot the portal migrates, imports the official DOGFOOD fixtures, and prints the acceptance checker's headers, the demo sign-ins and a **data hygiene report**:

```
seeded. test logins:
  organizer    Authorization: Bearer rd_seed_org_7f2a
  judge_a      Authorization: Bearer rd_seed_jdg26_91bc
  judge_b      Authorization: Bearer rd_seed_jdg24_44de
  participant  Authorization: Bearer rd_seed_prt_priya1_2e88

demo sign-in (password raptor-demo-2026):
  admin        admin@raptor-desk.local
  organizer    organizer@raptor-desk.local
  judge_a      jonas.vogel@example.org   (fixture judge jdg_26)
  judge_b      diego.herrera@example.org (fixture judge jdg_24)
  participant  priya1@example.org        (member of team tm_01)

data hygiene report for Sample Hack 2026 (evt_01):
  [resubmission] 'Dry Harbour' was submitted 2 times (prj_07, prj_41); kept as one project with versions ...
  [flat_liner] Judge jdg_07 gave identical scores to all 3 projects they reviewed ...
  [single_review_judges] Judges with a single review cannot be calibrated: jdg_01, jdg_23.
  [under_reviewed] 8 project(s) have fewer than 3 reviews ...
  [weak_evidence] Projects with fewer than 2 informative reviews ...: prj_19 (1)
  [missing_feedback] 49 of 123 reviews have no written comment ...
  [thin_tracks] ...: Developer tools (3), Open hardware (3)
```

The fixed tokens and demo passwords exist only in the default `demo` profile. `RD_PROFILE=production` creates none of them.

**Judging this project? Start with [EVALUATE.md](EVALUATE.md): a five-minute path through every scoring criterion.**

---

## Acceptance

```
claimed T1 T2, verified T1 T2
```

All seven official checks pass: [`acceptance-report.txt`](acceptance-report.txt), generated with the unmodified `tools/run.py`. It was also checked under Python 3.9, where the checker uses its fallback TOML parser.

| Tier | Status | Evidence |
|---|---|---|
| **T1 Core** | **Claimed, verified** | Accounts, 5 roles scoped per event, events with dates, tracks and prizes, invite-link teams, draft and edit until the deadline (versions), deadline enforced in the API (409 with the close time), public gallery with search and filters |
| **T2 Judging** | **Claimed, verified** | Invitations, batch and planned assignment, weighted rubric (percent or multipliers, gates, tie-break bonuses), backend isolation ([ACCESS-MATRIX.md](ACCESS-MATRIX.md)), progress dashboard, documented normalization with a proof ([JUDGING.md](JUDGING.md)), CSV at every stage |
| T3 Public | Not claimed | Audit trail page is built. Voting and comments are not yet built |
| T4 Stretch | Not claimed | A REST API with OpenAPI covers every console action (a test enforces parity). Webhooks, certificates, signed records, embed widget and import are not yet built |

---

## What makes it different

- **DOGFOOD itself comes preloaded.** A live event is configured from the `dogfood-2026` template: the T1 gate, 40/25/20/15 weights on a 0–5 scale, and bonuses that only break ties. There are also templates for Zero Dependency, MINDCODE and Code Resurrection (multiplier weights).
- **Judging you can defend.** An additive judge-bias model, a shrunken z-score and the raw mean, side by side, plus bootstrap intervals, P(top k) per prize cutoff, and flags for close calls, weak evidence and method disagreement. On the fixtures, the raw average's tie for first is resolved, and a project that ranked third only because it drew a lenient judge drops to seventh. A simulation with a known truth shows the correction beats averaging in 96% of runs.
- **Isolation is proven, not promised.** Default-deny policies on every route; the portal refuses to boot if one is missing. The API accepts bearer tokens only and never redirects. The access matrix is generated from real responses.
- **The traps are named.** The flat-lining judge, single-review judges, the resubmission (one project, two versions), missing feedback and shared team names are all detected at boot and handled in the data model.
- **An honest, append-only audit trail.** Database triggers reject edits to the audit log, the phase history and submitted versions.

---

## Documentation

| | |
|---|---|
| [EVALUATE.md](EVALUATE.md) | Five-minute evaluation path, one section per scoring criterion |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system fits together, and why |
| [DATA-MODEL.md](DATA-MODEL.md) | Schema, invariants, the fixture transform, getting data in and out |
| [JUDGING.md](JUDGING.md) | Assignment, scoring maths, normalization, uncertainty, proof |
| [ACCESS-MATRIX.md](ACCESS-MATRIX.md) | Who can read what, from real HTTP responses |
| [docs/DECISION-LOG.md](docs/DECISION-LOG.md) | Every design decision, what was rejected, and why |
| API reference | http://localhost:8080/api/docs (OpenAPI at `/api/openapi.json`) |

## Development

```bash
uv sync                 # Python 3.12, dependencies from uv.lock
make check              # ruff, formatting, architecture contracts, migrations, tests
make accept             # run the official checker against a running portal
uv run python tools/simulate_proof.py   # regenerate every number in JUDGING.md
```

## Known limitations

- T3 (voting, comments) and most of T4 are not built yet. See the table above.
- Certificates, signed judge records and the close-call review loop are planned next.
- Judges' review time is measured in the browser while the page is visible, so it is an estimate.
- The leniency model corrects each judge's level, not their scale. JUDGING.md §10 lists the model's assumptions.

## License

MIT. See [LICENSE](LICENSE) and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
