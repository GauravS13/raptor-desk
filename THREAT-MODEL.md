# Threat model

What Raptor Desk defends against, how, and what it does **not** stop. The
tests named here are in `tests/`; the access matrix is generated from real
responses ([ACCESS-MATRIX.md](ACCESS-MATRIX.md)).

## Assets

- **Scores and rankings.** Prize money depends on them.
- **Judges' identities and individual scores.** Exposing either invites pressure and softens judgement.
- **Votes and tallies.** Tallies seen early can be gamed.
- **Personal data** (names, emails) of participants and judges.
- **The deployment's signing key**, which makes records verifiable.

## Threats and defences

| Threat | Defence | Evidence |
|---|---|---|
| A judge reads another judge's scores | Default-deny policies on every route, with a boot check that refuses a route without one. Repositories scoped to the caller. The API returns 403 before any lookup | Access matrix; the official checker |
| A route added without a policy | The system check fails at boot, and in CI | `core/checks.py`, test |
| Cross-site requests with a signed-in browser | The API ignores session cookies (bearer only); pages use CSRF tokens | Tests |
| Stored XSS in comments or submissions | Autoescaped templates; a strict CSP with no inline script or eval | Extended check "comments are escaped"; CSP test |
| Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` everywhere, except the embeddable gallery | Extended checks for the embed and for private pages |
| A score changed directly in the database | The hash-chained, signed ledger is compared with the live scores; the doctor command checks the triggers | `demo_tamper`, ledger tests |
| History rewritten (audit log, decisions, snapshots, credentials) | Append-only database triggers | Tests; `manage.py doctor` |
| A forged certificate or result | Ed25519 signatures, verified against the pinned deployment key | `/verify`, `tools/verify.py`, tests with a foreign key |
| Ballot-link guessing | 18 random bytes per token, stored only as a hash. Failed attempts rate-limited per network (429 with Retry-After) and audited by hash prefix | Extended checks "forged token rejected" and "rate limit on voting" |
| Voting twice | One vote per ballot; one member ballot per account; one emailed link per normalised address | Extended checks |
| Voting for your own team | Refused, and your own project is not on your ballot | Extended check "cannot vote for own team" |
| Tally leaks during voting | Tallies are organizer-only until close; the vote response carries no count, rank or score; webhooks carry no tallies | Extended checks |
| Vote bursts from one place | Held for organizer review, invisibly to the voter | Test "votes in a burst are held" |
| URL tokens in logs | Requests are logged by route pattern; any other log line has its tokens redacted, with a test enforcing coverage of every token route | `core/logs.py`, test |
| Webhook payload forgery | HMAC-SHA256 over the exact body, with a per-webhook secret | Extended check "webhook fires and is signed" |
| A tampered event bundle | Import refuses unless the manifest hash matches the content | Extended check "import rejects corrupted bundle" |
| Password guessing | Rate-limited sign-in; slow password hashing | Tests |

## What this does not stop

- **A dishonest organizer.** Organizers can see tallies, record deliberation decisions and publish. Every decision needs a written reason and is published with the results, but the portal cannot stop an organizer from deciding badly.
- **Anyone who controls the server and its key.** They can re-sign anything. The public key, snapshot hashes and published documents let outsiders notice changes after publication, but not before.
- **Sock-puppet voters with many email addresses, or many accounts.** Plus-addresses are folded together, but separate mailboxes are separate people to the portal. For high-stakes votes, use minted links handed out in person, or account voting for registered participants only.
- **A whole venue behind one address.** It looks like a burst. Held votes wait for an organizer; they are never silently dropped.
- **Webhook receivers on internal addresses (SSRF).** Organizers choose webhook URLs, and a self-hosted portal often needs to reach internal services, so private addresses are allowed. Only trusted people should be organizers.
- **Collusion between judges.** Leniency correction evens out harsh and generous judges, and flat-liners are excluded. Judges who agree to favour a project are not detected.
- **Denial of service.** The limits protect sign-in and voting from guessing, not the server from a flood. Put a reverse proxy in front of any public deployment.
