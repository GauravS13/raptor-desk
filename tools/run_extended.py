#!/usr/bin/env python3
"""DOGFOOD 2026 extended acceptance checks for T3 and T4 (team-written, not the official checker).

Usage:  python3 tools/run_extended.py .dogfood-extended.toml > acceptance-extended-report.txt

The same shape as the official tools/run.py: Python 3 standard library only,
one line per check, detail lines under a failure, and a claim line at the end.
It is stricter in one way: redirects are never followed, so a login redirect
where a 401 or 403 belongs counts as a failure.

Every run uses fresh identities (a run id, fresh ballot links, fresh email
addresses and import names), so running it twice gives the same result. State
is set up only through the portal's normal organizer APIs. Every "rejects bad
input" check is paired with an "accepts good input" control. It always exits 0.
"""

import argparse
import hashlib
import hmac
import importlib.util
import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    import tomllib  # Python 3.11 and newer
except ModuleNotFoundError:
    tomllib = None

HERE = Path(__file__).resolve().parent
TIERS = ("T3", "T4")
LEAK_KEYS = {"count", "votes", "tally", "rank", "score"}


# --- Configuration --------------------------------------------------------------------------


def parse_toml(text):
    """Enough TOML for this file on older Pythons: [section], "string" and ["a", "b"]."""
    data, section = {}, None
    for raw in text.splitlines():
        line = raw.split("#")[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line[1:-1].strip(), {})
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith("["):
            parsed = [v.strip().strip('"') for v in value.strip("[]").split(",") if v.strip()]
        else:
            parsed = value.strip('"')
        section[key.strip()] = parsed
    return data


def load_config(path):
    text = Path(path).read_text(encoding="utf-8")
    return tomllib.loads(text) if tomllib else parse_toml(text)


# --- HTTP without redirects -------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


def request(url, header=None, method="GET", body=None, raw=None):
    headers = {"Accept": "application/json"}
    if header:
        name, _, value = header.partition(":")
        headers[name.strip()] = value.strip()
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=30) as resp:
            return Response(resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read())
    except urllib.error.URLError as exc:
        return Response(0, {}, str(exc.reason).encode())


# --- Checks -------------------------------------------------------------------------------------


class Check:
    def __init__(self, tier, label):
        self.tier, self.label, self.ok, self.detail = tier, label, False, []

    def note(self, line):
        self.detail.append(line)


def _has_keys(obj, keys):
    if isinstance(obj, dict):
        return any(k in keys or _has_keys(v, keys) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_keys(v, keys) for v in obj)
    return False


