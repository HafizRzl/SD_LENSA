# FORHAFIZ — LENSA, Explained From the Ground Up

> Your personal briefing for the Final Year Presentation. Everything the project is, from
> "what is a neural network" up to "why the pacman map weaves left at 1.0 m" — with the real
> numbers, the real formulas, and the honest limitations an examiner will probe.
> Updated **2026-10-18**, on branch `pacman-nav`. October changes are woven into each section;
> §15 lists them in one place (handy for a "what changed since the proposal defence" slide).

---

## Contents

1. [What LENSA is](#1-what-lensa-is)
2. [The journey of one camera frame](#2-the-journey-of-one-camera-frame)
3. [Foundations — the AI, actually explained](#3-foundations--the-ai-actually-explained)
4. [Seeing distance](#4-seeing-distance)
5. [Deciding what to say — the rules engine](#5-deciding-what-to-say--the-rules-engine)
6. [The voice — neural TTS and the cached-clip trick](#6-the-voice--neural-tts-and-the-cached-clip-trick)
7. [The pacman map — deep dive](#7-the-pacman-map--deep-dive)
8. [The hardware — the wearable "Wireless Eye"](#8-the-hardware--the-wearable-wireless-eye)
9. [The full product architecture](#9-the-full-product-architecture)
10. [Decisions log — why X and not Y](#10-decisions-log--why-x-and-not-y)
11. [Repo map](#11-repo-map)
12. [Demo cheat sheet](#12-demo-cheat-sheet)
13. [Likely examiner questions](#13-likely-examiner-questions)
14. [Glossary](#14-glossary)
15. [What changed since the proposal defence (Oct 2026)](#15-what-changed-since-the-proposal-defence-oct-2026)

---

## 1. What LENSA is

**LENSA is a wearable assistive-vision system for visually impaired Malaysians.** A head-mounted
camera + depth sensor stream to a smartphone; the phone detects obstacles, estimates how far away
they are, decides which one matters most, and **speaks one short Bahasa Melayu instruction**
("orang di kiri, 2 meter, elak ke kanan"). Everything runs **on-device, offline** — no cloud, no
subscription — targeting a fraction of the RM 2,900–21,000 that commercial aids cost.

The whole system is three verbs (the "3C"s):

- **Capture** — head unit sees the scene (camera) and measures distance (ToF sensor).
- **Compute** — the phone runs object detection + distance fusion + navigation logic.
- **Convey** — one prioritised voice line, in Malay, fast enough to act on.

**Current status (honest):**

| Piece | Status |
|---|---|
| Desktop beta (this repo, `desktop-beta/`) | **Working** — webcam → YOLO26n → rules → Malay voice → pacman map |
| Content/config layer (`assets/context/`) | **Working** — plain YAML, hot-reloaded, shared spec for the phone app |
| Native Android app | **Fully planned** (paste-ready code, phases 1–5 in `docs/superpowers/plans/`) — not yet built |
| ESP32-S3 head unit | **Designed + firmware written in the plan** — hardware not yet assembled |

The desktop beta is not a toy: it runs the *same* decision logic and reads the *same* config
files the phone app will, so every behaviour you tune now carries over.

---

## 2. The journey of one camera frame

This is the master diagram. Every section below zooms into one box.

```
 webcam frame (BGR image)
      │
      ▼
 ┌───────────────┐   TWO YOLO26n CNNs: stock COCO model + our road-hazard model.
 │ OBJECT        │   One pass each → detections (class, confidence, box x1,y1,x2,y2),
 │ DETECTION §3  │   each with a ByteTrack ID that stays the same across frames.
 └──────┬────────┘   Overlapping duplicate boxes are merged.
        ▼
 ┌───────────────┐   Per tracked object:
 │ POSITION +    │   • side: left|center|right, with a 4% dead-band so it can't flicker
 │ DISTANCE  §4  │   • distance: k / box_height (laptop), smoothed; none for flat hazards
 └──────┬────────┘               — replaced by real ToF depth on the wearable
        ▼
 ┌───────────────┐   Deterministic 3-layer logic (NO neural network here — on purpose):
 │ RULES         │   L1 interpret: whitelist + confidence filter → a scene
 │ ENGINE    §5  │   L2 decide:    danger scoring → ONE action (stop/steer/caution/blocked/clear)
 └──────┬────────┘   L3 language:  fill a Malay template → one short line
        ▼
 ┌───────────────┐   Speech gate: minimum gap, repeat interval, urgency interrupt —
 │ VOICE     §6  │   then two pre-rendered Husein clips joined into one sentence
 └──────┬────────┘   (neural TTS, rendered offline), or the OS voice on a cache miss.
        ▼
   phone/laptop speaker

 (in parallel, every 4 s)
 ┌───────────────┐   raw boxes + distances → top-down occupancy grid → BFS route to the
 │ PACMAN MAP §7 │   deepest safe cell → map window, AND the steering fallback when
 └───────────────┘   all three zones are blocked
```

---

## 3. Foundations — the AI, actually explained

### 3.1 A neural network in sixty seconds

A neural network is a function with millions of adjustable numbers (**weights**). Input goes in
(pixels), gets multiplied by weights, summed, passed through simple non-linear functions, layer
after layer, until an answer comes out (e.g. "83% person"). **Training** = showing it millions of
labelled examples and nudging the weights (gradient descent / backpropagation) until its answers
match the labels. **Inference** = using the trained weights — that's all LENSA ever does on
device; we never train on the phone.

### 3.2 CNN vs LSTM — and what YOLO actually is

Your direct question: **YOLO is a CNN, not an LSTM.** The two solve different problems:

| | **CNN** (Convolutional NN) | **LSTM** (Long Short-Term Memory) |
|---|---|---|
| Built for | **Spatial** data — images | **Sequential** data — text, audio, time series |
| Core trick | Slides small filters (e.g. 3×3) across the image; each filter learns to detect a local pattern | Processes inputs one step at a time, carrying a memory cell forward |
| Hierarchy | Early layers see edges → mid layers see textures/parts → deep layers see whole objects | Early steps influence later steps through the memory |
| Why it fits | An object looks the same wherever it appears — filter **weight sharing** exploits that | Word #7 depends on words #1–6 |

An image has no "time axis," so an LSTM brings nothing to single-frame detection. YOLO is a deep
stack of convolutions: the image goes in once, and out comes a grid of predictions.

**"You Only Look Once"** is the actual meaning of the name, and it is the key design idea.
Older detectors (R-CNN family) are **two-stage**: first propose ~2,000 candidate regions, then run
a classifier on each — accurate but slow. YOLO is **one-stage**: a single CNN pass predicts *all*
boxes and classes simultaneously, which is why it reaches real-time speeds. That trade
(speed ≥ small accuracy loss) is exactly right for a navigation aid, where a fast "car ahead" beats
a late, slightly-more-precise one.

### 3.3 YOLO26n specifically

- **YOLO26** is the current Ultralytics generation (2025), designed for edge devices: anchor-free,
  end-to-end (no separate non-max-suppression step needed at deployment), and heavily
  CPU-optimised.
- **n = nano**, the smallest variant: our weights file `desktop-beta/models/yolo26n.pt` is
  **~5.3 MB**. That small size is what makes phone deployment realistic.
- Shipped **pre-trained on COCO** by Ultralytics — *we did not train it* (see 3.6).
- In the beta we call it through the `ultralytics` Python package on torch; on the phone
  (Phase 2 plan) the same network runs as an **INT8 .tflite** on the NNAPI/GPU delegate.

### 3.4 Bounding boxes, confidence, and cleaning up detections

For each object the network outputs:

- a **bounding box** — pixel corners `(x1, y1, x2, y2)` around the object;
- a **class** — which of the 80 COCO categories;
- a **confidence** — 0..1, "how sure am I this box is real."

Raw detector output is noisy: overlapping duplicate boxes for one object. The classic cleanup is
**NMS (non-max suppression)** — keep the highest-confidence box, delete boxes overlapping it above
an IoU threshold, repeat. (IoU = intersection area ÷ union area of two boxes, a 0..1 overlap
measure.) YOLO26 does this end-to-end internally; our Phase-2 Kotlin plan still carries a small
tested NMS for exports that need it. LENSA additionally applies a **confidence floor of 0.45**
(`hazard_priority_rules.yaml`) — for a safety device, a hallucinated obstacle is noise, but we bias
so that anything *plausible* is still considered.

### 3.5 Quantization — why INT8 matters on a phone

Networks are trained in 32-bit floating point. **Quantization** converts weights (and math) to
8-bit integers: **4× smaller file, 2–4× faster**, and phone NPUs are built to chew INT8 — at a
typically ~1% accuracy cost. That is why the phone plan says "YOLO26n **INT8**." Same idea, milder,
in our TTS fix: loading the voice model in **bf16** (16-bit) halved its RAM (2.4 GB → 1.2 GB).

### 3.6 The COCO dataset, our own pothole model, and the honest gap that remains

COCO ("Common Objects in Context", ~330k images) defines the **80 classes** the pretrained
YOLO26n can see: people, vehicles, chairs, benches… but **no ground hazards** — the things blind
pedestrians fear most (potholes, drains, curbs, stairs).

So we **trained our own detector** (`desktop-beta/train_hazards.py`): YOLO26n fine-tuned from the
COCO weights (transfer learning). v1 used a Kaggle/Roboflow pothole set (classes `broken_road`,
`pothole`; test mAP50 0.73). **v2 adds a street-level manhole set** (open / displaced / broken /
intact covers) into **one 6-class model**: 60 epochs on an RTX 4060 (~3 h), **test mAP50 0.85**
(potholes unchanged at ~0.72; manholes 0.85–0.98). Safety-relevant reading of the confusion
matrix: **no open manhole was ever labelled "intact"** — it always triggers a warning, though
often under the wrong name ("displaced" or "pothole"); the weak spot is that ~27% of displaced
covers are labelled "intact" and therefore stay silent. It runs as a **second model** next to the COCO one, because
fine-tuning one model on potholes alone would make it forget people and cars (*catastrophic
forgetting*). Cost: two inferences per frame (~9.5 → ~4.8 fps on the laptop CPU).

Catalog today: **21 of 32 entries are live**; 11 are still dormant (stairs, curb, drain, pole,
wall, tree, door, crossing, animal, signboard, barrier) and each needs a labelled dataset and a
training run like the pothole/manhole ones — the planned Malaysian-hazard work. Own this
on the limitations slide. (Regenerate anytime: `python desktop-beta/list_classes.py` →
`desktop-beta/detectable_classes.md`.)

**mAP50 in one sentence:** the share of real objects found with a box overlapping the truth by
≥50%, averaged across confidence thresholds and classes — 1.0 would be perfect.

### 3.7 The other models in the system (quick tour)

- **Gemma 3n (E2B)** — a small on-device **LLM** (transformer, ~2B effective parameters) planned
  for **Loop B only**: when the user *asks* ("describe the scene"), recent YOLO labels are turned
  into natural Malay sentences. It is deliberately **banned from the safety loop** (§5.1).
- **ML Kit Text Recognition v2** — Google's on-device OCR, for reading signs on demand
  ("Papan tanda: AWAS LANTAI LICIN").
- **Malaysian-TTS-0.6B** — the neural Malay voice (§6).
- **Whisper (STT)** — was in an early architecture draft; **descoped** with the supervisor to
  protect the safety loop (voice *output* is the product; voice *input* is a nice-to-have).

---

## 4. Seeing distance

Detection tells you *what* and *where in the image* — navigation needs *how far*.

### 4.1 Desktop beta: monocular estimation (and its known flaw)

One camera cannot measure depth directly. The beta uses the oldest trick in optics: **apparent
size shrinks with distance**. For a pinhole camera, an object of real height H at distance d
projects to an image height proportional to H/d. Invert it:

```
distance ≈ k / box_height_normalized        (estimate_distance_m in lensa_beta.py)
k = 1.2 (calibration constant), clamped to 0.5–10 m
```

A person filling 60% of the frame height → 1.2/0.6 = **2.0 m**. Filling 20% → **6.0 m**.

**The flaw, stated plainly:** the formula silently assumes every object has a similar real-world
height. A child and an adult at the same distance produce different box heights → different
estimates. A car (wide, low) breaks the assumption differently than a pole (narrow, tall). That is
why every drawn distance is labelled "**est**" and why the real product replaces this entirely
with a physical depth sensor.

**Flat ground hazards break it completely**: a pothole's box is short because it lies flat, not
because it is far. So objects marked `ground: true` in the catalog get **no** distance and are
announced without metres ("Hati-hati, lubang jalan di kiri"). The proper fix is ground-plane
geometry from the camera's height and tilt — future work once the head unit is mounted.

The beta also **smooths** each tracked object's distance and holds the *spoken* metres until the
estimate moves >0.65 m, so 2.49 ↔ 2.51 m can't flip "dua meter" ↔ "tiga meter" every frame.

### 4.2 The wearable: VL53L8CX Time-of-Flight, 8×8 zones

**ToF principle:** the sensor emits infrared light pulses and measures the time for photons to
bounce back. Light travels ~0.3 m per nanosecond, so time → distance, physically measured, no
assumptions about object size. The VL53L8CX (it replaced a lost VL53L5CX — same grid, same
wiring) returns not one number but an **8×8 grid = 64 zones**
(typical range up to ~4 m), i.e. a coarse depth image.

**Fusion (Phase 3 plan):** each YOLO box knows its position in the image; the image maps onto the
8×8 grid; so each detection reads distance from *its own zone(s)*:

```
box centre (normalized cx, cy)  →  column = ⌊cx·8⌋, row = ⌊cy·8⌋  →  distance_mm[row][col]
```

That gives **per-object measured distance** — person at 2.1 m *and* car at 3.4 m simultaneously —
which the monocular trick can only approximate. The fusion code + unit test already exist in the
Phase-3 plan (`DepthGrid.distanceMAt`).

---

## 5. Deciding what to say — the rules engine

File: `desktop-beta/lensa_rules.py` (a faithful port of the app design). This is the safety brain,
and it is **deterministic — zero neural networks**. Same inputs → same output, every time,
microseconds, testable, and physically incapable of hallucinating a hazard.

### 5.1 Why NOT an LLM in the safety loop

This was a deliberate architecture decision (the "two-loop" design):

1. **Latency** — a walking user covers ~1.4 m/s; an LLM taking 1–3 s per frame answers about the
   world that no longer exists. Templates answer in microseconds.
2. **Hallucination** — an LLM *can* invent objects or distances; a string template *cannot*.
3. **Battery/thermal** — per-frame LLM inference would cook the phone and starve YOLO.

So: **Loop A** (continuous, safety) = deterministic rules below. **Loop B** (on-demand,
descriptive) = Gemma. Loop B is never allowed to delay Loop A.

### 5.2 The three layers

**Layer 1 — interpret.** Drop detections below the 0.45 confidence floor and any class not in
the catalog (the catalog is a **whitelist** — removed classes are seen but never spoken); merge
same-class detections keeping the **nearest** one *for naming*. Which sides are blocked is
computed from **every** detection, not the merged list — merging once hid the second chair.

**Layer 2 — decide.** First the **immediate-stop override**: anything with distance ≤ **1.0 m**
forces `STOP` at high urgency — no scoring, no exceptions (this mirrors the master safety rule:
when in doubt, stop). Otherwise every scene item gets a danger score:

```
score = danger_weight × proximity × moving_multiplier × sensitivity

danger_weight   from object_catalog.yaml (person 4, car 9, stairs 8, bench 3 …)
proximity       (6 − d) / (6 − 1), clamped 0..1   → 1.0 at 1 m, 0 at 6 m; 0.2 if unknown
moving_multiplier 1.5 if approaching, else 1.0
sensitivity     0.8 / 1.0 / 1.25 for low/medium/high user setting
```

**Worked example (why the car wins):** person at 2 m: 4 × 0.8 = **3.2**. Car at 2.5 m:
9 × 0.7 = **6.3**. The car is *farther* but announced first — "closest + most dangerous first"
is a weighted product, not just nearest-wins.

The winner's distance picks the band: ≤3 m → caution (medium urgency); ≤6 m → caution (low);
beyond → say nothing. Then the **steer logic** (rebuilt in October after testing showed it was
unstable):

- **Blocked sides come from box width, not box centre.** A box blocks a third of the image once
  it covers ≥25% of it, so a wide object straddling a line blocks both zones it touches.
- **Hazard dead-centre, both sides open →** step away from the side it *leans* toward
  (`orang di hadapan, 2 meter, elak ke kanan` when it sits left of centre). One side open → that side.
- **Hazard on a side, close (≤3 m), other side open →** steer away from it, naming it:
  `orang di kiri, 2 meter, elak ke kanan`. Centre open but the other side blocked → just announce.
- **Everything blocked →** ask the pacman map (§7) for a gap the coarse thirds miss; if there is
  none and the hazard is close → `Berhenti! Laluan tersekat` (said at most 3× per blockage).
- **Hysteresis:** once it says "kiri", it keeps saying "kiri" until "kanan" wins 3 frames in a row
  — unless the held side becomes blocked, then it switches at once (safety beats stability).

**Layer 3 — language.** The decision indexes a template in `phrase_library_ms.yaml` /
`_en.yaml`; placeholders (`{objek}`, `{arah}`, `{jarak}`, `{elak}`) are filled; high urgency
prepends "Awas!". Editing the YAML changes the voice of the product **without touching code** —
and the beta hot-reloads it in ~2 s.

### 5.3 The speech gate — one line at a time

A navigation aid that never stops talking is unusable. The gate (`SpeechGate`, config in
`audio_output_style.yaml`) enforces: minimum **1.0 s** gap between different lines; an identical
line repeats only every **4 s** (normal) or **2 s** (high urgency); optionally stay silent when the
scene hasn't changed; and a critical hazard **interrupts** current speech. The on-screen HUD
(top-right) shows SPEAKING/SPOKE/idle and the "next in" countdown so you can *see* the policy work.
"Laluan tersekat" has its own cap: at most **3 times per blockage**, where a blockage is identified
by the tracker IDs of the objects causing it — a new object makes it a new blockage.

---

## 6. The voice — neural TTS and the cached-clip trick

### 6.1 How the Malay neural voice actually works

`mesolitica/Malaysian-TTS-0.6B` is not a classic "record-a-voice-actor" system. It is two neural
stages:

1. **A language model for sound** (629M parameters — we measured it). Exactly like a text LLM
   predicts the next word, this model predicts the next **speech token** — a discrete code
   representing a slice of audio. Prompt: `husein: Berhenti! kereta di hadapan` → it generates
   hundreds of `speech_1234`-style tokens. The speaker name in the prompt selects the voice
   (husein = male, idayu = female).
2. **A neural audio codec (DistilCodec)** decodes those tokens into an actual 24 kHz waveform —
   think of it as a learned "MP3 decoder" whose dictionary was trained on speech.

### 6.2 Why it cannot run live — and the cached-clip design

Token-by-token generation is autoregressive: hundreds of sequential model calls per sentence. On
your CPU that measured **21–41 s per line** — obviously unusable in a safety loop. But LENSA's
safety vocabulary is a **small, finite set** of template outputs. So:

- **Render once, offline** (`render_clips.py`, own process): each phrase → a WAV clip
  (~100 KB) in `voice_cache/husein/`, keyed by an MD5 hash of the text.
- **Two clips per sentence**: `"orang di kiri"` + `"2 meter, elak ke kanan"`. Each half is a
  natural phrase, so a sentence has one join instead of four; leading/trailing silence is trimmed
  and the halves are joined in memory with an 80 ms pause. 76 clips cover every standard
  sentence (objects × 3 sides, plus 1–6 m × kiri/kanan). Numbers are spoken as Malay words —
  "2 meter" mispronounced, "dua meter" was the only version that sounded right in a listening test.
- **Play instantly, forever**: the live app just plays the file — near-zero latency, real Husein.
- **Cache miss**: line speaks immediately in the OS voice, a background *process* renders the
  Husein clip, and every repeat after that is Malay.

This is also the phone story: the app ships the **clips (a few MB)**, never the **model
(~1.2 GB)** — which is how the 150 MB app budget is met. Loop B (dynamic sentences from Gemma)
uses the system TTS or on-demand synthesis, where latency is acceptable.

### 6.3 Three real bugs we fixed (good "engineering challenges" slides)

1. **Voice died after one line.** Windows' speech engine (SAPI5) is a **COM object with thread
   affinity** — it must be created *and* used on the same thread. It was created on the main
   thread and driven from a worker → worked once, then wedged. Fix: one dedicated voice thread
   owns the engine end-to-end.
2. **0xC0000005 crash loading the voice model.** Two causes stacked: torch and numba (via
   librosa) each bundle an **OpenMP runtime** — two copies in one process crash; and the model
   loaded in float32 (~2.4 GB) on a machine with ~2 GB free RAM. Fix: `KMP_DUPLICATE_LIB_OK` +
   single-threaded OpenMP in the renderer process, and **bf16** weights (~1.2 GB).
3. **"I edited the config and nothing changed."** Config was read once at launch, and a stale
   duplicate existed under `build/`. Fix: plain YAML files + **hot-reload** (the beta polls file
   mtimes every 2 s) + deleted the duplicates. (YAML over JSON because JSON cannot hold comments,
   and the tuning files are half comments.)
4. **"Berhenti!" every single frame.** With `speak_when_scene_unchanged: true`, urgent lines
   skipped the repeat timer entirely — 85 announcements in 10 s on a test image. Fix: a repeated
   urgent line always waits `repeat_high_urgency_every_s` (2 s); new urgent lines still cut in.

---

## 7. The pacman map — deep dive

Branch `pacman-nav`, file `desktop-beta/pacman_map.py`. The idea in your own framing: a top-down
view of what a smarter navigator could do — **ghost 1 at 2 m → dodge left around it; ghost 2 at
5 m → weave right after passing.** Since October it is no longer only a picture: when all three
zones are blocked, the rules engine takes the **first turn of the map's route** as the spoken
direction. Cells the camera cannot see count as blocked — unseen is not safe.

### 7.1 From a bounding box to a floor plan — the actual geometry

The question you asked: how does a 2-D image box become a position on a top-down map? Answer:
**pinhole camera trigonometry**, using two numbers we already have per detection.

**Sideways (x):** the camera sees a horizontal field of view (FOV) of ~60°. A box centre at
normalized image position `cx` (0 = left edge, 1 = right edge) sits at an angle off the camera
axis:

```
θ = (cx − 0.5) × FOV                      # cx=0.5 → 0° (dead ahead)
lateral_x = d · tan(θ)                    # d = that object's estimated distance
```

*Worked example:* box centre at cx = 0.75, distance 4 m → θ = (0.25)(60°) = **15°** →
x = 4 × tan 15° ≈ **+1.07 m** (about a metre to your right).

**Forward (y):** simply the distance estimate d itself.

**Width:** at distance d the camera's view spans `2·d·tan(FOV/2)` metres side to side; a box
covering fraction `w_n` of the image width is therefore about

```
width_m = w_n × 2·d·tan(FOV/2)            # clamped to ≥ 0.4 m
```

So every detection collapses to a dot with a width — `(x metres sideways, d metres forward,
w metres wide)` — exactly a mark on a floor plan. Note this uses the **raw box centre**, *not* the
left/centre/right buckets: the map deliberately keeps the precision the spoken system throws away.

### 7.2 The occupancy grid

The floor plan is a grid of **0.5 m cells**: 13 columns (±3 m sideways) × 16 rows (8 m forward),
user at bottom-centre. Each object marks its footprint cells as blocked — **inflated by 0.3 m**
on every side. That inflation is a classic robotics trick ("configuration space"): the user is not
a point, they have shoulders; growing every obstacle by half a body-width means the planner can
treat the user as a point safely.

Every **4 seconds** the grid is rebuilt from the current (tracked, smoothed) detections and
re-planned — one snapshot shared by the map window and the steering fallback, so they never
disagree. (Every frame was tried and was too jumpy for both the route and the voice.)

### 7.3 BFS — what it is and why it's the right tool

**Breadth-First Search** explores the grid in expanding rings from the start cell: first all cells
1 step away, then all cells 2 steps away, … Because it visits in strictly increasing step order,
**the first time it reaches any cell, it has found a shortest path to it** — no weights, no
heuristics, guaranteed.

- Start: the user's cell (bottom-centre). Moves: up/down/left/right (4-connected — hence the
  pacman look). Blocked cells are walls.
- BFS floods the whole reachable area, remembering each cell's parent.
- **Goal = "deepest safe cell ahead"** (your chosen definition): among all reached cells, take the
  one with the greatest forward row (ties → the most central). Backtracking parents from that cell
  reconstructs the route, drawn as yellow pellets.

*Why BFS and not A\* or Dijkstra?* Dijkstra generalises BFS to weighted edges — our grid is
unweighted, so it degenerates to BFS anyway. A\* adds a heuristic to reach a *known* goal faster —
our goal is discovered *by* the search ("whatever is deepest and reachable"), and the grid is only
13×16 = 208 cells; the whole search takes microseconds. BFS is the simplest algorithm that is
provably optimal here. (Say exactly that sentence in the viva.)

### 7.4 The ghost walkthrough (the built-in self-test)

`python desktop-beta/pacman_map.py` encodes your scenario as an executable test:

- Ghost 1: 0.3 m right of centre, **2 m** ahead, 0.8 m wide → blocks the central lanes at rows 3–4.
- Ghost 2: 0.8 m left of centre, **5 m** ahead → blocks left-ish lanes at rows 9–10.
- BFS output: **`weave: left @1.0m -> right @4.0m, clear to 8.0m`** — bend left before the near
  ghost, swing back right before the far one, reach the 8 m row. A wall test confirms a fully
  blocked corridor produces "no safe path / stops at 0.5 m."

That one line demonstrates the core claim: with *nothing more than bounding boxes and distance
estimates*, sequenced avoidance ("this one first, that one after") falls out of a 60-line planner.

### 7.5 Honest limits (know these before the examiner does)

- **Distance quality bounds map quality** — on the laptop it inherits the monocular estimate's
  assumptions (§4.1). On the wearable, ToF fusion fixes the near field.
- **Egocentric snapshot, not SLAM** — the map does not compensate for the user's own motion;
  it is a 4-second "what's in front of me now," not a persistent map. (A world-frame "follow-me"
  map with an IMU is parked as post-FYP future work — `docs/ROADMAP.md`.)
- **FOV is a constant (60°)** — per-camera calibration would tighten lateral placement.
- **Only a fallback** — the route is spoken only when the three zones are all blocked; a route
  up to 4 s old errs towards "Berhenti", never towards an unseen gap. Wider use is gated on walk tests.

---

## 8. The hardware — the wearable "Wireless Eye"

The head unit is deliberately **dumb**. All intelligence lives in the phone.

- **ESP32-S3 N16R8** — a dual-core 240 MHz microcontroller (16 MB flash / **8 MB octal PSRAM**).
  Its job: grab OV5640 camera JPEGs (DVP interface), poll the VL53L8CX 8×8 grid (I²C), timestamp
  and pair them in PSRAM, and stream over **Wi-Fi**. The big PSRAM is what lets it buffer a
  640×480 JPEG *and* 64 depth zones without dropping packets; it can also crop/resize/compress
  before sending ("pre-processing at the edge").
- **Why the ESP32 must not run YOLO** — it is a microcontroller: no NPU, ~KB-scale on-chip SRAM.
  Even ultra-quantized tiny detectors reach ~1–3 FPS at 96–160 px. At walking speed the user takes
  a full step before one frame is processed. A phone NPU runs YOLO26n at **30+ FPS at 640×640**.
  Ten-times-worse perception on a safety device was rejected outright.
- **Why Wi-Fi, not BLE** — BLE 5's practical throughput (~1–2 Mbit/s) cannot carry a video stream;
  MJPEG at VGA needs an order of magnitude more. The ESP32 runs as a **SoftAP** (its own hotspot,
  `192.168.4.1`): phone joins it directly — no router, fully offline, body-area network.
  Endpoints: `:81/stream` (MJPEG) and `/dist` (JSON 64-zone grid). Firmware:
  `script/lensa_head_unit/lensa_head_unit.ino` (outside the repo). Mount designs (glasses mount,
  sports-shield concept) are generated in `hardware/`.

---

## 9. The full product architecture

```
      HEAD UNIT (ESP32-S3 "Wireless Eye")            PHONE (Kotlin — the brain)
 ┌──────────────────────────────────┐        ┌────────────────────────────────────────┐
 │ OV5640 cam ─┐ time-synced JPEG +  │ Wi-Fi  │ WifiTransport (MJPEG + ToF grid)        │
 │ VL53L8CX 8×8┤ 64-zone depth grid  │ SoftAP │      ▼                                  │
 │             └ crop/compress       │═══════▶│ LOOP A (always on): YOLO26n INT8 →      │
 └──────────────────────────────────┘        │  bbox↔ToF fusion → rules → template →   │
                                              │  cached-clip TTS → speaker              │
                                              │ LOOP B (on demand): labels → Gemma 3n │
                                              │  → rich Malay → TTS                     │
                                              │ OCR (ML Kit) — on demand                │
                                              └────────────────────────────────────────┘
```

**Why native Kotlin (not Flutter):** supervisor's review — the phone is an *AI edge device*, not
a UI app. Android's on-device AI stack (LiteRT/TFLite, MediaPipe LLM Inference, ML Kit, CameraX)
is first-class in Kotlin; Flutter reaches it through plugins/platform channels that add friction
exactly where the project is hardest. The app is audio-first (almost no UI to lose). The Flutter
prototype was ported (logic → desktop beta, recipe → phase plans) and removed from the working
tree; it is preserved at git tag `flutter-reference`.

**Build plan** (each phase demoable, scaffolding in `docs/superpowers/plans/` — written in July,
so port the newer behaviour from `desktop-beta/`). **Not started yet:**

| Phase | Delivers |
|---|---|
| 1 | Kotlin skeleton + content layer + mock pipeline + TTS — speaks with no hardware, 7 unit tests |
| 2 | Real YOLO26n on the phone camera (CameraX + LiteRT INT8) |
| 3 | ESP32 firmware + WifiTransport + per-object ToF fusion |
| 4 | Loop B (Gemma via MediaPipe) + ML Kit OCR |
| 5 | Cached-clip TTS on the phone (the design the beta already proves) |

**Safety posture throughout:** deterministic Loop A owns the speaker; Loop B is suppressed first
under battery/thermal pressure; disconnects speak a fallback ("Sambungan terputus…"); unknown
distance defaults to caution; immediate-stop overrides everything.

---

## 10. Decisions log — why X and not Y

| Decision | Why (the one-sentence viva answer) |
|---|---|
| YOLO (one-stage) over R-CNN (two-stage) | Real-time on edge hardware; a fast warning beats a slightly more precise late one. |
| YOLO on the **phone**, not the ESP32 | Microcontroller ≈ 1–3 FPS at tiny resolution vs phone NPU 30+ FPS at 640² — safety demands the latter. |
| Deterministic rules in the safety loop, LLM only on demand | Microsecond latency, zero hallucination, testable; LLM latency and invention risk are unacceptable where a wrong word can cause a fall. |
| Wi-Fi SoftAP over BLE | BLE cannot carry video (~1–2 Mbit/s practical); SoftAP is offline and router-free. |
| ToF 8×8 over stereo/monocular depth | Physically measured per-zone distance, cheap sensor, no size assumptions; monocular stays only as the laptop stand-in. |
| Kotlin native over Flutter | Supervisor review: first-class access to LiteRT/MediaPipe/ML Kit where the project's risk actually lives. |
| Cached TTS clips over live neural TTS | 21–41 s/line on CPU live vs ~0 ms clip playback; ships MBs of WAV instead of GBs of model (150 MB budget). |
| YAML config over JSON | The tuning files are half comments; JSON has no comments. Plus hot-reload for live tuning. |
| Second detector instead of retraining one | Fine-tuning on potholes alone makes a model forget COCO classes (catastrophic forgetting); two models cost 2× inference but keep both. |
| Object tracking (ByteTrack IDs) | Sides and spoken metres are smoothed *per object*, so the voice doesn't flicker; also identifies a "blockage" for the repeat cap. |
| Two-clip sentences over word-by-word clips | Fewer joins = natural prosody; the tail set is tiny (1–6 m × 2 sides). |
| BFS over A*/Dijkstra for the map | Unweighted 208-cell grid: BFS is already optimal and simplest; A*'s heuristic needs a known goal, ours is discovered by the search. |
| Whisper/STT, face recognition, chat, memory — descoped | Protect the safety loop's latency and the FYP's deliverability; explicitly listed as future work. |

---

## 11. Repo map

```
lensa/
├── FORHAFIZ.md                  ← this document
├── assets/context/              ← the EDITABLE behaviour layer (shared spec, hot-reloaded)
│   ├── agents/*.md              ← Gemma prompts (Loop B), prose
│   ├── rules/*.yaml             ← object catalog (danger weights), hazard bands, OCR rules
│   ├── language/*.yaml          ← Malay/English phrase templates, glossary
│   └── config/*.yaml            ← audio style (repeat/interrupt/voice), fallbacks, defaults
├── desktop-beta/                ← the WORKING system (this is what you demo)
│   ├── lensa_beta.py            ← camera loop, YOLO, HUD, voice backends, hot-reload
│   ├── lensa_rules.py           ← the deterministic 3-layer engine + self-test
│   ├── pacman_map.py            ← occupancy grid + BFS + renderer + ghost self-test
│   ├── render_clips.py          ← offline Husein clip renderer (--prewarm)
│   ├── husein_tts.py            ← Malaysian-TTS-0.6B wrapper (CPU-safe: bf16, OpenMP guards)
│   ├── clip_cache.py            ← clip hash/path conventions
│   ├── train_hazards.py         ← trains the road-hazard model (separate CUDA venv on D:)
│   ├── list_classes.py          ← regenerates detectable_classes.md (live vs dormant)
│   ├── models/                  ← yolo26n.pt + road_hazard_yolo26n.pt (gitignored)
│   └── runs/road_hazard/        ← training charts for the viva (gitignored)
├── assets/dataset/              ← training data (gitignored)
├── hardware/                    ← head-unit mount generators + STLs
├── docs/
│   ├── ARCHITECTURE.md          ← canonical two-loop phone architecture
│   ├── HARDWARE.md              ← head-unit BOM + Wireless-Eye role
│   ├── MODEL_NOTES.md           ← which model runs where and why (+ pothole model results)
│   ├── ROADMAP.md               ← phase status + post-FYP future work
│   └── superpowers/plans/       ← paste-ready implementation plans, phases 1–5
```

Git: `master` = stable line; `pacman-nav` = current work (all October changes); tag
`flutter-reference` = the original Flutter prototype, removed from the tree after porting.

---

## 12. Demo cheat sheet

```bash
# one-time: pre-render the 76 Husein clips (~1 min each on CPU, Ctrl+C-safe, resumes)
python desktop-beta/render_clips.py --prewarm

# the demo: webcam + Malay voice + hazard HUD + pacman map window
python desktop-beta/lensa_beta.py

# variations
python desktop-beta/lensa_beta.py --source photo.jpg   # no webcam needed
python desktop-beta/lensa_beta.py --voice os           # instant OS voice
python desktop-beta/lensa_beta.py --lang en            # English output
python desktop-beta/lensa_beta.py --no-map             # hide the map window

# self-tests (run these live if asked "how do you know it's correct?")
python desktop-beta/lensa_rules.py     # steering, blocked, map fallback, hysteresis, phrasing
python desktop-beta/pacman_map.py      # the two-ghost weave + wall block
python desktop-beta/lensa_beta.py --self-test   # dead-band, held metres, dedupe, clip join, IDs
python desktop-beta/list_classes.py    # live vs dormant classes report
```

Live-tuning trick for the demo: while the beta runs, edit
`assets/context/rules/hazard_priority_rules.yaml` (e.g. `caution_below_m: 3.0 → 5.0`) — behaviour
changes within ~2 s, no restart. That moment sells the whole content-layer design.

---

## 13. Likely examiner questions

**Q: Is YOLO a CNN or an LSTM?**
A CNN — convolutional layers detecting spatial patterns in a single pass. LSTMs model sequences
(text/time series) and have no role in single-frame detection. (§3.2)

**Q: Why not use the LLM for everything? It's "smarter."**
Latency (seconds vs microseconds), hallucination risk in a safety-critical path, and battery. The
LLM adds *description*, the rules guarantee *safety*. Two loops, never mixed. (§5.1)

**Q: How accurate is your distance?**
On the laptop: an admitted heuristic (size ∝ 1/d, ±30-ish%, labelled "est"). On the wearable:
physically measured ToF per 8×8 zone fused per object — the design never relies on the heuristic
for the final product. (§4)

**Q: What happens when detection fails / it's dark / fog?**
Confidence floor drops weak detections; unknown distance defaults to caution; disconnect speaks a
stop-and-wait fallback; the immediate-stop band needs only ToF (works in the dark). The system's
bias is: silence is never assumed safe — caution lines exist for uncertainty.

**Q: Your catalog lists stairs and potholes — can you detect them?**
Potholes, broken road and manhole covers (open / displaced / broken / intact): yes — our own
6-class YOLO26n, test mAP50 0.85. Stairs, drains, curbs and 8 others: not yet — 11 of 32 catalog
entries are dormant and documented; each needs a labelled dataset and the same training run. (§3.6)

**Q: Why does your pothole model make mistakes?**
Most false alarms are "pothole" on road texture/patches; and the training images were grayscale
and partly rotated, so real colour footage may score lower than the test set. Walk tests with the
head unit will measure it honestly. (§3.6)

**Q: Your voice used to flip between left and right — how did you fix it?**
Root causes, not symptoms: blocked sides were computed after merging same-class objects (hiding
obstacles), the centre case always chose left, and nothing held a decision between frames. Now:
box-width zones from every detection, lean-aware centre steering, 3-frame hysteresis, and
per-object tracking with a side dead-band. All are regression-tested. (§5.2)

**Q: Why BFS and not A\*?**
Unweighted uniform grid → BFS already returns the provably shortest path; A\* only accelerates
search toward a *known* goal, but our goal (deepest reachable cell) is discovered by the flood
itself; 208 cells = microseconds anyway. (§7.3)

**Q: Why does the phone do the AI and not the ESP32?**
1–3 FPS at 96–160 px (microcontroller) vs 30+ FPS at 640² (phone NPU). The ESP32 is a
sensor-fusion streamer — that's what its 8 MB PSRAM is genuinely good at. (§8)

**Q: How do you fit neural TTS in a 150 MB app?**
We don't ship the model — we ship its *output*: pre-rendered WAV clips (~100 KB each) for the
finite safety vocabulary, rendered once offline. Dynamic sentences fall back to system TTS. (§6.2)

**Q: What's genuinely novel here vs assembling libraries?**
The integration is the contribution: a two-loop safety architecture with a deterministic,
content-tunable Malay guidance layer (hot-reloadable YAML shared across desktop and phone), ToF
zone-fusion for per-object distance, a cached-clip strategy making neural Malay TTS viable
offline on-device, a custom-trained road-hazard detector for Malaysian pavements, and a BFS
occupancy mapper that rescues the voice when the coarse zones are all blocked — all scoped, tested, and documented for a low-cost Malaysian-context device.

**Q: How do you know the safety logic is correct?**
It is deterministic and unit-tested: stop-band override, steer-away cases, confidence-floor
rejection, the October steering bugs (A–D), hysteresis, the map fallback, and the two-ghost
weave are executable tests you can run live. (§12)

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Inference** | Running a trained model (vs training it). All LENSA does on-device. |
| **CNN** | Convolutional neural network — sliding learned filters over an image; the spatial-pattern architecture. |
| **LSTM** | Recurrent network for sequences; carries memory across time steps. Not used here. |
| **One-stage detector** | Predicts all boxes+classes in a single network pass (YOLO). Two-stage = propose regions, then classify (R-CNN). |
| **Bounding box** | Rectangle around a detected object: (x1,y1,x2,y2) + class + confidence. |
| **IoU / NMS** | Overlap measure between boxes / the cleanup that removes duplicate boxes. |
| **Quantization (INT8/bf16)** | Storing weights in fewer bits: smaller, faster, NPU-friendly, ~1% accuracy cost. |
| **COCO** | The 80-class dataset the stock detector was trained on. |
| **NPU / NNAPI** | Phone neural accelerator / Android's API to reach it. |
| **ToF** | Time-of-Flight — distance from the travel time of emitted infrared light. |
| **Pinhole model / FOV** | Camera geometry: angle-of-view math that converts image position → real-world direction. |
| **Occupancy grid** | Top-down map of cells marked free/blocked — the standard robot-navigation world model. |
| **Configuration space (inflation)** | Growing obstacles by the user's half-width so the planner can treat the user as a point. |
| **BFS** | Breadth-first search — ring-by-ring flood that guarantees shortest paths on unweighted grids. |
| **Autoregressive** | Generating one token at a time, each depending on the previous — why neural TTS is slow. |
| **Neural codec / vocoder** | Network that turns discrete audio tokens back into a waveform (DistilCodec). |
| **SoftAP / MJPEG** | ESP32 acting as its own Wi-Fi hotspot / a video stream of JPEGs over HTTP. |
| **I²C / DVP / PSRAM** | Sensor bus (ToF) / parallel camera interface / external RAM the ESP32 buffers frames in. |
| **SAPI5 / COM thread affinity** | Windows speech engine / the "must be used on its creating thread" rule behind the one-line-then-silence bug. |
| **Hot-reload** | The beta re-reads edited YAML within ~2 s by polling file modification times. |
| **Loop A / Loop B** | Continuous deterministic safety loop / on-demand LLM description loop. |
| **Transfer learning / fine-tuning** | Starting training from a model that already learned general features (COCO) instead of from scratch. |
| **Catastrophic forgetting** | A model fine-tuned only on new classes loses the old ones — why the pothole model is a second model. |
| **mAP50** | Mean average precision at ≥50% box overlap — the standard detection accuracy score (1.0 = perfect). |
| **Object tracking (ByteTrack)** | Matching boxes frame to frame so each object keeps an ID. |
| **Hysteresis / dead-band** | Requiring a clear margin or several frames before changing a decision, so noise can't flip it. |

---

*Kept honest on purpose: the desktop beta and this document are the working proof; the phone app
and wearable are fully specified plans with paste-ready code, not yet built hardware. Say it that
way in the presentation — examiners reward candour armed with a working demo.*

---

## 15. What changed since the proposal defence (Oct 2026)

The deck's "future work" said: fix the direction-to-text logic, use real ToF distance, add
low-angle arithmetic, and detect roads/drains/pavement defects. Status:

| Deck said | Done |
|---|---|
| Correct the semantic direction logic | ✅ Box-width zones from all detections, lean-aware centre steering, all-blocked → map fallback / "Laluan tersekat", hysteresis, tracking. Regression-tested. |
| Real distance from the ToF sensor | ⏳ Firmware rewritten for the VL53L8CX; head unit being built; beta still uses the size estimate. |
| Low-angle (ground) arithmetic | ⏳ Ground hazards are announced without metres until the head-unit tilt is known. |
| Roads / longkang / pavement defects | ◐ Potholes, broken road + 4 manhole states in one model (test mAP50 0.85). Drains/curbs/stairs need data. |

Also new: object IDs (ByteTrack) and duplicate-box merging; catalog as a whitelist; two-clip
Malay voice with silence-trimmed joins and correct "dua meter"; "Laluan tersekat" capped at 3 per
blockage; repeat timing fixed; map refresh every 4 s shared with the voice; a GPU training
environment (`D:\lensa-venv-train`) separate from the CPU beta.
