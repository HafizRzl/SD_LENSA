# Model Notes

What runs where, and why. Update as models are swapped, trained or re-quantized.

## Object detection — two YOLO26n (nano) detectors
- Single-stage, one-pass detector chosen for the **best speed–accuracy trade-off on edge**.
  Preferred over Faster R-CNN (two-stage, slower), SSD (trails YOLO on accuracy–speed),
  EfficientDet (lags on the real-time edge trade-off) and DETR/RT-DETR (too compute-heavy).
- **Detector 1 — stock YOLO26n** (`models/yolo26n.pt`): Ultralytics-pretrained on COCO (80
  classes: people, vehicles, chairs, benches…). Not trained by us.
- **Detector 2 — road-hazard YOLO26n** (`models/road_hazard_yolo26n.pt`): **fine-tuned by us**
  (`desktop-beta/train_hazards.py`) from the COCO weights, **v2 = one model for 6 classes** over
  two datasets:
  - Kaggle/Roboflow "pothole and speed breaker detect" (CC BY 4.0; 3,795 / 168 / 253 images) —
    `broken_road`, `pothole`. Grayscale and partly 90°-rotated (Roboflow pre-processing).
  - Ultralytics Platform "manhole clean" (822 / 182 / 91 images, colour, street level) —
    `manhole_broken`, `manhole_intact`, `manhole_open` (cover missing), `manhole_displaced`.
    The original class codes are pinyin (jgps / jgwh / jgsd / jgwg), read off sample images.
  - 60 epochs on an RTX 4060 (~3.1 h), best epoch 60 (still improving — more epochs may help).
  - **Test set: mAP50 0.852, mAP50-95 0.649** — broken_road 0.732, pothole 0.724,
    manhole_broken 0.982, manhole_intact 0.851, manhole_open 0.941, manhole_displaced 0.885.
  - Adding manholes did not hurt potholes (v1 pothole-only model: 0.739 / 0.714; its charts stay
    in `runs/road_hazard/`).
  - Confusion at the working threshold (confidence 0.25): **no open manhole was labelled
    "intact"** (silent) — all were flagged as *some* hazard, but only 40% under the right name
    (33% "displaced", 20% "pothole"). Weak spot: **27% of displaced covers are labelled
    "intact"** (silent, since intact has danger 0), and 65% of intact covers raise a false
    warning. Test set is small (91 manhole images) — treat these as rough.
  - Why a second model instead of retraining one: fine-tuning on potholes alone makes a model
    forget COCO classes (catastrophic forgetting). Cost: ~2× inference (laptop CPU ~9.5 → ~4.8 fps).
- Desktop beta runs both as PyTorch `.pt` on the CPU, with ByteTrack object IDs. The phone plan
  is an **INT8 `.tflite`** export (LiteRT/NNAPI) — not done yet.
- Runs on the **phone**, **not** the ESP32-S3 — a microcontroller manages only ~1–3 FPS.

## Loop B — Gemini vision (ONLINE): describe the scene + read signs
- Decided 2026-10-20: Loop B is the only part of LENSA that needs internet. The user presses
  `d` (describe) or `r` (read sign) in the beta; one JPEG frame goes to Gemini with the prompt
  from `assets/context/agents/loopb_*.md`. Key + model in `desktop-beta/secrets.yaml`
  (gitignored; copy `secrets.example.yaml`). Code: `desktop-beta/loop_b.py` (`GeminiVision` is
  the only class that talks to Google — swap it to change provider).
- Why online: a vision model that can describe a street is several GB — too big for a phone app.
- No internet / no key → a spoken message ("Tiada internet…"); Loop A keeps running regardless.
- **Phone app (v0.4):** TERANGKAN / BACA buttons, usable even when PLAY is off; a ~1600 px still
  goes to Gemini's REST `generateContent` (`android-app/.../LoopB.kt`); same prompts and sign
  rules as the beta, proven by `LoopBTest`. Key baked in from `secrets.yaml`, scrambled — not real
  protection; see `android-app/README.md` for the safeguards and the server-side fix.
- Answers are free text, so they're spoken by the OS voice (`dynamic_voice` in
  `audio_output_style.yaml` — `auto` picks a Malay voice once one is installed).

## (Superseded) Scene-description agent — Gemma 3n (E2B) via MediaPipe LLM Inference (LiteRT)
- Runs on-device, **Loop B only** (on-demand rich Malay descriptions) — **not** in the
  safety-critical Loop A, which is deterministic (rules + templates, no model). Not built yet.
- Prompts live in `assets/context/agents/`. Keep outputs short — it's a small model.

## Text-to-speech — Mesolitica Malaysian-TTS-0.6B (speaker: husein)
- Bahasa Melayu voice (629M speech LM + DistilCodec vocoder, 24 kHz, bf16 ~1.2 GB, CPU).
- **Loop A** (safety) plays **pre-rendered phrase clips**: every sentence is two clips —
  `"{objek} di {arah}"` + `"{n} meter, elak ke {kiri|kanan}"` — silence-trimmed and joined in
  memory. 76 clips cover every standard sentence; misses render in a background process
  (`desktop-beta/render_clips.py`). Numbers are spoken as Malay words ("dua meter"); respellings
  live in `phrase_library_ms.yaml` → `pronounce:`.
- Chosen over Malaysian-Dia-1.6B after listening tests (0.6B installs cleanly and is lighter on
  CPU). Fallback: OS/Android system TTS. ElevenLabs considered as an offline *renderer* only
  (cloud at runtime would break the offline design).

## OCR — now Gemini (Loop B); ML Kit Text Recognition V2 stays the offline option for the phone
- Reads local-language text (signage, room numbers). Behaviour in `rules/ocr_reading_rules.yaml`.
  Not built yet (phone app, Loop B).

## Deferred (out of scope this cycle)
- **Whisper / Android SpeechRecognizer** (voice commands), face recognition, chat, memory —
  cut to keep the safety loop fast. Revisit post-FYP; see `ARCHITECTURE.md` § Scope.

## Rough on-device budget (fill in as measured)
| Model | Format | Size | Runs on |
|---|---|---|---|
| YOLO26n (COCO) | PyTorch `.pt` (beta) → INT8 `.tflite` (phone, planned) | 5.5 MB `.pt` | Laptop CPU now; phone NPU/GPU later (Loop A) |
| YOLO26n (road hazard) | PyTorch `.pt` (beta) → INT8 `.tflite` (phone, planned) | 5.5 MB `.pt` | Laptop CPU now; phone NPU/GPU later (Loop A) |
| Gemini (vision) | online API | — (cloud) | Google's servers, on demand (Loop B) |
| Malaysian-TTS-0.6B | bf16 PyTorch (renders clips only) | ~1.2 GB | Laptop CPU, offline render; phone ships the ~6 MB of clips |
