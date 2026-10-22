"""Render Malay voice clips (Malaysian-TTS-0.6B) into the beta's cache.

Runs as its OWN process on purpose: it needs single-threaded OpenMP and ~1.2 GB RAM, and
keeping it out of the live beta keeps YOLO fast. The beta spawns this per missed line;
run --prewarm once so the common phrases play in Husein's voice from the first frame.

  python desktop-beta/render_clips.py --prewarm             # phrase inventory (see segment_lines)
  python desktop-beta/render_clips.py "Berhenti! kereta di hadapan"   # specific line(s)
  python desktop-beta/render_clips.py --speaker idayu --prewarm       # other voice
  python desktop-beta/render_clips.py --prewarm --force               # re-render (e.g. after a
                                                                      # pronounce: edit)

Clips land in voice_cache/<speaker>/<hash>.wav.
"""
import argparse
import math
import re
import time
from pathlib import Path

import soundfile as sf

from clip_cache import cache_dir_for, clip_key
from lensa_rules import Content

MS_NUMBERS = {1: "satu", 2: "dua", 3: "tiga", 4: "empat", 5: "lima", 6: "enam", 7: "tujuh",
              8: "lapan", 9: "sembilan", 10: "sepuluh"}


def tts_text(text: str, content: Content) -> str:
    """What the TTS is actually GIVEN for a clip. The cache key and the on-screen text stay
    as written ("2 meter"); only the voice input changes: digits -> Malay words ("dua") and
    the pronounce: respellings from phrase_library_ms.yaml (e.g. how to say "meter")."""
    text = re.sub(r"\b(10|[1-9])\b", lambda m: MS_NUMBERS[int(m.group(1))], text)
    for word, say in content.phrases_ms.pronounce.items():
        text = re.sub(rf"\b{re.escape(word)}\b", say, text)
    return text


def live_classes() -> set:
    """Classes the current detectors can actually fire (no point voicing the rest yet)."""
    from ultralytics import YOLO
    models = Path(__file__).parent / "models"
    out = set()
    for pt in (models / "yolo26n.pt", models / "road_hazard_yolo26n.pt"):
        if pt.exists():
            out |= {n.lower().replace(" ", "_") for n in YOLO(str(pt)).names.values()}
    return out


def segment_lines(content: Content) -> list[str]:
    """The phrase inventory decision_segments() joins into every standard sentence:

      head  "{objek} di {arah}"            live catalog objects x (kiri|kanan|hadapan)
      tail  "{n} meter, elak ke {elak}"    n = 1..notice band x (kiri|kanan)
            "{n} meter" / "elak ke {elak}"
      + fixed words: clear line, Berhenti!, Hati-hati, Laluan tersekat, Awas!

    Objects the stock model can't detect (stairs, drain...) are skipped until the custom
    model exists; they still render on demand the first time one is spoken."""
    ms = content.phrases_ms
    segs = [ms.render("clear"), "Berhenti!", "Hati-hati", "Laluan tersekat", ms.render("loopb_wait")]
    hi = ms.urgency_prefix.get("high", "").strip()
    if hi:
        segs.append(hi)
    sides = [ms.direction("left"), ms.direction("right")]
    for n in range(1, math.ceil(content.hazard.bands.notice_below_m) + 1):
        segs.append(f"{n} meter")
        segs += [f"{n} meter, elak ke {s}" for s in sides]
    segs += [f"elak ke {s}" for s in sides]
    live = live_classes()
    for cls in content.objects.by_class:
        if cls in live and content.objects.danger_weight(cls) > 0:
            label = content.objects.label(cls, "ms")
            segs += [f"{label} di {ms.direction(pos)}" for pos in ("left", "right", "center")]
    return list(dict.fromkeys(s for s in segs if s))


def main():
    ap = argparse.ArgumentParser(description="Render Malay clips into the beta's voice cache")
    ap.add_argument("texts", nargs="*", help="specific line(s) to render")
    ap.add_argument("--speaker", default="husein", help="husein | idayu | ...")
    ap.add_argument("--prewarm", action="store_true",
                    help="render the phrase inventory (full sentence coverage)")
    ap.add_argument("--force", action="store_true", help="re-render clips that already exist")
    args = ap.parse_args()

    content = Content.load()
    texts = list(args.texts)
    if args.prewarm:
        texts += segment_lines(content)
    texts = [t for t in dict.fromkeys(texts) if t.strip()]
    cache = cache_dir_for(args.speaker)
    todo = [t for t in texts if args.force or not (cache / f"{clip_key(t)}.wav").exists()]
    if not todo:
        print("[render] nothing to do (all cached)")
        return
    print(f"[render] {len(todo)} clip(s) to render, ~{len(todo) * 35 // 60} min on CPU. Ctrl+C-safe (resumes).")

    from husein_tts import HuseinTTS, SAMPLE_RATE  # heavy import only when there is work
    tts = HuseinTTS(args.speaker).load()
    for i, text in enumerate(todo, 1):
        t0 = time.time()
        path = cache / f"{clip_key(text)}.wav"
        tmp = path.with_suffix(".tmp")
        # format="WAV" is required: soundfile can't infer it from a ".tmp" extension.
        sf.write(str(tmp), tts.synth(tts_text(text, content)), SAMPLE_RATE, subtype="PCM_16", format="WAV")
        tmp.replace(path)  # atomic -> the beta never plays a half-written file
        print(f"[render] {i}/{len(todo)} ({time.time() - t0:.0f}s): {text}  [said: {tts_text(text, content)}]")
    print(f"[render] DONE -> {cache}")


if __name__ == "__main__":
    main()
