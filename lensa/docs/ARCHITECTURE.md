# Architecture

> **Last revised: 2026-10-20** (Loop B moves ONLINE: Gemini vision for scene description + sign reading;
> earlier: sensor → VL53L8CX, TTS → Malaysian-TTS-0.6B clips, second road-hazard detector). This is the canonical LENSA architecture. It supersedes the
> earlier Flutter description: after supervisor review, the app moves to **native Android
> (Kotlin)** for cleaner on-device AI integration, and the AI runs **on the phone, offline**.
> The Flutter prototype was ported (logic → `desktop-beta/`, build recipe → the phase plans)
> and removed from the tree; it lives at git tag **`flutter-reference`**.

LENSA is split into a **physical head unit** and a **native Android app**. The **phone is the
brain** — all AI runs on-device, offline. The ESP32-S3 is a **"Wireless Eye"**: a fast,
synchronized sensor-fusion streamer, *not* a deep-learning device.

## The shape: one device, two loops

```
        HEAD UNIT (ESP32-S3 — "Wireless Eye")        PHONE (Kotlin — the brain)
   ┌──────────────────────────────────┐        ┌────────────────────────────────────────┐
   │ OV5640 cam ─┐  crop/resize/       │ Wi-Fi  │  HeadUnitClient (MJPEG + ToF grid)      │
   │ VL53L8CX 8×8┤  compress, time-sync│ SoftAP │        │  synced (frame, depthGrid)      │
   │             └─ MJPEG + ToF payload │═══════▶│        ▼                                │
   └──────────────────────────────────┘(offline)│  ┌─ LOOP A · micro-nav (always on) ───┐ │
                                                 │  │ YOLO26n (LiteRT/NNAPI)             │ │
                                                 │  │  → map bbox → ToF zone (distance)  │ │
                                                 │  │  → prioritize (Kotlin rules)       │ │
                                                 │  │  → template phrase                 │ │
                                                 │  │  → cached-clip TTS  ▶ speaker      │ │
                                                 │  └────────────────────────────────────┘ │
                                                 │  ┌─ LOOP B · macro-nav (on-demand) ───┐ │
                                                 │  │ camera frame → Gemini (ONLINE)     │ │
                                                 │  │  (MediaPipe LLM) → rich Malay      │ │
                                                 │  │  → system TTS   ▶ speaker         │ │
                                                 │  └────────────────────────────────────┘ │
                                                 │     OcrService (ML Kit) — on-demand      │
                                                 └────────────────────────────────────────┘
```

- **Loop A — micro-navigation** is the product: continuous, deterministic, safety-critical,
  low-latency. No LLM in this path — it *cannot* hallucinate a hazard.
- **Loop B — macro-navigation** is the on-demand enrichment, and the **only part that needs
  internet**: a user gesture sends one camera frame to **Gemini** (vision) for a
  natural scene description. Latency-tolerant.
- The two loops **never block each other; Loop A always owns the speaker.** Under thermal or
  battery pressure, Loop B is suppressed first — Loop A is never suppressed.

## Physical — head unit ("Wireless Eye")

The ESP32-S3 **N16R8** (16 MB flash / **8 MB octal PSRAM**) is used for what it is genuinely good
at: high-throughput, time-synchronized sensor fusion and streaming.

- **Sensor fusion in PSRAM** — buffers a 640×480 JPEG from the OV5640 *and* all **64 zones of the
  VL53L8CX (8×8 depth matrix)**, packaged into one timestamped payload so frame and depth are
  aligned in time. The large PSRAM is what lets it do this without dropping packets.
- **On-node preprocessing** — crop / resize / JPEG-compress before transmit to save Wi-Fi
  bandwidth and phone decode cost.
- **Streaming** — runs as a **Wi-Fi SoftAP**; the phone joins it. Self-contained on the body, no
  router, fully offline. (Phone has no internet while joined — fine; LENSA is offline by design.)
- **No on-node inference.** A microcontroller runs YOLO at ~1–3 FPS on tiny inputs — too slow for a
  walking pace. The phone's NPU/GPU runs YOLO26n at 30+ FPS on 640×640. Detection stays on the phone.

Bill of materials and wiring: see `HARDWARE.md`.

## Digital — native Android app

- **Main processing unit** — Kotlin app; coroutines + `Flow` drive the pipeline.
- **YOLO26n (INT8)** via **LiteRT/TFLite** with GPU/NNAPI delegate — real-time detection. Two
  detectors: stock COCO YOLO26n + the road-hazard YOLO26n we trained (pothole, broken road).
  Each box gets a tracker ID so its side and spoken distance stay steady between frames.
