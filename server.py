"""LENSA hazard map server (DBHousing hackathon).

Run:   python server.py          walker app  -> http://localhost:8000/
                                 admin board -> http://localhost:8000/admin.html
                                 agency page -> the link inside each agency email
       python server.py --demo   same, but a "day" lasts 1 minute, so fix-by deadlines and
                                 overdue reminders can be shown on stage
Test:  python server.py --test
Stdlib only (http.server + sqlite3), nothing to pip install.
"""
import json
import math
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
DB_PATH = HERE / "hazards.db"   # delete this file to reset the demo
PORT = 8000
# ponytail: same type within this radius = same hazard. Phone GPS is ~5-10 m off per report,
# so two reports of one drain can land ~20 m apart. Too big and two real drains merge.
MATCH_M = 20
DAY_S = 60 if "--demo" in sys.argv else 86400
URGENT_DANGER = 9                       # danger >= this is URGENT
URGENT_FIX_DAYS, NORMAL_FIX_DAYS = 2, 7  # fix-by deadline after the agency is emailed
BASE_URL = f"http://localhost:{PORT}"   # replaced by the LAN address at startup (agency links)

# Real agencies for Seri Iskandar (UTP). For the hackathon every email goes to the inbox typed on the
# admin board, not to them. ponytail: keyword routing; a real rollout needs a per-township table.
AGENCIES = {
    "MDPT": ["Majlis Daerah Perak Tengah", "drains, walkways, pavements"],
    "JKR": ["JKR Daerah Perak Tengah", "roads, potholes"],
    "IWK": ["Indah Water Konsortium", "sewer manholes"],
}


def suggest_agency(type_):
    t = type_.lower()
    if "manhole" in t:
        return "IWK"
    if "pothole" in t or "road" in t:
        return "JKR"
    return "MDPT"


SCHEMA = """
create table if not exists hazards(
    id integer primary key, type text not null, danger integer not null,
    lat real not null, lon real not null, status text not null,
    reports integer not null, first_by text not null,
    created real not null, updated real not null);
create table if not exists events(
    hazard_id integer not null, t real not null, kind text not null, by text not null, text text not null);
"""
# Added after the first demo build; ALTER keeps hazards already logged by the old server.
NEW_COLUMNS = {"agency": "text", "key": "text", "sent_at": "real", "due_at": "real",
               "fixed_at": "real", "reminders": "integer not null default 0", "note": "text"}
# unconfirmed -> confirmed -> sent -> in_progress -> fixed, or rejected by the admin


def connect(path=DB_PATH):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    have = {r["name"] for r in db.execute("pragma table_info(hazards)")}
    for col, decl in NEW_COLUMNS.items():
        if col not in have:
            db.execute(f"alter table hazards add column {col} {decl}")
    # the old server's "send" set no agency, deadline or link: put those back in the "to send" pile
    db.execute("update hazards set status='confirmed' where status in ('sent', 'in_progress') and key is null")
    db.commit()
    return db


def metres(lat1, lon1, lat2, lon2):
    """Haversine distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def log(db, hid, kind, by, text):
    db.execute("insert into events values(?, ?, ?, ?, ?)", (hid, time.time(), kind, by, text))


def get(db, hid):
    h = db.execute("select * from hazards where id=?", (hid,)).fetchone()
    if h is None:
        raise ValueError(f"no case #{hid}")
    return h


def public(db, h, me=None):
    """A case as the apps see it: timeline added, the agency's secret key removed."""
    h = dict(h)
    h.pop("key")
    ev = db.execute("select t, kind, by, text from events where hazard_id=? order by t", (h["id"],)).fetchall()
    h["events"] = [{"t": e["t"], "text": e["text"]} for e in ev]
    h["mine"] = me is not None and (h["first_by"] == me or any(e["kind"] == "report" and e["by"] == me for e in ev))
    h["urgent"] = h["danger"] >= URGENT_DANGER
    h["overdue"] = h["status"] in ("sent", "in_progress") and time.time() > (h["due_at"] or 0)
    h["suggested"] = suggest_agency(h["type"])
    h["agency_name"] = AGENCIES[h["agency"]][0] if h["agency"] else None
    return h