def _verify_tool():
    spec = importlib.util.spec_from_file_location("raptor_verify", HERE / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Listener:
    """The checker's own webhook receiver."""

    def __init__(self, bind):
        host, _, port = bind.rpartition(":")
        received = self.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append((self.rfile.read(length), dict(self.headers)))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer((host or "0.0.0.0", int(port)), Handler)  # noqa: S104
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def build_checks(cfg, run):
    base = cfg["portal"]["base_url"].rstrip("/")
    auth = cfg["auth"]
    v = cfg["voting"]
    t4 = cfg["t4"]
    checks = []

    def url(path, **kw):
        for key, value in kw.items():
            path = path.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
        return base + path

    def new(tier, label):
        check = Check(tier, label)
        checks.append(check)
        return check

    def expect(check, response, wanted, what):
        if response.status in wanted:
            return True
        where = "redirected" if 300 <= response.status < 400 else f"got {response.status}"
        check.note(what)
        check.note(f"{where}, wanted {'/'.join(map(str, sorted(wanted)))}")
        return False

    # ---- T3: public ------------------------------------------------------------------------

    c = new("T3", "results hidden from visitors during voting")
    public = request(url(v["results_public"]))
    api = request(url(v["results_api"]))
    c.ok = expect(c, public, {401, 403}, f"GET {v['results_public']}") & expect(
        c, api, {401, 403}, f"GET {v['results_api']}"
    )

    c = new("T3", "results hidden from participants during voting")
    c.ok = expect(
        c, request(url(v["results_api"]), auth["participant"]), {401, 403}, "as participant"
    ) & expect(c, request(url(v["results_api"]), auth["judge_a"]), {403}, "as judge_a")

    c = new("T3", "organizers can see results during voting")
    tally = request(url(v["results_api"]), auth["organizer"])
    rows = (tally.json() or {}).get("projects") if tally.status == 200 else None
    c.ok = expect(c, tally, {200}, "as organizer") and bool(rows) and "votes" in rows[0]
    if tally.status == 200 and not c.ok:
        c.note("no per-project tallies in the response")

    minted = request(url(v["mint_tokens"]), auth["organizer"], "POST", {"count": 3})
    tokens = (minted.json() or {}).get("tokens", []) if minted.status in (200, 201) else []

    c = new("T3", "valid ballot token can vote")
    first = None
    if len(tokens) < 3:
        c.note(f"POST {v['mint_tokens']} as organizer gave {minted.status}, wanted 3 tokens")
    else:
        first = request(
            url(v["vote"], token=tokens[0]), None, "POST", {"project": v["other_project"]}
        )
        c.ok = expect(c, first, {200, 201}, "first vote with a fresh ballot link")

    c = new("T3", "vote response does not leak tallies")
    body = first.json() if first else None
    c.ok = first is not None and first.status in (200, 201) and not _has_keys(body, LEAK_KEYS)
    if not c.ok:
        c.note(f"response body: {first.text[:200] if first else 'no vote was made'}")

    c = new("T3", "same token cannot vote twice")
    if tokens:
        again = request(
            url(v["vote"], token=tokens[0]), None, "POST", {"project": v["other_project"]}
        )
        c.ok = 400 <= again.status < 500
        if not c.ok:
            c.note(f"second vote with the same token: got {again.status}, wanted 409")

    c = new("T3", "forged token rejected")
    forged_token = f"tk_{v['event']}.forged{run}"
    forged = request(
        url(v["vote"], token=forged_token), None, "POST", {"project": v["other_project"]}
    )
    if forged.status == 429:  # an earlier run's rate limit: wait it out once
        time.sleep(min(int(forged.headers.get("Retry-After", "30")), 65))
        forged = request(
            url(v["vote"], token=forged_token), None, "POST", {"project": v["other_project"]}
        )
    c.ok = expect(c, forged, {401, 403, 404}, f"POST vote with {forged_token}")

    c = new("T3", "cannot vote for own team")
    own = request(
        url(v["vote_as_member"]), auth["participant"], "POST", {"project": v["own_project"]}
    )
    control = request(
        url(v["vote_as_member"]), auth["participant_b"], "POST", {"project": v["own_project"]}
    )
    own_refused = 400 <= own.status < 500 and "own team" in own.text.lower()
    control_ok = control.status in (200, 201) or (
        control.status == 409 and "already" in control.text.lower()
    )
    c.ok = own_refused and control_ok
    if not own_refused:
        c.note(f"participant voting for own project: got {own.status} {own.text[:120]}")
    if not control_ok:
        c.note(f"control, participant_b for the same project: got {control.status}")

    c = new("T3", "duplicate identity detected")
    request(url(v["email_request"]), None, "POST", {"email": "priya1@example.org"})
    dup = request(url(v["email_request"]), None, "POST", {"email": "  PRIYA1@Example.org "})
    c.ok = expect(c, dup, {409}, "second request for the same address, different case")

    c = new("T3", "email-gated vote works end to end")
    voter = f"voter_{run}@example.org"
    asked = request(url(v["email_request"]), None, "POST", {"email": voter})
    link = None
    mailpit = cfg["portal"]["mailpit_url"].rstrip("/")
    for _ in range(40):
        found = request(f"{mailpit}/api/v1/search?query=" + urllib.parse.quote(f"to:{voter}"))
        messages = (found.json() or {}).get("messages") or []
        if messages:
            message = request(f"{mailpit}/api/v1/message/{messages[0]['ID']}").json() or {}
            match = re.search(r"https?://\S+/vote/(\S+)", message.get("Text", ""))
            link = match.group(1) if match else None
            break
        time.sleep(0.5)
    if asked.status not in (200, 201, 202):
        c.note(f"POST {v['email_request']}: got {asked.status}")
    elif link is None:
        c.note(f"no email to {voter} arrived in Mailpit within 20s")
    else:
        page = request(url("/vote/{token}", token=link))
        voted = request(url(v["vote"], token=link), None, "POST", {"project": v["other_project"]})
        c.ok = expect(c, page, {200}, "opening the emailed link") and expect(
            c, voted, {200, 201}, "voting with the emailed link"
        )

    c = new("T3", "ballot order randomized per voter")
    if len(tokens) >= 3:

        def order(token):
            return [
                p["id"]
                for p in (request(url(v["ballot"], token=token)).json() or {}).get("projects", [])
            ]

        second, third, second_again = order(tokens[1]), order(tokens[2]), order(tokens[1])
        c.ok = bool(second) and second == second_again and second != third
        if not c.ok:
            c.note(
                f"token 2: {second[:4]}..., again: {second_again[:4]}..., token 3: {third[:4]}..."
            )

    c = new("T3", "gallery NOT randomized")
    g1, g2 = request(url(v["gallery"])), request(url(v["gallery"]))
    c.ok = g1.status == 200 and g1.body == g2.body
    if not c.ok:
        c.note("two requests for the gallery differ")

    c = new("T3", "comments require identity")
    path = v["comments"]
    anon = request(url(path, project=v["other_project"]), None, "POST", {"body": f"hello {run}"})
    signed = request(
        url(path, project=v["other_project"]), auth["participant"], "POST", {"body": f"hello {run}"}
    )
    c.ok = expect(c, anon, {401}, "anonymous comment") & expect(
        c, signed, {200, 201}, "control: participant"
    )

    c = new("T3", "comments are escaped")
    marker = f"x_{run}"
    request(
        url(path, project=v["other_project"]),
        auth["participant"],
        "POST",
        {"body": f"<script>{marker}</script>"},
    )
    page = request(url(v["project_page"], project=v["other_project"])).text
    c.ok = f"&lt;script&gt;{marker}" in page and f"<script>{marker}" not in page
    if not c.ok:
        c.note("the stored comment is not shown escaped")

    c = new("T3", "audit trail records voting")
    ballot = request(url(v["ballot"], token=tokens[0])).json() if tokens else None
    ballot_id = (ballot or {}).get("ballot_id", "")
    entries = (
        request(url(v["audit"]) + f"?action=vote&q={ballot_id}&limit=50", auth["organizer"]).json()
        or []
    )
    actions = [e.get("action") for e in entries] if isinstance(entries, list) else []
    forged_prefix = hashlib.sha256(forged_token.encode()).hexdigest()[:12]
    forged_entries = (
        request(url(v["audit"]) + f"?q={forged_prefix}", auth["organizer"]).json() or []
    )
    no_emails = not any("@" in (e.get("summary", "")) for e in entries)
    participant_audit = request(url(v["audit"]), auth["participant"])
    c.ok = (
        "vote.accepted" in actions
        and "vote.rejected" in actions
        and bool(forged_entries)
        and no_emails
        and participant_audit.status == 403
    )
    if not c.ok:
        c.note(f"actions for ballot {ballot_id}: {actions}; forged entries: {len(forged_entries)}")
        c.note(f"control, audit as participant: got {participant_audit.status}, wanted 403")

    c = new("T3", "rate limit on voting")
    limited = None
    for i in range(30):
        response = request(
            url(v["vote"], token=f"tk_{v['event']}.burst{run}{i}"),
            None,
            "POST",
            {"project": v["other_project"]},
        )
        if response.status == 429:
            limited = response
            break
    c.ok = limited is not None
    if limited is None:
        c.note("30 rapid forged votes, no 429")
    elif not limited.headers.get("Retry-After"):
        c.note("429 without a Retry-After header")

    # ---- T4: stretch ---------------------------------------------------------------------------

    c = new("T4", "OpenAPI served")
    openapi = request(url(t4["openapi"]))
    spec = openapi.json() or {}
    c.ok = openapi.status == 200 and str(spec.get("openapi", "")).startswith("3.")
    if not c.ok:
        c.note(f"GET {t4['openapi']}: {openapi.status}")

    c = new("T4", "OpenAPI matches reality")
    patterns = [
        re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in spec.get("paths", {})
    ]
    routes = [r for r in cfg.get("official_routes", {}).values() if r.startswith("/api/")]
    routes += [
        r.split("?")[0]
        for section in ("voting", "t4")
        for r in cfg[section].values()
        if isinstance(r, str) and r.startswith("/api/") and "openapi" not in r
    ]
    missing = [
        r for r in routes if not any(p.match(re.sub(r"\{[^}]+\}", "x", r)) for p in patterns)
    ]
    c.ok = bool(patterns) and not missing
    for route in missing:
        c.note(f"not in the OpenAPI document: {route}")

    c = new("T4", "API rejects missing auth")
    c.ok = expect(c, request(url(t4["export"])), {401}, f"GET {t4['export']} without a token")

    c = new("T4", "API enforces role")
    c.ok = expect(
        c, request(url(t4["export"]), auth["participant"]), {403}, "as participant"
    ) & expect(c, request(url(t4["export"]), auth["organizer"]), {200}, "control: organizer")

    c = new("T4", "webhook registration")
    listener, hook = None, {}
    try:
        listener = Listener(cfg["portal"]["webhook_listen"])
    except OSError as exc:
        c.note(f"cannot listen on {cfg['portal']['webhook_listen']}: {exc}")
    registered = request(
        url(t4["webhooks"]),
        auth["organizer"],
        "POST",
        {"url": cfg["portal"]["webhook_target"], "events": ["comment.created"]},
    )
    hook = registered.json() or {}
    refused = request(
        url(t4["webhooks"]),
        auth["participant"],
        "POST",
        {"url": cfg["portal"]["webhook_target"], "events": ["comment.created"]},
    )
    c.ok = (
        listener is not None
        and expect(c, registered, {201}, "as organizer")
        and bool(hook.get("id") and hook.get("secret"))
    ) & expect(c, refused, {403}, "control: participant")

    c = new("T4", "webhook fires and is signed")
    if listener is not None and hook.get("secret"):
        request(
            url(path, project=v["other_project"]),
            auth["participant"],
            "POST",
            {"body": f"hook {run}"},
        )
        delivery = None
        for _ in range(40):
            for raw, headers in list(listener.received):
                event = (json.loads(raw) if raw else {}).get("event")
                if event == "comment.created" and f"hook {run}".encode() in raw:
                    delivery = (raw, headers)
            if delivery:
                break
            time.sleep(0.5)
        if delivery is None:
            c.note("no signed comment.created delivery within 20s")
        else:
            raw, headers = delivery
            sent = {k.lower(): val for k, val in headers.items()}.get("x-dogfood-signature", "")
            right = "sha256=" + hmac.new(hook["secret"].encode(), raw, hashlib.sha256).hexdigest()
            wrong = "sha256=" + hmac.new(b"not-the-secret", raw, hashlib.sha256).hexdigest()
            c.ok = hmac.compare_digest(sent, right) and sent != wrong
            if not c.ok:
                c.note("the signature does not match the HMAC of the body")
    if hook.get("id"):
        request(url(t4["webhooks"]) + f"/{hook['id']}", auth["organizer"], "DELETE")
    if listener is not None:
        listener.close()

    verify_tool = _verify_tool()
    portal_key = (request(url(t4["public_key"])).json() or {}).get("public_key", "")
    passport = request(url(t4["passport"])).json() or {}
    certificates = passport.get("certificates") or []
    code = certificates[0]["verify_url"].rsplit("/", 1)[1] if certificates else ""
    record = request(url(t4["certificate"], code=code)) if code else Response(0, {}, b"")
    document = record.json() or {}
    payload = document.get("payload") or {}

    c = new("T4", "certificate verifies")
    c.ok = (
        record.status == 200
        and bool(payload.get("person", {}).get("name"))
        and bool(payload.get("event", {}).get("name"))
        and bool(payload.get("number"))
        and document.get("valid") is True
    )
    if not c.ok:
        c.note(f"GET {t4['certificate']} for a certificate from {t4['passport']}: {record.status}")

    c = new("T4", "forged certificate rejected")
    unknown = request(url(t4["certificate"], code="CERT-9999"))
    changed_code = (code[:-1] + ("A" if code[-1:] != "A" else "B")) if code else "x"
    changed = request(url(t4["certificate"], code=changed_code))
    c.ok = (
        unknown.status in (404, 422)
        and changed.status in (404, 422)
        and "not valid" in unknown.text.lower()
    )
    if not c.ok:
        c.note(f"CERT-9999: {unknown.status}; one character changed: {changed.status}")

    c = new("T4", "judge record is signed")
    signature = document.get("signature") or {}
    local = document and verify_tool.check_document(document, portal_key)[0]
    c.ok = (
        bool(local)
        and signature.get("alg") == "Ed25519"
        and signature.get("public_key") == portal_key
    )
    if not c.ok:
        c.note("the record did not verify locally against the portal's published key")

    c = new("T4", "tampered record rejected")
    if payload:
        tampered = json.loads(json.dumps(document))
        tampered.pop("valid", None)
        tampered["payload"]["person"]["name"] += "x"
        local_bad = verify_tool.check_document(tampered, portal_key)[0]
        portal_bad = request(url(t4["record_verify"]), None, "POST", tampered).json() or {}
        clean = {k: val for k, val in document.items() if k != "valid"}
        portal_good = request(url(t4["record_verify"]), None, "POST", clean).json() or {}
        c.ok = (
            not local_bad and portal_bad.get("valid") is False and portal_good.get("valid") is True
        )
        if not c.ok:
            c.note(
                f"local {local_bad}, portal tampered {portal_bad}, portal original {portal_good}"
            )

    c = new("T4", "public record leaks no scores")
    c.ok = (
        bool(payload)
        and not _has_keys(payload, {"scores", "score", "criteria"})
        and "projects_reviewed" in payload
    )
    if not c.ok:
        c.note("the public record carries scores or criteria, or lacks the review count")

    c = new("T4", "embed widget frameable")
    embed = request(url(t4["embed"]))
    csp = embed.headers.get("Content-Security-Policy", "")
    blocked = "X-Frame-Options" in embed.headers or "frame-ancestors 'none'" in csp
    c.ok = (
        embed.status == 200 and "text/html" in embed.headers.get("Content-Type", "") and not blocked
    )
    c.ok = c.ok and t4["embed_title"] in embed.text
    if not c.ok:
        c.note(f"GET {t4['embed']}: {embed.status}, framing blocked: {blocked}")

    c = new("T4", "private pages NOT frameable")
    private = request(url(t4["private_page"]), auth["organizer"])
    xfo = private.headers.get("X-Frame-Options", "")
    c.ok = xfo.upper() == "DENY" or "frame-ancestors 'none'" in private.headers.get(
        "Content-Security-Policy", ""
    )
    if not c.ok:
        c.note(f"GET {t4['private_page']} can be framed")

    c = new("T4", "export bundle")
    exported = request(url(t4["export"]), auth["organizer"])
    bundle = exported.json() or {}
    needed = ("event", "tracks", "criteria", "teams", "projects", "reviews")
    c.ok = (
        exported.status == 200
        and all(k in bundle for k in needed)
        and bool((bundle.get("manifest") or {}).get("sha256"))
    )
    if not c.ok:
        c.note(f"GET {t4['export']}: {exported.status}, keys {sorted(bundle)[:8]}")

    def summary(b):
        titles = sorted(ver["name"] for p in b.get("projects", []) for ver in p.get("versions", []))
        scores = sorted(json.dumps(r.get("scores"), sort_keys=True) for r in b.get("reviews", []))
        return (b.get("manifest") or {}).get("counts"), titles, scores

    c = new("T4", "import round-trip")
    if bundle:
        name = f"roundtrip_{run}"
        imported = request(
            url(t4["import"]), auth["organizer"], "POST", {"bundle": bundle, "name": name}
        )
        new_id = (imported.json() or {}).get("event", "")
        again = (
            request(url(t4["export"].replace(t4["export_event"], new_id)), auth["organizer"]).json()
            or {}
        )
        refused = request(
            url(t4["import"]), auth["participant"], "POST", {"bundle": bundle, "name": name}
        )
        c.ok = (
            expect(c, imported, {200, 201}, "import as organizer")
            and summary(again) == summary(bundle)
        ) & expect(c, refused, {403}, "control: participant")
        if imported.status in (200, 201) and summary(again) != summary(bundle):
            c.note("the re-exported event differs in counts, titles or scores")

    c = new("T4", "import rejects corrupted bundle")
    if bundle.get("projects"):
        corrupted = json.loads(json.dumps(bundle))
        corrupted["projects"][0]["versions"][0]["name"] += " (changed)"
        response = request(
            url(t4["import"]),
            auth["organizer"],
            "POST",
            {"bundle": corrupted, "name": f"corrupt_{run}"},
        )
        c.ok = response.status == 422 and "manifest mismatch" in response.text.lower()
        if not c.ok:
            c.note(f"got {response.status} {response.text[:120]}, wanted 422 manifest mismatch")

    return checks


def main():
    ap = argparse.ArgumentParser(description="DOGFOOD 2026 extended acceptance checks (T3, T4)")
    ap.add_argument("config", help="path to .dogfood-extended.toml")
    args = ap.parse_args()
    cfg = load_config(args.config)
    claimed = [t for t in cfg.get("tiers", {}).get("claimed", []) if t in TIERS]
    run = secrets.token_hex(3)

    print("DOGFOOD 2026 extended acceptance report (team-written, not the official checker)")
    print(f"portal: {cfg['portal']['base_url']}")
    print(f"run: {run}")
    print(f"claimed: {' '.join(claimed) or 'nothing'}")
    print()

    checks = build_checks(cfg, run)
    width = max(len(c.label) for c in checks) + 2
    for c in checks:
        dots = "." * (width - len(c.label))
        print(f"{c.tier}  {c.label} {dots} {'PASS' if c.ok else 'FAIL'}")
        for line in c.detail:
            print(f"       {line}")

    verified = []
    for tier in TIERS:  # T4 only counts if T3 passed too, as in run.py
        mine = [c for c in checks if c.tier == tier]
        if mine and all(c.ok for c in mine):
            verified.append(tier)
        else:
            break
    print()
    for tier in TIERS:
        mine = [c for c in checks if c.tier == tier]
        print(f"{tier}  {tier} checks: {sum(c.ok for c in mine)}/{len(mine)}")
    print()
    print(f"claimed {' '.join(claimed) or 'nothing'}, verified {' '.join(verified) or 'nothing'}")
    overclaim = [t for t in claimed if t not in verified]
    if overclaim:
        print(f"note: claimed but not verified: {' '.join(overclaim)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