- **8×8 ToF fusion** — each detection's bounding box is mapped to the overlapping ToF zone(s) to
  attach an accurate `distance_m` and `position` (left / center / right).
- **Prioritisation** — closest + most dangerous first (Kotlin rules; see `rules/`).
- **Loop A phrasing** — deterministic template from the phrase libraries (`language/`).
- **Loop B** — **Gemini** (online vision API), on-demand only: describe the scene, read a sign.
  Chosen over on-device Gemma 3n (2026-10-20): a vision model good enough to describe a street
  is several GB — too big to ship in a phone app. No internet → Loop B says so; Loop A is unaffected.
- **TTS** — Mesolitica **Malaysian-TTS-0.6B** (speaker *husein*) as pre-rendered clips for Loop A; system TTS for dynamic text (see below).

## On-device AI stack — what runs where

| Component | Model / lib | Runtime | Loop |
|---|---|---|---|
| Object detection | 2× YOLO26n INT8 (COCO + road hazard) | LiteRT/TFLite + GPU/NNAPI | A |
| Distance + position | *8×8 ToF fusion* (no model) | CPU | A |
| Hazard priority + phrasing | *Kotlin rules + template* (no model) | CPU (µs) | A |
| Scene description + sign reading | Gemini (vision) | online API, on demand | B |
| OCR | ML Kit Text Recognition v2 | ML Kit | on-demand |
| Text-to-speech | Malaysian-TTS-0.6B (husein) | cached clips (A); system TTS for dynamic text (B) | A + B |

## Processing flow

**Loop A (per processed frame — throttled to save battery; speech debounced):**
1. Receive synced `(frame, depthGrid[8][8])` from the head unit.
2. Run YOLO detection on the frame.
3. For each detection, map its bbox → overlapping ToF zone(s) → attach `distance_m`, `position`.
4. Prioritise (closest + danger; `moving_toward` raises priority).
5. Compose the spoken line from the template phrase library (deterministic).
6. Speak via cached-clip TTS. Debounce: don't re-announce an unchanged scene; repeat
   high-urgency at the configured interval; **immediate-danger override** interrupts.

**Loop B (on user gesture — double-tap):**
1. Take the recent YOLO labels (e.g. `["kerusi","meja","pintu"]`).
2. Send the current frame + `agents/loopb_describe_scene.md` to Gemini → 2–3 short sentences
   (hazards first) in the user's language. No internet / no key → a spoken message, nothing else.