def report(db, type_, danger, lat, lon, by):
    """A walker said "yes, log it". New case, or a confirmation if ANOTHER walker already
    logged the same type nearby. Fixed and rejected cases don't match: a drain that breaks
    again, or a "false alarm" that keeps being reported, becomes a new case."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("bad coordinates")
    now = time.time()
    open_ = db.execute("select * from hazards where type=? and status not in ('fixed', 'rejected')",
                       (type_,)).fetchall()
    near = min(open_, key=lambda h: metres(lat, lon, h["lat"], h["lon"]), default=None)
    if near and metres(lat, lon, near["lat"], near["lon"]) <= MATCH_M:
        hid = near["id"]
        confirm = near["status"] == "unconfirmed" and by != near["first_by"]   # not by yourself
        db.execute("update hazards set status=?, reports=reports+1, updated=? where id=?",
                   ("confirmed" if confirm else near["status"], now, hid))
        log(db, hid, "report", by, f"Confirmed by a second walker ({by})" if confirm else f"Reported again by {by}")
    else:
        hid = db.execute(
            "insert into hazards(type, danger, lat, lon, status, reports, first_by, created, updated)"
            " values(?, ?, ?, ?, 'unconfirmed', 1, ?, ?, ?)",
            (type_, danger, lat, lon, by, now, now)).lastrowid
        log(db, hid, "report", by, f"First reported by {by}")
    db.commit()
    return get(db, hid)


def email(h, reminder=False):
    """The draft the admin sends to the agency (they press Send in Gmail / their mail app)."""
    name, role = AGENCIES[h["agency"] or suggest_agency(h["type"])]
    urgent = h["danger"] >= URGENT_DANGER
    stamp = lambda t: time.strftime("%d %b %Y, %H:%M", time.localtime(t)) if t else "-"
    late = h["due_at"] and time.time() > h["due_at"]
    subject = (f"{'REMINDER: ' if reminder else ''}[LENSA #{h['id']}] {'URGENT ' if urgent else ''}"
               f"walkway hazard: {h['type']} - fix by {stamp(h['due_at'])}")
    intro = (f"Reminder #{h['reminders']}: this case is still open"
             f"{' and past its fix-by date' if late else ''}." if reminder else
             "A walkway hazard was logged by LENSA users (an assistive navigation aid for "
             "visually impaired pedestrians).")
    body = "\n".join([
        f"To: {name} ({role})", "", intro, "",
        f"Case:        #{h['id']}",
        f"Hazard:      {h['type']}",
        f"Danger:      {h['danger']}/10{' (URGENT)' if urgent else ''}",
        f"Reports:     {h['reports']} walker report(s)",
        f"First seen:  {stamp(h['created'])}",
        f"Location:    {h['lat']:.6f}, {h['lon']:.6f}",
        f"Map:         https://maps.google.com/?q={h['lat']:.6f},{h['lon']:.6f}",
        f"Fix by:      {stamp(h['due_at'])}", "",
        "Please update the case when work starts and when it is fixed (no login needed):",
        f"{BASE_URL}/agency.html?case={h['id']}&key={h['key']}", "",
        "Thank you,", "Township Management - LENSA Hazard Map",
    ])
    return {"agency": name, "subject": subject, "body": body}


def act(db, hid, action, agency=None):
    """Admin board buttons. Returns (case, email draft or None)."""
    h = get(db, hid)
    s, now = h["status"], time.time()

    def move(new, text, **cols):
        sets = ", ".join(f"{c}=?" for c in ("status", "updated", *cols))
        db.execute(f"update hazards set {sets} where id=?", (new, now, *cols.values(), hid))
        log(db, hid, action, "admin", text)

    if action == "approve" and s == "unconfirmed":
        move("confirmed", "Approved by admin without a second walker")
    elif action == "reject" and s in ("unconfirmed", "confirmed"):
        move("rejected", "Rejected by admin (false alarm or duplicate)")
    elif action == "send" and s == "confirmed":
        agency = agency if agency in AGENCIES else suggest_agency(h["type"])
        days = URGENT_FIX_DAYS if h["danger"] >= URGENT_DANGER else NORMAL_FIX_DAYS
        move("sent", f"Emailed to {AGENCIES[agency][0]}", agency=agency, key=h["key"] or secrets.token_urlsafe(8),
             sent_at=now, due_at=now + days * DAY_S)
    elif action == "send" and s in ("sent", "in_progress"):
        pass   # draft the same email again; nothing changes
    elif action == "remind" and s in ("sent", "in_progress"):
        db.execute("update hazards set reminders=reminders+1, updated=? where id=?", (now, hid))
        log(db, hid, action, "admin", f"Reminder #{h['reminders'] + 1} sent to the agency")
    elif action == "fixed" and s in ("sent", "in_progress"):
        move("fixed", "Marked fixed by admin", fixed_at=now)
    else:
        raise ValueError(f"can't {action} a case that is {s}")
    db.commit()
    h = get(db, hid)
    return h, (email(h, reminder=action == "remind") if action in ("send", "remind") else None)


def agency_case(db, hid, key):
    h = get(db, hid)
    if not h["key"] or not secrets.compare_digest(h["key"], str(key)):
        raise PermissionError("this link is not valid for this case")
    return h


def agency_update(db, hid, key, status, note):
    """The agency's link: 'work started', then 'fixed'."""
    h = agency_case(db, hid, key)
    allowed = {"in_progress": ("sent",), "fixed": ("sent", "in_progress")}
    if h["status"] not in allowed.get(status, ()):
        raise ValueError(f"case #{hid} is already {h['status']}")
    now = time.time()
    name = AGENCIES[h["agency"]][0]
    text = f"{name}: {'work started' if status == 'in_progress' else 'fixed'}" + (f" - {note}" if note else "")
    db.execute("update hazards set status=?, updated=?, note=?, fixed_at=? where id=?",
               (status, now, note or h["note"], now if status == "fixed" else None, hid))
    log(db, hid, status, "agency", text)
    db.commit()
    return get(db, hid)


