# LENSA — Wearable Assistive Vision System

**Wearable Assistive Vision System for Visually Impaired Malaysians.**
A low-cost, bilingual (Bahasa Melayu / English), real-time navigation aid. A head-mounted
camera + ToF sensor stream to a smartphone, which detects obstacles, estimates distance,
prioritises hazards, and speaks concise guidance to the user.

> Author: Muhammad Hafiz Bin Razali · 22007409
> Supervisor: Ts. Nazleeni Samiha Haron

## How LENSA works (3C's)

1. **Capture** — head-mounted camera + ToF sensor see the scene.
2. **Compute** — object detection + distance + navigation logic (on the phone).
3. **Convey** — one prioritised Bahasa Melayu voice line, e.g. `"orang di kiri, 2 meter, elak ke kanan"`.

## What's in this repo

```
lensa/
├── FORHAFIZ.md        ← full project explainer, foundations → implementation (read this first)
├── assets/context/    ← the editable behaviour layer (YAML, hot-reloaded; agents/ = Gemma prompts)
├── assets/dataset/    ← training data (gitignored; e.g. the Kaggle pothole set)
├── desktop-beta/      ← the WORKING system: webcam → 2 detectors → rules → Malay voice → pacman map
├── hardware/          ← 3D-printable head-unit mounts (glasses mount, shield concept) + generators
└── docs/              ← architecture, hardware, model notes, roadmap, Kotlin phase 1–5 plans
```

Head-unit firmware (ESP32-S3 + OV5640 + VL53L8CX) lives outside this repo:
`../../script/lensa_head_unit/lensa_head_unit.ino`.

## Quickstart (desktop beta)

```bash
pip install -r desktop-beta/requirements.txt
python desktop-beta/render_clips.py --prewarm   # once: render the 76 Husein voice clips (slow, CPU)
python desktop-beta/lensa_beta.py               # webcam + Malay guidance + top-down map
```

Details, tuning, and self-tests: `desktop-beta/README.md`. Behaviour is tuned by editing
`assets/context/**/*.yaml` — the running beta picks up edits in ~2 s.

## The Android app (the product)

LENSA ships as a **native Android (Kotlin) app**: a simple front-facing UI (Start / Describe
scene / Read text buttons) that is a thin shell over everything in this repo — the content
layer, the detection → rules → voice pipeline, the clip cache, and the ESP32-S3 "Wireless Eye"
head unit (camera + 8×8 ToF over Wi-Fi SoftAP). Everything runs on-device, offline.

- **Kotlin scaffolding (written July 2026):** `docs/superpowers/plans/` — phases 1–5
  (app skeleton → YOLO → ESP32 transport → Gemma/OCR → cached TTS), with code, tests and
  commands. The desktop beta has moved on since (tracking, new steering, two-clip voice, second
  detector) — **port the logic from `desktop-beta/`, which is the source of truth.** Not started yet.
- **Canonical design:** `docs/ARCHITECTURE.md` (two-loop, phone-as-brain).
- **`desktop-beta/` is that app's brain, being validated first** — same YAML config, same
  rules, same voice strategy. The beta is not a separate product; it is the app minus the phone.

> History: the original Flutter prototype was ported into the beta + Kotlin plans and removed
> from the tree — it lives at git tag **`flutter-reference`**.