3. Speak it with system TTS (free text can't be pre-rendered). Never gates or delays Loop A.

**OCR (on user gesture — two-finger tap / long-press):** run ML Kit on the current frame →
summarise per `rules/ocr_reading_rules.yaml` → speak.

## TTS strategy — instant safety, rich on demand

The Malay neural TTS (~1.2 GB, ~1 min per phrase on CPU) is far too slow to run **live** in the
safety path. But Loop A's vocabulary is a **small, finite set**, so it never has to.

- **Loop A — cached clips.** Every sentence is two pre-rendered clips: `"{objek} di {arah}"`
  (catalog objects × kiri/kanan/hadapan) + `"{n} meter, elak ke {kiri|kanan}"` (n = 1–6). The
  ~76 clips are rendered offline once (`desktop-beta/render_clips.py`), silence-trimmed and
  joined at play time → near-zero latency, Husein's voice, ~6 MB to ship.
- **Loop B / OCR — dynamic text.** Free-form descriptions can't be pre-rendered: Android system
  TTS (Malay). Latency is acceptable there.

## Transport & data contract

Head unit → phone over Wi-Fi (SoftAP). Frames as **MJPEG over HTTP**; the ToF depth grid on a
small **JSON/WebSocket** endpoint, each payload timestamped so the phone can pair frame with grid.
BLE is *not* used (it cannot carry a camera stream); it is left as a noted future option only.

Head-unit payload (edit here if the firmware changes):

```yaml
payload:
  timestamp_ms: 172839..._   # shared clock, used to pair frame ↔ depth
  frame_jpeg: <bytes>        # 640×480 (or preprocessed), MJPEG
  tof_grid:                  # VL53L8CX 8×8 = 64 zones, millimetres
    - [1200, 1180, ...]      # 8 rows × 8 cols; null where no return
```

The phone fuses that into the per-detection record the agents consume. Keeping this record stable
lets you swap the detector without touching the agents:

```yaml
detection:
  class: person          # must match a yolo_class in rules/object_catalog.yaml
  confidence: 0.87        # 0.0–1.0
  distance_m: 2.0         # from the ToF zone(s) overlapping this bbox; null if unknown
  position: center        # left | center | right (from bbox x + ToF column)
  moving_toward: false    # optional; raises priority if true
```

OCR text (from ML Kit) is passed separately as `{ text, confidence, language }`.

## App module structure (Kotlin)

```
app/src/main/
├── transport/    HeadUnitClient — MJPEG frames + ToF grid   (Mock + Wifi impls)
├── perception/   ObjectDetector (LiteRT YOLO) · OcrService (ML Kit)   (Mock + Real)
├── navigation/   HazardPrioritizer (rules) · PhraseComposer (template)     ← Loop A
│                 SceneDescriber + SignReader (Gemini, online)               ← Loop B
├── audio/        TtsService — cached-clip player + system TTS for dynamic text
├── content/      loads assets/context/**  (the editable layer, ported as-is)
├── pipeline/     PipelineController — Loop A orchestration + Loop B trigger (coroutines/Flow)
├── settings/     UserSettings (DataStore)
├── ui/           audio-first: full-screen gesture surface + status + settings
└── di/           composition root (manual or Hilt)
```

Every interface has a `Mock` implementation, so the **whole pipeline runs and demos with no
hardware** (the current repo's mock/real seam, ported). The `assets/context/**` editable layer
(prompts, rules, phrase libraries, config) ports over unchanged.

## Where each module's editable context lives

| Module                    | File(s) |
|---------------------------|---------|
| Scene interpretation      | `assets/context/agents/layer1_scene_interpretation.md` |
| Hazard decision           | `assets/context/agents/layer2_hazard_decision.md` + `rules/hazard_priority_rules.yaml` |
| Instruction / language    | `assets/context/agents/layer3_instruction_language.md` + `language/*.yaml` |
| Object set                | `assets/context/rules/object_catalog.yaml` |
| OCR behaviour             | `assets/context/rules/ocr_reading_rules.yaml` |
| Voice style / fallbacks   | `assets/context/config/*.yaml` |

> Format note: the config layer (`rules/`, `language/`, `config/`) is **plain YAML**; only the
> agent prompts stay markdown. Ports (Kotlin/Flutter) load the `.yaml` directly — no fenced-block
> extraction needed.

> Note: Loop B uses `agents/loopb_*.md` (Gemini, online); the older 3-layer prompts are kept as
> reference for an LLM-phrased Loop A, which is NOT used. **Loop A** phrasing is a deterministic
> template read from `language/*` + `rules/*` — no model in the safety path.

## UX / accessibility (the user is blind)

The screen is one **full-screen gesture surface**, TalkBack-friendly, haptic-confirmed:
- **Double-tap** → Loop B (describe the scene).
- **Two-finger tap / long-press** → OCR read.
- Loop A needs no interaction — it speaks when there is a hazard.

## Safety, errors, degradation

- Head-unit disconnect → speak `sambungan terputus` (`config/safety_fallback.yaml`).
- Missing ToF return / low confidence → caution defaults (existing fallback rules).
- **Thermal / battery pressure → suppress Loop B; never Loop A.** Safety has absolute priority.
- Speech debounce / repeat / immediate-danger-override logic ports from the prototype pipeline.

## Testing

- `PhraseComposer` is pure and deterministic → **unit tests** (detection in → exact phrase out).
  Cheap and high-value for a safety path.
- Mock transport + mock detector → **full pipeline test and live demo with no hardware**.
- Roadmap phases 4–5 (controlled walk tests → field trial) unchanged — see `ROADMAP.md`.

## Build order (AGILE — each step is demoable)

1. Kotlin skeleton + content layer + **mock pipeline** + TTS (runs, speaks, no hardware).
2. Real YOLO (LiteRT) on the phone camera → **Loop A end-to-end on-device**.
3. ESP32 SoftAP: MJPEG + 8×8 ToF payload → real head unit feeds Loop A.
4. **Loop B** (Gemini, online, on-demand): scene description + sign reading.
5. Cached-clip TTS + walk tests.

## Scope (fixed for this cycle)

**In:** obstacle detection + distance + hazard warning (Loop A), on-demand scene description
(Loop B), OCR-on-demand, bilingual voice output. **Out:** voice commands / STT, face recognition,
conversational chat, memory database. (These were considered and deferred to keep the
safety-critical loop fast and the FYP demonstrable.)
