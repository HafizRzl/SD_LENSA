"""Copy what the APK needs into app/src/main/assets/ (gitignored — always regenerated):

  context/   the behaviour yaml + Loop B prompts (same files the desktop beta uses)
  models/    the two ONNX detectors (desktop-beta/models/*.onnx — export with Ultralytics)
  clips/     the Husein voice clips (desktop-beta/voice_cache/husein/*.wav)

    python android-app/sync_assets.py      (run before every APK build)
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DST = HERE / "app" / "src" / "main" / "assets"

if DST.exists():
    shutil.rmtree(DST)
for sub in ("rules", "language", "config", "agents"):
    shutil.copytree(REPO / "assets" / "context" / sub, DST / "context" / sub)
(DST / "models").mkdir(parents=True)
for name in ("yolo26n.onnx", "road_hazard_yolo26n.onnx"):
    src = REPO / "desktop-beta" / "models" / name
    if not src.exists():
        raise SystemExit(f"missing {src} — export it first (see desktop-beta/README.md)")
    shutil.copy(src, DST / "models" / name)
clips = list((REPO / "desktop-beta" / "voice_cache" / "husein").glob("*.wav"))
(DST / "clips").mkdir()
for c in clips:
    shutil.copy(c, DST / "clips" / c.name)
# Loop B key: desktop-beta/secrets.yaml (gitignored) -> assets/loopb.bin, XOR-scrambled so a plain
# text search of the APK doesn't find it. NOT real security (see android-app/README.md). Never printed.
import json, os, yaml
sec = REPO / "desktop-beta" / "secrets.yaml"
s = (yaml.safe_load(sec.read_text(encoding="utf-8")) or {}) if sec.exists() else {}
key = os.environ.get("GEMINI_API_KEY") or str(s.get("gemini_api_key") or "")
model = str(s.get("gemini_model") or "")
if key and model and not key.startswith("PASTE") and not model.startswith("PASTE"):
    mask = b"LENSA-loopB-key-mask"
    raw = json.dumps({"key": key, "model": model}).encode()
    (DST / "loopb.bin").write_bytes(bytes(b ^ mask[i % len(mask)] for i, b in enumerate(raw)))
    print(f"Loop B: key + model '{model}' baked in (scrambled)")
else:
    print("Loop B: no key in desktop-beta/secrets.yaml — TERANG/BACA will say 'Kunci API belum ditetapkan'")
mb = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file()) / 1e6
print(f"assets synced: context yaml, 2 models, {len(clips)} clips — {mb:.1f} MB -> {DST}")
