"""Parity cases: run the desktop beta's Python logic on fixed scenes and write the exact answers to
app/src/test/resources/golden.json. The Kotlin unit test (GoldenParityTest) must reproduce every
one — that is how we know the phone says the same thing as the beta.

    python android-app/make_golden.py      (re-run after changing lensa_rules.py / pacman_map.py)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop-beta"))
from lensa_rules import Content, Detection, RulesNavigationAgent, decision_segments, meaning_key  # noqa: E402
import pacman_map as pm  # noqa: E402


def det(cls, dist, cx, w=0.15, conf=0.9, tid=None, spoken=None):
    pos = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"
    return Detection(cls, conf, dist, pos, cx=cx, width_n=w, track_id=tid, spoken_m=spoken)


SCENES = {   # name -> list of FRAMES (each a list of detections), run on ONE fresh agent
    "empty": [[]],
    "car stop": [[det("car", 0.9, 0.5)]],
    "person 2m centre": [[det("person", 2.0, 0.5)]],
    "person 2m left": [[det("person", 2.0, 0.17)]],
    "person 5m left": [[det("person", 5.0, 0.17)]],
    "low confidence": [[det("car", 0.9, 0.5, conf=0.2)]],
    "not in catalog": [[det("not_a_class", 2.0, 0.5)]],
    "A chair C + chair L": [[det("chair", 2.0, 0.5), det("chair", 2.5, 0.17)]],
    "C leans left": [[det("person", 2.0, 0.40, 0.2)]],
    "C leans right": [[det("person", 2.0, 0.60, 0.2)]],
    "D all blocked": [[det("person", 2.0, 0.5, 0.25), det("chair", 2.0, 0.17), det("chair", 2.0, 0.83)]],
    "map gap": [[det("person", 2.8, 0.45, 0.08), det("chair", 2.8, 0.12), det("chair", 2.8, 0.88)]],
    "far bottle": [[det("person", 2.0, 0.68, 0.64), det("bottle", 5.4, 0.14, 0.12)], [det("person", 2.0, 0.68, 0.64)]],
    "pothole ahead": [[det("pothole", None, 0.5)]],
    "pothole left": [[det("pothole", None, 0.17)]],
    "open manhole right": [[det("manhole_open", None, 0.85)]],
    "open manhole ahead": [[det("manhole_open", None, 0.5)]],
    "broken cover right": [[det("manhole_broken", None, 0.85)]],
    "intact cover": [[det("manhole_intact", None, 0.5)]],
    "held metres": [[det("person", 2.6, 0.17, spoken=2)]],
    "far car 7m": [[det("car", 7.0, 0.5)]],
    "car 2.5m + person 2m": [[det("car", 2.5, 0.85), det("person", 2.0, 0.17)]],
    "hysteresis flicker": [[det("person", 2.0, 0.45, 0.1)], [det("person", 2.0, 0.55, 0.1)],
                           [det("person", 2.0, 0.55, 0.1)], [det("person", 2.0, 0.55, 0.1)]],
    "held side blocked": [[det("person", 2.0, 0.45, 0.1)],
                          [det("person", 2.0, 0.55, 0.1), det("chair", 2.0, 0.85)]],
    "stale empty map": [[], [det("person", 2.0, 0.5, 0.25), det("chair", 2.0, 0.17), det("chair", 2.0, 0.83)]],
}


def scene_out(frames, lang):
    c = Content.load()
    a = RulesNavigationAgent(c, lang)
    out = []
    for f in frames:
        dec, line = a.run(f)
        out.append({"action": dec.action, "urgency": dec.urgency, "avoid": dec.avoid,
                    "line": line, "segments": decision_segments(dec, c, lang),
                    "key": list(meaning_key(dec, c.hazard.bands))})
    return out


def frames_json(frames):
    return [[{"cls": d.yolo_class, "conf": d.confidence, "dist": d.distance_m, "cx": d.cx,
              "w": d.width_n, "tid": d.track_id, "spoken": d.spoken_m} for d in f] for f in frames]


MAPS = {   # name -> hits (x_m, d_m, w_m)
    "two ghosts": [(0.3, 2.0, 0.8), (-0.8, 5.0, 0.8)],
    "person 2m": [(0.0, 2.0, 0.5)],
    "chair right": [(0.6, 1.5, 0.5)],
    "wall": [(x / 10.0, 1.0, 1.0) for x in range(-30, 31, 5)],
    "empty": [],
}

gold = {"scenes": [], "maps": []}
for name, frames in SCENES.items():
    for lang in ("ms", "en"):
        gold["scenes"].append({"name": name, "lang": lang, "frames": frames_json(frames),
                               "expect": scene_out(frames, lang)})
for name, hits in MAPS.items():
    g, _ = pm.paint([(x, d, w, "x") for x, d, w in hits])
    path = pm.bfs_deepest(g | pm.UNSEEN)
    gold["maps"].append({"name": name, "hits": hits, "path": [list(p) for p in path],
                         "turn": pm.first_turn(path)})

# Loop B (online): prompts + sign lines must match desktop-beta/loop_b.py exactly
import loop_b as lb  # noqa: E402
from lensa_rules import _load  # noqa: E402
rules = _load("rules/ocr_reading_rules.yaml")
c = Content.load()
gold["loopb"] = {
    "prompts": {f"{kind}_{lang}": (lb.describe_prompt(lang) if kind == "describe" else lb.read_prompt(lang, rules))
                for kind in ("describe", "read") for lang in ("ms", "en")},
    "signs": [{"raw": raw, "lang": lang, "line": lb.sign_line(raw, lang, rules, c.phrases(lang))}
              for raw in ("NONE", " none. ", "", "  DILARANG   MASUK ", "Bilik 3.14" + chr(10) + "Makmal Komputer",
                          "AWAS " * 40, "X" * 120)
              for lang in ("ms", "en")],
}

out = HERE / "app" / "src" / "test" / "resources" / "golden.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {out} — {len(gold['scenes'])} scene runs, {len(gold['maps'])} maps")
