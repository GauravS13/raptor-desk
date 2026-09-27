# Data model

Every table uses a string primary key `<prefix>_<ulid>`: time-sortable, and it
says what it refers to. **Records loaded from the official fixtures keep their
ids** (`evt_01`, `jdg_26`, `tm_01`, `prj_07`), so routes, CSVs and exports line
up with the published data. Invariants live in the database (constraints and
triggers), not only in code.

## Tables

### Accounts
| Table | Key columns | Invariants |
|---|---|---|
| `accounts_user` | email, name, affiliation, is_admin | email unique, stored lowercase |
| `accounts_apitoken` | user, token_hash, label, is_seed, revoked_at | only a SHA-256 hash is stored; unique |
| `accounts_magiclink` | user, token_hash, expires_at, used_at | single use, 15 minutes |
| `accounts_rolegrant` | user, event, role (participant/judge/organizer) | unique (user, event, role). Roles never cross events |
| `accounts_judgeprofile` | skill_tags, coi_emails, coi_domains | domain conflicts are opt-in |

### Events
| Table | Key columns | Invariants |
|---|---|---|
| `events_event` | phase, windows (registration, submissions, judging, voting), weighting, reviews_per_project, max_load_per_judge, blind_judging, voting settings | submissions open before they close; voting opens before it closes; at least 1 review per project |
| `events_track`, `events_prize` | name / rank, amount | track names unique per event; rank ≥ 1 |
| `events_criterion` | key, weight, min/max, anchors, is_gate + gate_threshold, is_bonus | key unique per event; min < max; weight ≥ 0; a gate must have a threshold |
| `events_customquestion` | label, kind, choices, required | |
| `events_requiredartifact` | key, kind, parser | key unique per event |
| `events_phasetransition` | from, to, actor, reason, overridden, preflight | **append-only** (trigger) |

### Teams and submissions
| Table | Key columns | Invariants |
|---|---|---|
| `teams_team` | event, name | name unique per event |
| `teams_teammember` | team, user, **event**, role | **unique (event, user)**: one team per person per event |
| `teams_invite` | token_hash, expires_at, uses_left | hash only |
| `submissions_project` | team, track, status, canonical_version, gallery_order | |
| `submissions_projectversion` | n, name, tagline, description, links, tech_tags, source, source_ref, submitted_at | unique (project, n); **immutable once submitted** (trigger) |
| `submissions_customanswer` | version, question, value | one answer per question per version |
| `submissions_artifactupload` | version, artifact, file/url/text, parsed | one per artifact per version |

### Judging
| Table | Key columns | Invariants |
|---|---|---|
| `judging_judgetrack` | event, judge, track | unique |
| `judging_assignment` | judge, project, source, status, reason, queue_position | **unique (judge, project)** |
| `judging_review` | assignment, **version**, comment, improvement, status, active_seconds | **unique (assignment, version)**: a judge may review a resubmission, and each version once |
| `judging_scoreitem` | review, criterion, value | one score per criterion per review; range checked against the criterion |
| `judging_conflict` | event, judge, team, reason | unique |

### Core
| Table | Purpose | Invariants |
|---|---|---|
| `core_auditevent` | who did what, to which target, as a readable sentence | **append-only** (trigger). Actor stored by id only; IP as a salted hash |
| `core_outboxmessage` | queued emails and other side effects, with retries | dedupe key unique |
| `core_ratebucket` | fixed-window counters | keys hold salted hashes, never raw identities |

## The fixture transform

`apps/seed/fixtures.py` loads `data/fixtures.json` into this schema. It is a transform, not a copy:

| Fixture shape | Becomes |
|---|---|
| `event` | an Event in the judging phase, with the fixture's own `submissions_close` (so a new submission is refused honestly) |
| `tracks`, `judges` | Tracks; Users with the judge role, a JudgeProfile and JudgeTrack rows (the judge's fixture id becomes the user id) |
| `teams` | Teams and TeamMembers (first member is the lead) with the participant role. Distinct teams sharing a display name are disambiguated with their id |
| `projects` | grouped by (team, title, repo): **a resubmission becomes one project with versions**. `prj_07` and `prj_41` are versions 1 and 2 of `prj_07`, and the later one is canonical |
| `scores` | Assignments (source "fixture") and Reviews linked to **the version that was scored**, with ScoreItems. The three criteria are discovered from the score keys. **Missing scores stay missing** |

## Getting data out

**CSV** (organizer console → Exports, or `GET /api/events/{id}/exports/{kind}.csv`):
- `results`: rank, raw / shrunken z / additive scores, movement, 90% interval, P(top k), flags
- `reviews`: every review, per criterion, with the composite, whether it counts, comment and time
- `assignments`, `teams`, `submissions` (every version), `registrations`, `audit`

Every file starts with a header row. Cells are protected against spreadsheet
formula injection.

**REST API:** every console action is also an API operation. The OpenAPI
document is at `/api/openapi.json`.

## Storage and migration

- SQLite (WAL) at `/data/db.sqlite3` in the Docker volume. Uploads are in `/data/media`, keys in `/data/keys`.
- Migrations are Django migrations. The trigger migrations include PostgreSQL variants, so moving to PostgreSQL is a settings change.
