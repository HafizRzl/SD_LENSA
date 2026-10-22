# LENSA — Android app (v0.4)

Live view on top (camera + boxes, `#ID` labels, left/centre/right lines, pacman mini-map, spoken
line), and a 2×2 grid of big buttons below:

| | |
|---|---|
| **PLAY** — Loop A on: back camera → both detectors → rules → Husein voice | **STOP** — Loop A off |
| **TERANGKAN** — Loop B: describe the scene (Gemini, online) | **BACA** — Loop B: read a sign (Gemini, online) |

The brain is the desktop beta ported 1:1 to Kotlin — `Rules.kt`, `PacmanMap.kt`, `Tracking.kt`,
`LoopB.kt` — and `GoldenParityTest` / `LoopBTest` prove it says exactly what the Python says
(50 scene runs, 5 map routes, both Loop B prompts, 14 sign-text cases).

Not yet: the ESP32 head unit.

## Loop B (TERANGKAN / BACA)
- Works **anytime**: while PLAY runs it takes a ~1600 px still from the live camera; when stopped it
  opens the camera just for that one photo. Says "Sebentar" (Husein clip), sends the photo + the
  prompt (`assets/context/agents/loopb_*.md`) to Gemini, speaks the answer with Android TTS and
  shows it on screen for 15 s. Hazard warnings keep priority: an urgent "Berhenti!/Awas!" cuts in;
  a routine hazard line can't drop the answer.
- No key in the APK → "Kunci API belum ditetapkan". No internet / API error → "Tiada internet…"
  (the error detail is shown in the status line).
- **Malay voice:** answers use Android TTS, preferring Google's engine (it has Malay). If it
  sounds English, install/enable "Speech Recognition & Synthesis from Google" and its Malay voice
  (Settings → General management → Text-to-speech).
- Each press sends ONE photo to Google. Nothing is sent otherwise.

### The API key
Put it in `desktop-beta/secrets.yaml` (copy `secrets.example.yaml`; gitignored) — the same file
the desktop beta uses. `sync_assets.py` bakes it into the APK as `assets/loopb.bin`, XOR-scrambled
so a plain text search of the APK doesn't find it.

**That is not real protection — anyone determined can recover a key shipped inside an APK.**
Limit the damage on Google's side (Google Cloud console → APIs & Services → Credentials → your key):
1. Keep the key on the **free tier with no billing account linked** — worst case, someone uses
   up your free quota; no money can be charged.
2. **API restrictions** → restrict the key to the *Generative Language API* only.
3. Don't share the APK publicly; if it leaks, **delete the key and make a new one** (then rebuild).

Real fix (later): a tiny server holding the key (e.g. a free Cloudflare Worker that forwards to
Gemini), or Google's *Firebase AI Logic* + *App Check*. Only `GeminiClient` in `LoopB.kt` changes.

## Build (toolchain lives on D:, nothing on C:)

```bash
# 1. models: export once from the desktop beta (Ultralytics, ONNX with NMS)
#    D:\lensa-venv-train\Scripts\python.exe -c "from ultralytics import YOLO; [YOLO(p).export(format='onnx', imgsz=640, simplify=True, nms=True) for p in ('desktop-beta/models/yolo26n.pt','desktop-beta/models/road_hazard_yolo26n.pt')]"
# 2. copy models + clips + yaml into the APK assets
python android-app/sync_assets.py
# 3. after changing lensa_rules.py / pacman_map.py: refresh the parity cases
python android-app/make_golden.py
# 4. build + test (Git Bash). sync_assets.py says whether a Loop B key was baked in.
cd android-app
export JAVA_HOME='D:\lensa-android\jdk-21.0.12.1+1' GRADLE_USER_HOME='D:\lensa-android\gradle-home'
./gradlew.bat testDebugUnitTest assembleDebug
# -> app/build/outputs/apk/debug/app-debug.apk
```
`local.properties` (gitignored) must contain `sdk.dir=D\:/lensa-android/sdk`.

## Why ONNX, not TFLite
Ultralytics' LiteRT/TFLite export runs only on Linux/macOS. ONNX exports on Windows and
ONNX Runtime Android runs it; detections are identical to PyTorch at LENSA's 0.45 threshold
(232/232 boxes on 100 test images).