db = None
lock = threading.Lock()   # ponytail: one lock around sqlite; fine for a handful of phones
# LENSA -> phone: "a fixed hazard was just passed". In memory only: a restart forgets
# un-answered prompts, which is fine because the prompt is only valid for 5 s anyway.
detected = []


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE / "web"), **kw)

    def log_message(self, fmt, *args):   # API polls every second would flood the console
        if not getattr(self, "path", "").startswith("/api/"):   # a phone trying https:// arrives with no path
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def api(self, fn):
        try:
            with lock:
                out = fn()
            self.send_json(out)
        except PermissionError as e:
            self.send_json({"error": str(e)}, 403)
        except (KeyError, ValueError, TypeError) as e:   # bad JSON is a ValueError too
            self.send_json({"error": str(e)}, 400)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        agency = re.fullmatch(r"/api/agency/(\d+)", url.path)
        if url.path == "/api/hazards":
            def fn():
                rows = db.execute("select * from hazards order by updated desc").fetchall()
                return [public(db, h, q.get("me")) for h in rows]
            return self.api(fn)
        if url.path == "/api/agencies":
            return self.api(lambda: AGENCIES)
        if url.path == "/api/detected":
            def fn():
                after = max(0, int(q.get("after", 0)))
                return {"next": len(detected), "events": detected[after:]}
            return self.api(fn)
        if agency:
            return self.api(lambda: public(db, agency_case(db, int(agency[1]), q.get("key", ""))))
        if url.path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = lambda: json.loads(raw or b"{}")
        admin = re.fullmatch(r"/api/hazards/(\d+)/(approve|reject|send|remind|fixed)", path)
        agency = re.fullmatch(r"/api/agency/(\d+)", path)
        if path == "/api/report":
            def fn():
                d = body()
                h = report(db, str(d["type"])[:60], int(d.get("danger", 5)),
                           float(d["lat"]), float(d["lon"]), str(d.get("by", "anon"))[:40])
                print(f"  report by {d.get('by')}: #{h['id']} {h['type']} -> {h['status']}")
                return public(db, h, d.get("by"))
            return self.api(fn)
        if path == "/api/detected":
            def fn():
                d = body()
                ev = {"type": str(d["type"])[:60], "danger": int(d.get("danger", 5)), "t": time.time()}
                detected.append(ev)
                print(f"  LENSA passed: {ev['type']} (danger {ev['danger']})")
                return ev
            return self.api(fn)
        if admin:   # ponytail: the admin board has no login; add one before this leaves the LAN
            def fn():
                h, mail = act(db, int(admin[1]), admin[2], body().get("agency"))
                print(f"  admin {admin[2]} #{h['id']} -> {h['status']}")
                return {"hazard": public(db, h), "email": mail}
            return self.api(fn)
        if agency:
            def fn():
                d = body()
                h = agency_update(db, int(agency[1]), d.get("key", ""), d.get("status"),
                                  str(d.get("note", "")).strip()[:300])
                print(f"  agency #{h['id']} -> {h['status']}")
                return public(db, h)
            return self.api(fn)
        self.send_json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second copy bind the same port and silently answer instead
    # of this one (an old server kept serving old code). Without it, a 2nd copy fails loudly.
    allow_reuse_address = sys.platform != "win32"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # UDP connect sends nothing; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def self_test():
    t = connect(":memory:")
    # walkers: report -> confirm
    a = report(t, "open drain", 9, 4.38500, 100.97900, "A")
    assert a["status"] == "unconfirmed"
    assert report(t, "open drain", 9, 4.38500, 100.97900, "A")["status"] == "unconfirmed"  # not by yourself
    b = report(t, "open drain", 9, 4.38505, 100.97905, "B")    # ~8 m away, second walker
    assert b["id"] == a["id"] and b["status"] == "confirmed" and b["reports"] == 3
    assert report(t, "pothole", 7, 4.38500, 100.97900, "B")["id"] != a["id"]       # other type, new case
    assert report(t, "open drain", 9, 4.38600, 100.97900, "B")["id"] != a["id"]    # ~110 m, new case
    try:
        report(t, "pothole", 7, 999, 0, "A")
        raise AssertionError("bad coordinates accepted")
    except ValueError:
        pass
    assert public(t, a, "A")["mine"] and not public(t, a, "C")["mine"] and "key" not in public(t, a)
    assert [suggest_agency(x) for x in ("open manhole", "pothole", "open drain")] == ["IWK", "JKR", "MDPT"]

    # admin: approve an urgent 1st report, email it, agency closes it with the link
    m = report(t, "open manhole", 10, 4.39000, 100.98000, "A")
    for bad in ("send", "fixed", "remind"):
        try:
            act(t, m["id"], bad)
            raise AssertionError(f"{bad} allowed on an unconfirmed case")
        except ValueError:
            pass
    assert act(t, m["id"], "approve")[0]["status"] == "confirmed"
    m, mail = act(t, m["id"], "send")
    assert m["status"] == "sent" and m["agency"] == "IWK" and m["key"] in mail["body"]
    assert abs(m["due_at"] - m["sent_at"] - URGENT_FIX_DAYS * DAY_S) < 1
    assert not public(t, m)["overdue"]
    t.execute("update hazards set due_at=0 where id=?", (m["id"],))
    assert public(t, get(t, m["id"]))["overdue"]
    assert "REMINDER" in act(t, m["id"], "remind")[1]["subject"]
    try:
        agency_update(t, m["id"], "wrong-key", "fixed", "")
        raise AssertionError("wrong key accepted")
    except PermissionError:
        pass
    assert agency_update(t, m["id"], m["key"], "in_progress", "crew on site")["status"] == "in_progress"
    done = agency_update(t, m["id"], m["key"], "fixed", "new cover installed")
    assert done["status"] == "fixed" and done["fixed_at"] and done["note"] == "new cover installed"
    try:
        agency_update(t, m["id"], m["key"], "fixed", "")
        raise AssertionError("fixed twice")
    except ValueError:
        pass
    assert report(t, "open manhole", 10, 4.39000, 100.98000, "B")["id"] != m["id"]   # broke again after fix

    # admin: reject -> a later report at that spot is a new case
    w = report(t, "blocked walkway", 8, 4.39500, 100.98500, "A")
    assert act(t, w["id"], "reject")[0]["status"] == "rejected"
    assert report(t, "blocked walkway", 8, 4.39500, 100.98500, "B")["id"] != w["id"]
    print("self-test OK")


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
        sys.exit()
    sys.stdout.reconfigure(line_buffering=True)   # show prints live in the console / preview log
    try:
        server = Server(("0.0.0.0", PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} is already in use: another copy of this server is running. Close it first.")
    db = connect()
    BASE_URL = f"http://{lan_ip()}:{PORT}"
    print(f"LENSA hazard map on port {PORT}{'  [DEMO: 1 day = 1 minute]' if DAY_S == 60 else ''}")
    print(f"  walker app : http://localhost:{PORT}/   (phone: {BASE_URL}/ - see README for GPS/mic)")
    print(f"  admin board: http://localhost:{PORT}/admin.html")
    server.serve_forever()
