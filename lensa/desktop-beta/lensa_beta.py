"""LENSA desktop beta — YOLO26n + the real Loop-A navigation logic on your laptop.

Pipeline (mirrors the phone, minus the ToF sensor):
    webcam/video/image  ->  YOLO26n + ByteTrack IDs  ->  position (left/centre/right)
                        ->  distance* ->  rules engine (shared assets/context/**)
                        ->  Bahasa Melayu guidance  ->  drawn on screen (+ optional speech)

* No ToF on a laptop, so distance is a rough monocular estimate from box size,
  clearly marked "est". In the product, real distance comes from the VL53L5CX.
  Disable it with --no-distance to see the "unknown distance" behaviour instead.

Usage:
    python lensa_beta.py                      # default webcam (index 0)
    python lensa_beta.py --source 1           # another camera
    python lensa_beta.py --source clip.mp4    # a video file
    python lensa_beta.py --source photo.jpg   # a still image (no webcam needed)
    python lensa_beta.py --voice os           # fast OS voice (default voice comes from
                                              # assets/context/config/audio_output_style.yaml)
    python lensa_beta.py --no-speak           # silent
Keys:  q = quit · d = describe the scene · r = read a sign   (d/r need internet: Loop B, Gemini)
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf
from ultralytics import YOLO

from clip_cache import clip_path
from lensa_rules import (Content, Detection, RulesNavigationAgent, content_stamp,
                         decision_segments, meaning_key)
from loop_b import LoopB

try:
    import winsound  # Windows stdlib, plays cached WAV clips
except ImportError:
    winsound = None

HERE = Path(__file__).parent
HAZARD_MODEL = HERE / "models" / "road_hazard_yolo26n.pt"   # written by train_hazards.py

# BGR colours by urgency/action
COL_STOP = (0, 0, 255)       # red
COL_MED = (0, 140, 255)      # orange
COL_LOW = (0, 220, 255)      # yellow
COL_CLEAR = (0, 200, 0)      # green
COL_BOX = (230, 180, 40)     # cyan-ish for detection boxes
COL_IGNORED = (110, 110, 110)  # grey: detected but not in the catalog (ignored)

ZONES = {"left": (0.0, 1 / 3), "center": (1 / 3, 2 / 3), "right": (2 / 3, 1.0)}
# ponytail: tune on walk tests. A tracked object must cross a 1/3 line by this much
# (fraction of frame width) before its side changes — absorbs YOLO box jitter.
ZONE_MARGIN = 0.04
DUP_IOU = 0.7          # two boxes overlapping this much = one object detected twice


def banner_colour(dec) -> tuple:
    if dec.action == "stop" or dec.urgency == "high":
        return COL_STOP
    if dec.action in ("steer_left", "steer_right") or dec.urgency == "medium":
        return COL_MED
    if dec.action == "proceed":
        return COL_CLEAR
    return COL_LOW


def estimate_distance_m(box_h_norm: float, k: float = 1.2) -> float:
    """Rough monocular distance proxy (stand-in for the ToF sensor). k = calibrate."""
    return max(0.5, min(10.0, k / max(box_h_norm, 0.02)))


def position_of(cx_norm: float, prev: str | None = None) -> str:
    raw = "left" if cx_norm < 1 / 3 else "right" if cx_norm > 2 / 3 else "center"
    if prev is None or raw == prev:
        return raw
    lo, hi = ZONES[prev]
    return prev if lo - ZONE_MARGIN <= cx_norm <= hi + ZONE_MARGIN else raw


class Tracks:
    """Per-object memory keyed by the tracker ID. Gives each object a side with a dead-band,
    a smoothed distance, and the metres actually SAID — held until the estimate moves
    >0.65 m away, so 2.49<->2.51 can't flip "2 meter"<->"3 meter" every frame."""
    EMA = 0.4          # distance smoothing (higher = reacts faster, jitters more)
    FORGET_S = 2.0     # drop an ID not seen for this long

    def __init__(self):
        self.s = {}
        self.frame = 0

    def tick(self):
        """Call once at the start of every frame (consecutive-frame counting)."""
        self.frame += 1

    def confirmed(self, tid, n: int) -> bool:
        """Seen in n frames in a row? Untracked boxes can't be counted, so they pass."""
        return tid is None or self.s.get(tid, {}).get("hits", 0) >= n

    def update(self, tid, cx, dist):
        st = self.s.get(tid) if tid is not None else None
        pos = position_of(cx, st["pos"] if st else None)
        if dist is not None and st and st["dist"] is not None:
            dist = st["dist"] + self.EMA * (dist - st["dist"])
        spoken = st["spoken"] if st else None
        if dist is None:
            spoken = None
        elif spoken is None or abs(dist - spoken) > 0.65:
            spoken = max(1, round(dist))
        if tid is not None:
            hits = st["hits"] + 1 if st and st["frame"] == self.frame - 1 else 1
            self.s[tid] = {"pos": pos, "dist": dist, "spoken": spoken, "seen": time.monotonic(),
                           "frame": self.frame, "hits": hits}
        return pos, dist, spoken

    def prune(self):
        cut = time.monotonic() - self.FORGET_S
        self.s = {k: v for k, v in self.s.items() if v["seen"] >= cut}


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(boxes):
    """boxes: [(x1, y1, x2, y2, conf, cls, tid)]. One object flagged twice (same box, or e.g.
    chair+bench on the same seat) -> keep only the most confident box."""
    kept = []
    for b in sorted(boxes, key=lambda b: -b[4]):
        if all(iou(b, k) < DUP_IOU for k in kept):
            kept.append(b)
    return kept


