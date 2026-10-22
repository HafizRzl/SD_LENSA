# LENSA — Desktop Beta

The **working LENSA system** on a laptop: two YOLO26n detectors + the deterministic Loop-A
navigation logic + Malay voice. It reads the **same** content files the phone app will
(`../assets/context/**/*.yaml`), so whatever you tune here behaves identically on the phone.

```
webcam / video / image
  → YOLO26n (COCO: people, vehicles, chairs…)  ┐  ByteTrack IDs per object (#3, #r1…),
  → road-hazard model (pothole, broken road)   ┘  duplicate boxes merged
  → side (left/centre/right, by box width, with a dead-band) + distance*
  → rules engine: stop / steer / caution / blocked   (+ pacman-map fallback every 4 s)
  → Bahasa Melayu line → two cached Husein clips, trimmed and joined → speaker
```

\* Laptops have no ToF sensor, so distance is a **rough estimate from box height** (marked
"est"). Flat ground hazards (pothole, broken road, drain…) get **no** distance — box height
says nothing about them — and are announced without metres. On the wearable, distance will
come from the **VL53L8CX** 8×8 ToF sensor.

## Run

```bash
# once: render the voice clips in Husein's voice (~1 min per clip on CPU, Ctrl+C-safe)
python desktop-beta/render_clips.py --prewarm

python desktop-beta/lensa_beta.py                    # webcam, both detectors, Husein voice
python desktop-beta/lensa_beta.py --source clip.mp4  # a video file
python desktop-beta/lensa_beta.py --source photo.jpg # a still image (no webcam)
python desktop-beta/lensa_beta.py --hazard-model none   # COCO detector only (~2x faster)
python desktop-beta/lensa_beta.py --voice os         # fast OS voice instead of Husein
python desktop-beta/lensa_beta.py --lang en          # English labels/guidance
python desktop-beta/lensa_beta.py --no-speak         # silent
python desktop-beta/lensa_beta.py --no-map           # hide the top-down map window
```
Press **q** to quit. Bottom banner = the spoken guidance; colour = urgency (red = stop/blocked,
orange = steer/caution, green = clear). Faint vertical lines mark the left/centre/right zones;
grey boxes are detected classes you removed from the catalog (ignored). The top-right **voice
HUD** shows when it speaks and the repeat countdown. The **map window** redraws every 4 s.

Speed on a laptop CPU: ~9.5 fps with one detector, ~4.8 fps with both.

## What it says

| Situation | Example |
|---|---|
| Hazard ahead, a side is open | `orang di hadapan, 2 meter, elak ke kiri` (steps away from the side it leans to) |
| Close hazard on a side | `kerusi di kiri, 2 meter, elak ke kanan` |
| Far hazard / ground hazard on a side | `kerusi di kiri, 5 meter` · `Hati-hati, lubang jalan di kiri` |
| Closer than 1 m | `Berhenti! kereta di hadapan` |
| Every direction blocked, no gap on the map | `Berhenti! Laluan tersekat` (max 3× per blockage) |
| Nothing in range | `Laluan hadapan kosong` |

Each sentence is **two clips**: `"{objek} di {arah}"` + `"{n} meter, elak ke {kiri|kanan}"`
(n = 1–6, the notice band). Numbers are spoken as Malay words ("dua meter").

## Loop B — describe the scene / read a sign (online)

Press **d** (describe) or **r** (read a sign) in the beta window. LENSA says "Sebentar", sends
the current camera frame to **Gemini**, and speaks the answer (also shown top-left). This is the
only part of LENSA that needs internet; hazards (Loop A) keep running offline either way.

Setup once: copy `secrets.example.yaml` → `secrets.yaml` (gitignored) and paste your Gemini API
key and model name. Prompts: `assets/context/agents/loopb_*.md`; sign rules:
`rules/ocr_reading_rules.yaml`. Answers use the OS voice (`dynamic_voice` in
`audio_output_style.yaml`: `auto` picks a Malay Windows voice once installed).
Self-test (no network): `python desktop-beta/loop_b.py`.

## Voice — how Husein works

Default voice comes from `assets/context/config/audio_output_style.yaml` (`voice: husein`).
It plays **pre-rendered clips** from `voice_cache/husein/`. A line with no clip yet is spoken by
the OS voice once, and `render_clips.py` is spawned in a **separate process** to render it —
next time it's Husein. `--prewarm` renders the whole set up front (76 clips). How a word is
*pronounced* can be tuned in `phrase_library_ms.yaml` → `pronounce:` (then `--prewarm --force`).

## Tuning — live, no restart

The beta **hot-reloads** the `.yaml` content files ~2 s after you save:

- `rules/object_catalog.yaml` — the **whitelist** of objects LENSA announces: BM/EN labels,
  `danger_weight`, `ground: true` for flat hazards. Classes not listed are ignored.
- `rules/hazard_priority_rules.yaml` — confidence floor, distance bands, sensitivity.
- `language/phrase_library_ms.yaml` — the spoken Malay templates + `pronounce:` respellings.
- `config/audio_output_style.yaml` — repeat frequency (`repeat_*_every_s`,
  `speak_when_scene_unchanged`, `min_gap_between_lines_s`), `blocked_max_repeats`.

Exceptions (need a restart): `voice:` and `speaking_rate:`.

## What it can detect

Two detectors: stock YOLO26n (Ultralytics, the 80 **COCO** classes — not trained by us) and the
**road-hazard model** we trained (`broken_road`, `pothole`; test mAP50 0.73). Run
`python desktop-beta/list_classes.py` to regenerate **`detectable_classes.md`** — which catalog
entries are **live** vs **dormant** (dormant = no model detects it yet, e.g. stairs, drain, curb).

## Training a new hazard model

`train_hazards.py` fine-tunes YOLO26n on a labelled dataset in `assets/dataset/` (gitignored).
It runs in a **separate CUDA environment** on the RTX 4060 — the beta's own Python is CPU-only
on purpose (the Husein TTS was patched for CPU):

```bash
D:\lensa-venv-train\Scripts\python.exe desktop-beta/train_hazards.py            # ~2 h, 60 epochs
D:\lensa-venv-train\Scripts\python.exe desktop-beta/train_hazards.py --resume   # continue a stopped run
```
Plug the charger in first (the GPU throttles on battery) and close heavy apps — RAM is tight.
Outputs: `models/road_hazard_yolo26n.pt` + charts in `runs/road_hazard/` (both gitignored).

## Self-tests

```bash
python desktop-beta/lensa_rules.py         # steering, blocked, map fallback, hysteresis, phrasing
python desktop-beta/pacman_map.py          # occupancy grid + BFS route
python desktop-beta/lensa_beta.py --self-test   # dead-band, held metres, dedupe, clip join, speaker order, IDs
python desktop-beta/loop_b.py             # Loop B prompts, sign lines, answer/offline paths (no network)
```

## Files

- `lensa_beta.py` — camera loop, both detectors + tracking, side/distance smoothing, drawing,
  speech gate, voice backends, hot-reload.
- `lensa_rules.py` — the deterministic Loop-A engine (the logic the phone app ports).
- `pacman_map.py` — top-down occupancy grid + BFS route (every 4 s; the all-blocked fallback).
- `loop_b.py` — Loop B: Gemini describe / read-sign, on a background thread.
- `render_clips.py` — renders Malay clips (Malaysian-TTS-0.6B) into `voice_cache/`.
- `husein_tts.py` — the neural TTS wrapper (CPU-safe). `clip_cache.py` — cache paths.
- `train_hazards.py` — trains the road-hazard detector (GPU venv).
- `list_classes.py` — regenerates `detectable_classes.md`.
