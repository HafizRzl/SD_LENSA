# LENSA Hazard Map (DBHousing hackathon)

## Repo layout

```
server.py, web/   NEW for the hackathon: crowd-sourced walkway hazard map + township admin + agency page
lensa/            LENSA itself (wearable assistive vision), a snapshot of the FYP repo
                  (HafizRzl/-FYP-LENSA, branch pacman-nav, commit 667681f), so hackathon changes
                  never touch the FYP repo
```

`lensa/` keeps its own `.gitignore`, so models, the Gemini key and voice cache are not committed. They are
already copied locally, **except the key**: copy `claude/lensa/desktop-beta/secrets.yaml` from the FYP folder
into `lensa/desktop-beta/` yourself (or fill in `secrets.example.yaml`). On a fresh clone, put the four model
files from the FYP `desktop-beta/models/` into `lensa/desktop-beta/models/`. See `lensa/README.md` to run LENSA.

A walker passes a fixed hazard → LENSA asks *"Dangerous hazard found: open drain. Do you want to log it?"* →
"yes" within 5 s logs a pin at the phone's GPS → a **second** walker logging the same type within 20 m
**confirms** it → the township admin emails the right agency → the agency marks it **work started**, then **fixed**
from a link in the email → walkers near the spot hear it was repaired, and the reporter gets a thank-you.

```
server.py         Python server, stdlib only (no pip install). SQLite file hazards.db.
web/index.html    Walker app (phone): map, voice prompt, yes/no, warnings, "repaired" news
web/admin.html    Township admin board (laptop/projector)
web/agency.html   Agency page, opened from the link in the email
```

## Run

```
python D:\hackathon\server.py           normal: fix-by deadlines in real days
python D:\hackathon\server.py --demo    stage: 1 day = 1 minute, so a case goes OVERDUE in 2-7 minutes
```

- Walker app: http://localhost:8000/ (phone: the `http://<laptop-ip>:8000/` address it prints)
- Admin board: http://localhost:8000/admin.html (open it through this address, not by double-clicking the file)
- Reset the demo: stop the server and delete `hazards.db`
- Self-test: `python server.py --test`
- Only one copy may run. A second copy now stops with "Port 8000 is already in use".

First run on Windows: allow Python through the firewall on **private** networks, or the phone can't connect.

## Case lifecycle (admin board)

| Status | Who moves it | Buttons |
|---|---|---|
| 1st report (orange) | 2nd walker says yes → confirmed | **Approve now** (urgent, skip waiting), **Reject** (false alarm) |
| Confirmed (red) | admin | agency dropdown (auto-picked) + **Email agency**, **Reject** |
| With agency (purple) | agency link / admin | **Email again**, **Mark fixed (admin)**; **Send reminder** once OVERDUE |
| Work started (teal) | agency link | same as above |
| Fixed (green) | agency link / admin | shows time from first report to fix |

- Agency routing: manhole → **IWK**, pothole/road → **JKR Perak Tengah**, everything else → **MDPT** (drains,
  walkways, pavements). Change it with the dropdown.
- Fix-by deadline starts when emailed: **2 days** if danger ≥ 9 (URGENT), else **7 days**.
- **Email agency** opens a ready-made draft: **Open in Gmail**, **Open in mail app** or **Copy text**. Type your own
  inbox in *To* once (it's remembered). For the demo all "agency" emails go there.
- The email contains the agency link `http://<laptop-ip>:8000/agency.html?case=N&key=...`. It only opens on a
  device on the same Wi-Fi as the laptop. The key stops other people from closing cases.
- Click a case to see its full history (reported, confirmed, emailed, reminders, agency notes).

## Phone setup (do this before the demo)

The phone only allows GPS and the microphone on HTTPS pages, and the laptop serves plain HTTP.
In **Chrome on the phone**:

1. Put the phone and laptop on the same Wi-Fi/hotspot.
2. Open `chrome://flags`, search **"Insecure origins treated as secure"**.
3. Type `http://<laptop-ip>:8000` (the address the server prints), set it to **Enabled**, then tap **Relaunch**.
4. Open that address, tap **Start walk**, allow location and microphone.

## How LENSA triggers the prompt

LENSA (or anything else) tells the phone a fixed hazard was just passed:

```
POST /api/detected   {"type": "open drain", "danger": 9}
```

From Python (e.g. desktop-beta): `requests.post("http://localhost:8000/api/detected", json={"type": "open drain", "danger": 9})`.
LENSA is not wired to this yet; until it is, use **Test / demo backup → Simulate LENSA passing it**.

## Demo script (one person, ~4 minutes, run with `--demo`)

1. Phone `http://<ip>:8000/` (walker A): Simulate → say/tap **yes** → orange pin.
2. Phone `http://<ip>:8000/?user=B` (walker B): walk toward it → "Caution… about 25 metres". Simulate → **yes** → red (confirmed).
3. Admin board: IWK/JKR/MDPT already picked → **Email agency** → **Open in Gmail** → send to your inbox.
4. Phone: open the email, tap the link → **Work started** (note "crew on site").
   Optional: wait 2 min → the case turns **OVERDUE** on the board → **Send reminder**.
5. Agency page: **Mark as fixed** (note "cover replaced") → board shows green + time to fix.
6. Phone as walker A (`?user=` blank again): "Thank you. The open drain you reported has been repaired."
   Walker B near the spot: "Good news… has been repaired."

Indoors with no GPS: tick **Fake GPS** and tap the map to place yourself.
No mic (Claude's in-app browser blocks it, or a noisy hall): tap the **Yes / No** buttons during the 5 s window, or press **Y / N**.

## API

```
GET  /api/hazards[?me=<walker>]           all cases with history; "mine" = this walker reported it
GET  /api/agencies
POST /api/report   {type, danger, lat, lon, by}
POST /api/detected {type, danger}         LENSA -> phone prompt
POST /api/hazards/<id>/approve|reject|send|remind|fixed   {agency}   (admin; send/remind return the email draft)
GET  /api/agency/<id>?key=...             agency page
POST /api/agency/<id>  {key, status: in_progress|fixed, note}
```