GAP_S = 0.08     # natural pause between the two halves of a sentence
FADE_S = 0.012   # tiny fade at each clip edge -> no click at the join


def _trim(x: np.ndarray, sr: int) -> np.ndarray:
    """Cut the leading/trailing silence the TTS leaves on every clip (0.1-0.35 s each)."""
    a = np.abs(x)
    idx = np.where(a > max(a.max() * 0.02, 1e-4))[0]
    if idx.size == 0:
        return x
    pad = int(0.04 * sr)   # keep soft onsets (s, h) intact
    x = x[max(0, idx[0] - pad): idx[-1] + pad].copy()
    f = min(int(FADE_S * sr), len(x) // 2)
    if f:
        ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
        x[:f] *= ramp
        x[-f:] *= ramp[::-1]
    return x


@lru_cache(maxsize=256)
def joined_wav(paths: tuple) -> bytes:
    """Trim + join clips into ONE in-memory WAV, played in a single call (no stutter)."""
    pieces, sr = [], 24000
    for i, path in enumerate(paths):
        x, sr = sf.read(str(path), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        if i:
            pieces.append(np.zeros(int(GAP_S * sr), np.float32))
        pieces.append(_trim(x, sr))
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(pieces), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class Pyttsx3Backend:
    """Offline OS voice (SAPI5 on Windows). Fast, but an English voice reading Malay —
    fine for frequency testing, not the final Malay voice. MUST be created and driven on
    the worker thread: SAPI5 is a COM object with thread affinity (creating it on one
    thread and calling it from another is what made it speak once then wedge)."""
    name = "os (pyttsx3)"

    def __init__(self, rate: int = 170, voice: str = "auto"):
        self.rate = rate
        self.voice = voice                # 'auto' = a Malay voice if installed; or a name part
        self._engine = None

    def init(self):
        try:
            import comtypes
            comtypes.CoInitialize()   # COM must be initialised on THIS (worker) thread
        except Exception:
            pass
        import pyttsx3
        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", self.rate)
        v = pick_voice(self._engine.getProperty("voices"), self.voice)
        if v is not None:
            self._engine.setProperty("voice", v.id)
        print(f"[voice] OS voice: {v.name if v is not None else 'system default'}")

    def speak(self, text: str, decision=None):
        self._engine.say(text)
        self._engine.runAndWait()

    speak_free = speak


def pick_voice(voices, want: str):
    """'auto' -> the first Malay voice (ms / Malay / Melayu) or None (= system default);
    anything else -> the first voice whose name or id contains it (case-insensitive)."""
    def text(v):
        return f"{v.name} {v.id} {' '.join(map(str, v.languages or []))}".lower()
    if (want or "auto").lower() == "auto":
        return next((v for v in voices if any(k in text(v) for k in ("ms-my", "ms_my", "malay", "melayu"))), None)
    return next((v for v in voices if want.lower() in text(v)), None)


class ClipCacheBackend:
    """Malay voice (default) via pre-rendered clips in voice_cache/<speaker>.

    Segment-first: a Decision decomposes into two phrase clips ("orang di kiri" +
    "2 meter, elak ke kanan"), trimmed and joined into one buffer before playing.
    Falls back to a whole-line clip if one exists, else speaks the OS voice now and spawns
    render_clips.py in a SEPARATE process (own RAM + threads — the beta never holds the
    1.2 GB model, YOLO stays fast) to render the missing pieces for next time.
    Run render_clips.py --prewarm once to start fully warm."""
    name = "malay cached clips (husein)"

    def __init__(self, speaker: str = "husein", rate: int = 170, segmenter=None, os_voice="auto"):
        self.speaker = speaker
        self.segmenter = segmenter            # Decision -> [segment texts] | None
        self._os = Pyttsx3Backend(rate, os_voice)
        self._todo: list[str] = []
        self._queued: set[str] = set()
        self._proc = None

    def init(self):
        self._os.init()

    def speak(self, text: str, decision=None):
        # 1) Segment path: all pieces cached -> play them in sequence (pure Husein).
        segs = self.segmenter(decision) if (decision is not None and self.segmenter) else None
        if segs:
            paths = [clip_path(self.speaker, s) for s in segs]
            if all(p.exists() for p in paths) and winsound is not None:
                winsound.PlaySound(joined_wav(tuple(paths)), winsound.SND_MEMORY)
                self._pump()
                return
            self._queue(*[s for s, p in zip(segs, paths) if not p.exists()])
        # 2) Whole-line clip (e.g. rendered before segments existed).
        path = clip_path(self.speaker, text)
        if path.exists() and winsound is not None:
            winsound.PlaySound(joined_wav((path,)), winsound.SND_MEMORY)
            self._pump()
            return
        # 3) Nothing cached -> OS voice now; render the line (or its segments, queued above).
        self._os.speak(text)
        if not segs:
            self._queue(text)
        self._pump()

    def speak_free(self, text: str):
        """Free text (Loop B answers): the OS voice, never queued for a Husein render —
        descriptions are one-offs, rendering each would cost ~1 min of CPU for nothing."""
        self._os.speak(text)

    def _queue(self, *texts: str):
        for t in texts:
            if t not in self._queued:
                self._queued.add(t)
                self._todo.append(t)
                print(f"[voice] queued for {self.speaker} render: {t!r}")

    def _pump(self):
        # One renderer at a time (each holds ~1.2 GB). Drain the whole queue into a single
        # subprocess so the model loads once per batch, not once per clip.
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self._todo:
            return
        texts = self._todo[:]
        self._todo.clear()
        self._proc = subprocess.Popen(
            [sys.executable, str(HERE / "render_clips.py"), "--speaker", self.speaker, *texts],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class Speaker:
    """Drives a TTS backend on ONE dedicated thread, one line at a time. A newer line
    replaces a queued one, so stale warnings are never spoken. Swap `backend` to change
    the voice — this is the seam a Malay engine (Dia / MeloTTS) plugs into."""

    def __init__(self, backend):
        self.backend = backend
        self.busy = False
        self._lock = threading.Lock()
        self._pending = None
        self._free = None                 # Loop B answer waiting to be spoken
        self.ended_at = 0.0               # when the last line finished playing
        threading.Thread(target=self._loop, daemon=True).start()

    def quiet_s(self) -> float:
        """Seconds of silence since the last line FINISHED (0 while speaking or a line waits)."""
        if self.busy or self._pending or self._free:
            return 0.0
        return time.time() - self.ended_at

    def say(self, text: str, interrupt: bool = False, decision=None, free: bool = False):
        # 'interrupt' is approximated by latest-wins. We NEVER touch the engine from the
        # caller thread — cross-thread engine calls are exactly what corrupted SAPI5.
        with self._lock:
            if free:
                self._free = text         # its own slot: a newer hazard line can't replace it
            else:
                self._pending = (text, decision)

    def _next(self):
        """Urgent hazard first, then a waiting Loop B answer, then routine hazard lines."""
        with self._lock:
            p, f = self._pending, self._free
            if p and p[1] is not None and p[1].urgency == "high":
                self._pending = None
                return p, False
            if f:
                self._free = None
                return (f, None), True
            self._pending = None
            return p, False

    def _loop(self):
        try:
            self.backend.init()          # create + init the engine ON this thread
        except Exception as e:
            print(f"[LENSA] voice backend '{getattr(self.backend, 'name', '?')}' init failed: {e}")
            return
        while True:
            time.sleep(0.03)
            item, free = self._next()
            if not item:
                continue
            text, decision = item
            self.busy = True
            try:
                if free:
                    self.backend.speak_free(text)
                else:
                    self.backend.speak(text, decision)
            except Exception as e:
                print(f"[LENSA] speak error: {e}")
            self.ended_at = time.time()
            self.busy = False


class SpeechGate:
    """Decides whether THIS line should be spoken NOW: a gap of silence between lines, a longer
    repeat interval for the same MEANING, stay quiet on an unchanged scene, and let high urgency
    cut in. Since 2026-10-22 (phone test: it never stopped talking): lines are compared by meaning
    (object, side, action, distance band — see meaning_key), not text, so '1.8 m'/'2.0 m' jitter
    is not news; and every gap is timed from when the voice went QUIET, not when it started.
    Tune it in config/audio_output_style.yaml."""

    def __init__(self, style, bands, quiet_s=None):
        self.s = style
        self.bands = bands
        self.quiet_s = quiet_s or (lambda: float("inf"))   # silence since speech ended
        self.last_key = None
        self.last_line = ""
        self.last_at = 0.0
        self.blocked_incident = None   # IDs of the blockage last announced
        self.blocked_said = 0          # times "Laluan tersekat" was said for it

    def wait_remaining(self, decision, high: bool) -> float:
        same = meaning_key(decision, self.bands) == self.last_key
        since = min(time.time() - self.last_at, self.quiet_s())
        if same and high:
            # an urgent line may cut in when NEW, but repeating it always waits — otherwise
            # speak_when_scene_unchanged=true re-fired "Berhenti!" every single frame
            return max(0.0, self.s.repeat_high_urgency_every_s - since)
        if same and not self.s.speak_when_scene_unchanged:
            return max(0.0, self.s.repeat_normal_every_s - since)
        if not high:
            return max(0.0, self.s.min_gap_between_lines_s - since)
        return 0.0

    def should_speak(self, line: str, high: bool, decision=None) -> bool:
        if (decision is not None and decision.action == "blocked"
                and decision.incident == self.blocked_incident
                and self.blocked_said >= self.s.blocked_max_repeats):
            return False   # same blockage already announced enough — stay quiet
        return bool(line) and self.wait_remaining(decision, high) <= 0.0

    def mark(self, line: str, decision=None):
        self.last_line = line
        self.last_key = meaning_key(decision, self.bands)
        self.last_at = time.time()
        if decision is not None and decision.action == "blocked":
            if decision.incident != self.blocked_incident:   # new set of objects -> new incident
                self.blocked_incident, self.blocked_said = decision.incident, 0
            self.blocked_said += 1


def draw_loopb_hud(frame, loopb):
    """Top-left strip: Loop B status / last answer (wrapped), so you can read what was said."""
    text = "Loop B: asking Gemini..." if loopb.busy else loopb.last
    if not text:
        return
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if len(cur) + len(wd) > 70:
            lines.append(cur)
            cur = ""
        cur = f"{cur} {wd}".strip()
    lines = (lines + [cur])[:4]
    cv2.rectangle(frame, (0, 36), (min(frame.shape[1], 620), 44 + 22 * len(lines)), (40, 20, 40), -1)
    for i, ln in enumerate(lines):
        cv2.putText(frame, ln, (8, 56 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 255), 1)


def draw_speech_hud(frame, gate, line, decision, spoke_now, speaking):
    w = frame.shape[1]
    x = w - 240
    cv2.rectangle(frame, (x - 12, 36), (w, 152), (25, 25, 25), -1)
    if speaking:
        status, col = "SPEAKING", (0, 220, 255)
    elif spoke_now:
        status, col = "SPOKE", (0, 220, 0)
    else:
        status, col = "idle", (150, 150, 150)
    cv2.putText(frame, f"voice: {status}", (x, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    high = decision.urgency == "high"
    wait = gate.wait_remaining(decision, high) if line else 0.0
    since = (time.time() - gate.last_at) if gate.last_at else 0.0
    cv2.putText(frame, f"next in : {wait:4.1f}s", (x, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1)
    cv2.putText(frame, f"last    : {since:4.1f}s ago", (x, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1)
    cv2.putText(frame, f"gap>{gate.s.min_gap_between_lines_s:.0f}s  repeat>{gate.s.repeat_normal_every_s}s",
                (x, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)


def make_backend(name: str | None, style, segmenter=None):
    """Pick a TTS backend. Default comes from config (audio_output_style.yaml: voice).
    'husein'/'idayu' = Malay cached clips; 'os' = the OS voice (fast, English-accented)."""
    rate = int(170 * style.speaking_rate)
    name = (name or style.voice or "husein").lower()
    if name in ("husein", "idayu"):
        return ClipCacheBackend("idayu" if name == "idayu" else "husein", rate, segmenter,
                                style.dynamic_voice)
    try:
        import pyttsx3  # noqa: F401
        return Pyttsx3Backend(rate, style.dynamic_voice)
    except Exception as e:
        print(f"[LENSA] no OS voice available ({e}); run 'pip install pyttsx3'")
        return None


def open_source(src: str):
    if src.isdigit():
        cap = cv2.VideoCapture(int(src), cv2.CAP_DSHOW)  # DSHOW = fast open on Windows
        return "cam", cap
    ext = Path(src).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        return "image", cv2.imread(src)
    return "video", cv2.VideoCapture(src)


def main():
    ap = argparse.ArgumentParser(description="LENSA desktop beta (YOLO26n + Loop-A logic)")
    ap.add_argument("--model", default=str(Path(__file__).parent / "models" / "yolo26n.pt"))
    ap.add_argument("--hazard-model", default=str(HAZARD_MODEL),
                    help="second detector for road hazards (train_hazards.py); 'none' to disable")
    ap.add_argument("--source", default="0", help="0=webcam, or a video/image path")
    ap.add_argument("--lang", default="ms", choices=["ms", "en"])
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--no-distance", action="store_true", help="disable the monocular distance estimate")
    ap.add_argument("--no-speak", action="store_true", help="disable text-to-speech (voice is ON by default)")
    ap.add_argument("--no-map", action="store_true", help="disable the top-down pacman map window")
    ap.add_argument("--voice", default=None,
                    help="override config voice: 'husein' | 'idayu' (Malay cached clips) | 'os' (OS voice)")
    args = ap.parse_args()

    print(f"[LENSA] loading model: {args.model}")
    models = [("", YOLO(args.model))]
    if args.hazard_model != "none" and Path(args.hazard_model).exists():
        print(f"[LENSA] loading hazard model: {args.hazard_model}")
        models.append(("r", YOLO(args.hazard_model)))   # own tracker -> IDs shown as #r1, #r2...
    elif args.hazard_model != "none":
        print(f"[LENSA] no hazard model at {args.hazard_model} (run train_hazards.py) — COCO only")
    content = Content.load()
    agent = RulesNavigationAgent(content, lang=args.lang)
    gate = SpeechGate(content.audio, content.hazard.bands)

    speaker = None
    if not args.no_speak:
        # Segmenter reads `content` at call time, so hot-reloaded labels apply to segments too.
        backend = make_backend(args.voice, content.audio,
                               segmenter=lambda dec: decision_segments(dec, content, args.lang))
        if backend is not None:
            speaker = Speaker(backend)

    if speaker:
        gate.quiet_s = speaker.quiet_s

    def loopb_say(text, wait_clip=False):
        # "Sebentar" goes the normal way (a cached Husein clip); answers are free text.
        if speaker:
            speaker.say(text, free=not wait_clip)
    loopb = LoopB(args.lang, content.phrases(args.lang), loopb_say)

    kind, cap = open_source(args.source)
    if kind != "image" and (cap is None or not cap.isOpened()):
        print(f"[LENSA] could not open source '{args.source}'. Try --source with a video/image path.")
        return
    if kind == "image" and cap is None:
        print(f"[LENSA] could not read image '{args.source}'.")
        return

    t_prev = time.time()
    fps = 0.0
    win = "LENSA beta — q quit · d describe · r read sign"
    cfg_stamp = content_stamp()
    last_cfg_check = time.time()
    tracks = Tracks()

    while True:
        # Hot-reload: pick up edits to assets/context/**.yaml (~2 s) without restarting.
        if time.time() - last_cfg_check > 2.0:
            last_cfg_check = time.time()
            try:
                st = content_stamp()
                if st != cfg_stamp:
                    cfg_stamp = st
                    content = Content.load()
                    agent = RulesNavigationAgent(content, lang=args.lang)
                    gate.s, gate.bands = content.audio, content.hazard.bands
                    loopb.lib = content.phrases(args.lang)
                    print("[config] reloaded assets/context (voice/speaking_rate changes need a restart)")
            except Exception as e:
                print(f"[config] reload failed, keeping previous config: {e}")

        if kind == "image":
            frame = cap.copy()
        else:
            ok, frame = cap.read()
            if not ok:
                break
        raw = frame.copy()          # clean frame (no boxes drawn) for Loop B
        h, w = frame.shape[:2]

        # track() = predict() + ByteTrack: every box keeps the same ID across frames.
        # Each model has its own tracker, so its IDs get a prefix ("r" = road hazard model).
        boxes = []
        for prefix, model in models:
            res = model.track(frame, conf=args.conf, imgsz=args.imgsz, persist=True,
                              tracker="bytetrack.yaml", verbose=False)[0]
            names = res.names
            ids = res.boxes.id.int().tolist() if res.boxes.id is not None else [None] * len(res.boxes)
            for b, tid in zip(res.boxes, ids):
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                cls = names[int(b.cls[0])].lower().replace(" ", "_")   # -> object_catalog yolo_class
                if cls not in content.objects:   # not in your catalog -> ignored, drawn grey
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), COL_IGNORED, 1)
                    continue
                boxes.append((x1, y1, x2, y2, float(b.conf[0]), cls,
                              None if tid is None else f"{prefix}{tid}"))

        dets = []
        tracks.tick()
        for x1, y1, x2, y2, conf, cls, tid in dedupe(boxes):
            cx = ((x1 + x2) / 2) / w
            # ponytail: box height only works for UPRIGHT things. Flat ground hazards (pothole,
            # drain...) get "unknown" distance until the ground-plane estimate exists.
            ground = content.objects.is_ground(cls)
            raw_dist = None if (args.no_distance or ground) else estimate_distance_m((y2 - y1) / h)
            pos, dist, spoken = tracks.update(tid, cx, raw_dist)
            ok = tracks.confirmed(tid, content.hazard.confirm_frames)   # not seen long enough yet?
            if ok:
                dets.append(Detection(cls, conf, dist, pos, cx=cx, width_n=(x2 - x1) / w,
                                      spoken_m=spoken, track_id=tid))

            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), COL_BOX if ok else COL_IGNORED, 2)
            tag = ("" if tid is None else f"#{tid} ") + content.objects.label(cls, args.lang)
            if dist is not None:
                tag += f" ~{dist:.1f}m est"
            cv2.putText(frame, tag, (int(x1), max(14, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, COL_BOX, 2)
        tracks.prune()

        decision, line = agent.run(dets)

        if not args.no_map:   # the agent's own map (refreshes every pacman_map.REFRESH_S)
            img = agent.map.img.copy()
            cv2.putText(img, f"next scan {agent.map.remaining_s():.0f}s", (img.shape[1] - 150, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
            cv2.imshow("LENSA map (top-down)", img)

        # thirds guides (the left/centre/right zones)
        cv2.line(frame, (w // 3, 0), (w // 3, h), (60, 60, 60), 1)
        cv2.line(frame, (2 * w // 3, 0), (2 * w // 3, h), (60, 60, 60), 1)

        # guidance banner
        col = banner_colour(decision)
        cv2.rectangle(frame, (0, h - 60), (w, h), (30, 30, 30), -1)
        cv2.rectangle(frame, (0, h - 60), (10, h), col, -1)
        cv2.putText(frame, line if line else "-", (22, h - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)

        # fps
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(1e-6, now - t_prev))
        t_prev = now
        cv2.putText(frame, f"{fps:4.1f} fps  |  {decision.action}", (22, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

        high = decision.urgency == "high"
        spoke_now = False
        if gate.should_speak(line, high, decision):
            gate.mark(line, decision)
            print(f"[{decision.urgency:6}] {line}")
            if speaker:
                speaker.say(line, interrupt=high and content.audio.interrupt_on_high_urgency,
                            decision=decision)
            spoke_now = True
        draw_speech_hud(frame, gate, line, decision, spoke_now, speaker.busy if speaker else False)
        draw_loopb_hud(frame, loopb)

        cv2.imshow(win, frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("d"):
            loopb.describe(raw)
        elif key == ord("r"):
            loopb.read_sign(raw)

    if kind != "image":
        cap.release()
    cv2.destroyAllWindows()


def self_test():
    # side dead-band: 0.34 is past the 1/3 line but inside the margin -> stays "left"
    assert position_of(0.34) == "center" and position_of(0.34, "left") == "left"
    assert position_of(0.40, "left") == "center"          # clearly past -> switches
    # spoken metres held through 2.49 <-> 2.51 jitter
    t = Tracks()
    said = {t.update(7, 0.5, d)[2] for d in (2.4, 2.6, 2.45, 2.55, 2.6)}
    assert said == {2}, said
    # same object flagged twice -> one box; a separate object survives
    a, dup, other = (0, 0, 10, 10, .9, "chair", 1), (1, 0, 11, 10, .6, "bench", 2), (50, 0, 60, 10, .8, "chair", 3)
    assert dedupe([dup, a, other]) == [a, other]
    # clip join: trimmed, one buffer, shorter than the raw clips back-to-back
    clips = sorted((HERE / "voice_cache" / "husein").glob("*.wav"))[:2]
    if len(clips) == 2:
        raw = sum(sf.info(str(c)).duration for c in clips)
        x, sr = sf.read(io.BytesIO(joined_wav(tuple(clips))))
        assert len(x) / sr < raw, (len(x) / sr, raw)
    # "Laluan tersekat" said at most blocked_max_repeats times per incident (set of IDs)
    from lensa_rules import Content, Decision
    g = SpeechGate(Content.load().audio, Content.load().hazard.bands)
    inc_a = Decision("blocked", "high", incident=frozenset({1, 2}))
    said = 0
    for _ in range(6):
        g.last_at = 0.0                      # pretend the repeat interval has passed
        if g.should_speak("Berhenti! Laluan tersekat", True, inc_a):
            g.mark("Berhenti! Laluan tersekat", inc_a)
            said += 1
    assert said == g.s.blocked_max_repeats, said
    g.last_at = 0.0
    assert g.should_speak("Berhenti! Laluan tersekat", True,
                          Decision("blocked", "high", incident=frozenset({1, 3}))), "new incident"
    # meaning, not text: distance jitter is not news; a new side/action is; gaps start after speech
    from lensa_rules import Decision as D
    g2 = SpeechGate(Content.load().audio, Content.load().hazard.bands, quiet_s=lambda: 0.5)
    a18 = D("steer_right", "medium", "person", "left", 1.8, avoid="right")
    g2.mark("orang di kiri, 2 meter, elak ke kanan", a18)
    g2.last_at = 0.0
    assert not g2.should_speak("orang di kiri, 2 meter, elak ke kanan", False,
                               D("steer_right", "medium", "person", "left", 2.3, avoid="right"))
    new = D("steer_left", "medium", "person", "right", 2.0, avoid="left")
    assert not g2.should_speak("x", False, new), "only 0.5 s of silence: must wait for the 2 s gap"
    g2.quiet_s = lambda: 2.5
    assert g2.should_speak("x", False, new)
    # confirm before speaking: seen 3 frames in a row
    t2 = Tracks()
    seen = []
    for _ in range(3):
        t2.tick(); t2.update("9", 0.5, 2.0); seen.append(t2.confirmed("9", 3))
    assert seen == [False, False, True], seen
    t2.tick(); t2.tick(); t2.update("9", 0.5, 2.0)            # missed a frame -> start again
    assert not t2.confirmed("9", 3)
    # speaker order: urgent hazard > waiting Loop B answer > routine hazard line
    from types import SimpleNamespace as NS
    sp = Speaker.__new__(Speaker)
    sp._lock, sp._pending, sp._free = threading.Lock(), None, None
    sp.say("orang di kiri", decision=NS(urgency="low"))
    sp.say("Ada kedai di kiri.", free=True)
    sp.say("Berhenti! kereta di hadapan", decision=NS(urgency="high"))
    order = [sp._next()[0][0] for _ in range(2)]
    assert order == ["Berhenti! kereta di hadapan", "Ada kedai di kiri."], order
    sp.say("orang di kiri", decision=NS(urgency="low")); sp.say("jawapan", free=True)
    assert sp._next()[0][0] == "jawapan"            # a routine line can't push the answer out
    # voice picker: Malay when installed, else default; or by name
    V = lambda name, id_, lang: NS(name=name, id=id_, languages=[lang])
    vs = [V("Microsoft Zira", "EN-US_ZIRA", "en-US"), V("Microsoft Rizwan", "MS-MY_RIZWAN", "ms-MY")]
    assert pick_voice(vs, "auto").name == "Microsoft Rizwan"
    assert pick_voice(vs[:1], "auto") is None and pick_voice(vs, "zira").name == "Microsoft Zira"
    # tracker IDs persist across frames (Ultralytics' bundled sample image)
    import ultralytics
    img = cv2.imread(str(Path(ultralytics.__file__).parent / "assets" / "bus.jpg"))
    model = YOLO(str(HERE / "models" / "yolo26n.pt"))
    ids = [model.track(img, persist=True, tracker="bytetrack.yaml", verbose=False)[0].boxes.id
           for _ in range(3)]
    assert ids[-1] is not None and ids[-1].tolist() == ids[-2].tolist(), ids
    print(f"OK — beta self-test passed (dead-band, held metres, dedupe, clip join, meaning gate, confirm, speaker order, voice pick, IDs {ids[-1].int().tolist()})")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main()
