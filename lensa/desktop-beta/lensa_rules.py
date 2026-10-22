"""LENSA deterministic navigation logic (Loop A) — desktop port.

Faithful port of the app's rules engine. Reads the SAME content files under
`../assets/context/**` (plain .yaml), so this beta and the Android app share ONE
behaviour spec. Tune the .yaml files (object_catalog, hazard_priority_rules,
phrase_library_*), not this code — the beta hot-reloads them while running.

Run `python lensa_rules.py` to execute the built-in self-test.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import yaml

from pacman_map import PacmanMapper, scene_key

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "context"

# Files the beta loads; also polled by the beta for hot-reload.
CONTENT_FILES = [
    "rules/object_catalog.yaml",
    "rules/hazard_priority_rules.yaml",
    "language/phrase_library_ms.yaml",
    "language/phrase_library_en.yaml",
    "config/audio_output_style.yaml",
]


def _load(rel: str) -> dict:
    data = yaml.safe_load((_ASSETS / rel).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def content_stamp() -> tuple:
    """mtimes of the content files — compare snapshots to detect edits (hot-reload)."""
    return tuple((_ASSETS / f).stat().st_mtime for f in CONTENT_FILES)


# --------------------------- content models ---------------------------
@dataclass
class ObjectCatalog:
    by_class: dict

    @classmethod
    def load(cls, y: dict) -> "ObjectCatalog":
        m = {}
        for e in (y.get("objects") or []):
            c = str(e.get("yolo_class", "")).strip()
            if not c:
                continue
            m[c] = {"en": str(e.get("en", c)), "ms": str(e.get("ms", c)),
                    "danger_weight": int(e.get("danger_weight", 3)),
                    "ground": bool(e.get("ground", False))}
        return cls(m)

    def danger_weight(self, c: str) -> int:
        # The catalog is a WHITELIST: a class you removed from the yaml weighs 0, so it is
        # never announced and never blocks a path, even though YOLO still detects it.
        return self.by_class.get(c, {}).get("danger_weight", 0)

    def is_ground(self, c: str) -> bool:
        """Flat on the ground (pothole, drain...) — box height says nothing about distance."""
        return self.by_class.get(c, {}).get("ground", False)

    def __contains__(self, c: str) -> bool:
        return c in self.by_class

    def label(self, c: str, lang: str) -> str:
        i = self.by_class.get(c)
        if not i:
            return c.replace("_", " ")
        return i["en"] if lang == "en" else i["ms"]


@dataclass
class DistanceBands:
    immediate_stop_below_m: float
    caution_below_m: float
    notice_below_m: float
    ignore_above_m: float


@dataclass
class HazardRules:
    confidence_floor: float
    bands: DistanceBands
    sensitivity: str
    moving_toward_multiplier: float
    ground_alert_min_danger: int   # ground hazard this dangerous -> "Awas!" even without distance
    confirm_frames: int            # frames in a row an object must be seen before LENSA reacts

    @classmethod
    def load(cls, y: dict) -> "HazardRules":
        db = y.get("distance_bands") or {}
        def band(k, d):
            return float(db.get(k, d))
        return cls(
            confidence_floor=float(y.get("confidence_floor", 0.45)),
            bands=DistanceBands(band("immediate_stop_below_m", 1.0),
                                band("caution_below_m", 3.0),
                                band("notice_below_m", 6.0),
                                band("ignore_above_m", 6.0)),
            sensitivity=str(y.get("sensitivity", "medium")),
            moving_toward_multiplier=float(y.get("moving_toward_multiplier", 1.5)),
            ground_alert_min_danger=int(y.get("ground_alert_min_danger", 9)),
            confirm_frames=int(y.get("confirm_frames", 3)),
        )


@dataclass
class PhraseLibrary:
    directions: dict
    templates: dict
    urgency_prefix: dict
    pronounce: dict          # word -> how the TTS should SAY it (render input only)

    @classmethod
    def load(cls, y: dict) -> "PhraseLibrary":
        def sm(x):
            return {str(k): str(v) for k, v in (x or {}).items()}
        return cls(sm(y.get("directions")), sm(y.get("templates")), sm(y.get("urgency_prefix")),
                   sm(y.get("pronounce")))

    def direction(self, pos: str) -> str:
        key = {"left": "left", "right": "right", "center": "ahead"}[pos]
        return self.directions.get(key, key)

    def render(self, key, object=None, direction=None, distance=None, text=None, avoid=None) -> str:
        t = self.templates.get(key, "")
        if object is not None:
            t = t.replace("{objek}", object).replace("{object}", object)
        if direction is not None:
            t = t.replace("{arah}", direction).replace("{direction}", direction)
        if distance is not None:
            t = t.replace("{jarak}", distance).replace("{distance}", distance)
        if text is not None:
            t = t.replace("{teks}", text).replace("{text}", text)
        if avoid is not None:
            t = t.replace("{elak}", avoid).replace("{avoid}", avoid)
        return t.strip()


# --------------------------- runtime types ---------------------------
@dataclass
class Detection:
    yolo_class: str
    confidence: float
    distance_m: Optional[float]
    position: str            # 'left' | 'center' | 'right'
    moving_toward: bool = False
    cx: Optional[float] = None       # normalized image x of box centre (0..1) — used by the mapper
    width_n: Optional[float] = None  # normalized box width (0..1) — used by the mapper
    spoken_m: Optional[int] = None   # metres to SAY, held steady per tracked object (beta)
    track_id: Optional[str] = None   # tracker ID (beta) — same object keeps it across frames


@dataclass
class SceneItem:
    yolo_class: str
    distance_m: Optional[float]
    position: str
    moving_toward: bool
    cx: Optional[float] = None
    spoken_m: Optional[int] = None


@dataclass
class Decision:
    action: str              # proceed|caution|stop|blocked|steer_left|steer_right
    urgency: str             # low|medium|high
    yolo_class: Optional[str] = None
    position: Optional[str] = None       # hazard's real side (left|center|right)
    distance_m: Optional[float] = None
    avoid: Optional[str] = None          # direction to move (left|right), steer only
    spoken_m: Optional[int] = None       # metres to say (falls back to round(distance_m))
    incident: frozenset = frozenset()    # blocked only: IDs of the objects blocking the path

    def metres(self) -> Optional[int]:
        if self.distance_m is None:
            return None
        return self.spoken_m if self.spoken_m is not None else round(self.distance_m)


@dataclass
class AudioStyle:
    """Voice frequency / 'one at a time' policy. Tune in config/audio_output_style.yaml."""
    interrupt_on_high_urgency: bool
    repeat_high_urgency_every_s: int
    repeat_normal_every_s: int
    blocked_max_repeats: int
    speak_when_scene_unchanged: bool
    min_gap_between_lines_s: float
    speaking_rate: float
    voice: str
    dynamic_voice: str       # OS voice for free text (Loop B): 'auto' or part of a voice name

    @classmethod
    def load(cls, y: dict) -> "AudioStyle":
        return cls(
            interrupt_on_high_urgency=bool(y.get("interrupt_on_high_urgency", True)),
            repeat_high_urgency_every_s=int(y.get("repeat_high_urgency_every_s", 2)),
            repeat_normal_every_s=int(y.get("repeat_normal_every_s", 4)),
            blocked_max_repeats=int(y.get("blocked_max_repeats", 3)),
            speak_when_scene_unchanged=bool(y.get("speak_when_scene_unchanged", False)),
            min_gap_between_lines_s=float(y.get("min_gap_between_lines_s", 1.0)),
            speaking_rate=float(y.get("speaking_rate", 1.0)),
            voice=str(y.get("voice", "husein")),
            dynamic_voice=str(y.get("dynamic_voice", "auto")),
        )


@dataclass
class Content:
    objects: ObjectCatalog
    hazard: HazardRules
    phrases_ms: PhraseLibrary
    phrases_en: PhraseLibrary
    audio: AudioStyle

    @classmethod
    def load(cls) -> "Content":
        return cls(
            ObjectCatalog.load(_load("rules/object_catalog.yaml")),
            HazardRules.load(_load("rules/hazard_priority_rules.yaml")),
            PhraseLibrary.load(_load("language/phrase_library_ms.yaml")),
            PhraseLibrary.load(_load("language/phrase_library_en.yaml")),
            AudioStyle.load(_load("config/audio_output_style.yaml")),
        )

    def phrases(self, lang: str) -> PhraseLibrary:
        return self.phrases_en if lang == "en" else self.phrases_ms


_SENS = {"low": 0.8, "high": 1.25}
ZONES = ("left", "center", "right")
# ponytail: a box blocks a zone once it covers >=25% of that third — tune on walk tests.
ZONE_OVERLAP = 0.25
# Frames the OTHER side must win in a row before a held steer flips (~0.3 s at 10 fps).
HOLD_FRAMES = 3
ROUTE_MAX_AGE_S = 1.0  # the all-blocked fallback never trusts a map older than this


def zones_of(d: Detection) -> set:
    """Zones a detection physically occupies — its box EXTENT, not just its centre, so a
    wide object near a 1/3 line blocks both zones it covers."""
    z = {d.position}
    if d.cx is None or d.width_n is None:
        return z
    x1, x2 = d.cx - d.width_n / 2, d.cx + d.width_n / 2
    for i, name in enumerate(ZONES):
        lo, hi = i / 3, (i + 1) / 3
        if (min(x2, hi) - max(x1, lo)) >= ZONE_OVERLAP / 3:
            z.add(name)
    return z


class RulesNavigationAgent:
    def __init__(self, content: Content, lang: str = "ms", sensitivity: Optional[str] = None):
        self.c = content
        self.lang = lang
        self.sensitivity = sensitivity or content.hazard.sensitivity
        self._held = None    # steer side currently being held (hysteresis)
        self._flip = 0       # consecutive frames the other side has won
        self._calm = 0       # consecutive frames with no steer
        # One map, refreshed every pacman_map.REFRESH_S: the beta draws it, _route reads it.
        self.map = PacmanMapper(lambda c: self.c.objects.label(c, self.lang))

    def run(self, detections):
        live = [d for d in detections if d.confidence >= self.c.hazard.confidence_floor
                and self.c.objects.danger_weight(d.yolo_class) > 0]
        self.map.maybe_refresh(live)
        scene = self._interpret(live)
        blocked = self._blocked(live)
        decision = self._stabilise(self._decide(scene, blocked, live), blocked)
        if decision.yolo_class is not None:
            item = next(it for it in scene if it.yolo_class == decision.yolo_class)
            decision = replace(decision, spoken_m=item.spoken_m)
        return decision, self._language(decision)

    def _blocked(self, ds) -> set:
        """Zones occupied by any NEAR detection (within the caution band, or a ground hazard
        with no distance). Built from the raw list, NOT the merged scene — merging by class hid
        every chair but the nearest one. Near-only since 2026-10-22: a bottle 5 m away doesn't
        stop you stepping left now, and far objects flickering in/out flipped the advice."""
        lim = self.c.hazard.bands.caution_below_m
        out = set()
        for d in ds:
            if d.distance_m is None or d.distance_m <= lim:
                out |= zones_of(d)
        return out

    def _route(self, ds) -> Optional[str]:
        """All three zones blocked -> ask the top-down map for a gap the coarse thirds miss.
        Returns 'left' | 'right' (first turn of the route), 'straight' (the map finds a clear
        corridor ahead — the thirds were over-cautious) or None (no way through).
        The snapshot is retaken first if it is older than ROUTE_MAX_AGE_S or was built from a
        different set of objects — the 4 s map may predate the obstacles that blocked the
        zones (it used to answer "clear" from an empty-scene snapshot)."""
        if self.map.age_s() > ROUTE_MAX_AGE_S or self.map.scene != scene_key(ds):
            self.map.refresh(ds)
        if self.map.turn:
            return self.map.turn
        if self.map.reach_m >= self.c.hazard.bands.caution_below_m:
            return "straight"
        return None

    def _incident(self, ds) -> frozenset:
        """Which objects make up this blockage — the speech gate caps repeats per incident."""
        lim = self.c.hazard.bands.notice_below_m
        return frozenset(d.track_id for d in ds if d.track_id is not None
                         and (d.distance_m is None or d.distance_m <= lim))

    def _stabilise(self, dec: Decision, blocked: set) -> Decision:
        """Hysteresis: keep the held steer side until the other side wins HOLD_FRAMES frames
        in a row. Safety beats stability: a held side that becomes blocked drops at once."""
        if dec.avoid is None:
            self._flip = 0
            self._calm += 1
            if self._calm >= HOLD_FRAMES:
                self._held = None
            return dec
        self._calm = 0
        if self._held in (None, dec.avoid) or self._held in blocked:
            self._held, self._flip = dec.avoid, 0
            return dec
        self._flip += 1
        if self._flip >= HOLD_FRAMES:
            self._held, self._flip = dec.avoid, 0
            return dec
        return replace(dec, action=f"steer_{self._held}", avoid=self._held)

    # Layer 1 — merge detections into a scene
    # (merged per class = what to NAME; blocking uses the raw list, see _blocked)
    def _interpret(self, ds):
        merged = {}
        for d in ds:
            ex = merged.get(d.yolo_class)
            if ex is None:
                merged[d.yolo_class] = SceneItem(d.yolo_class, d.distance_m, d.position, d.moving_toward,
                                                 d.cx, d.spoken_m)
            else:
                closer = (d.distance_m if d.distance_m is not None else 1e9) < \
                         (ex.distance_m if ex.distance_m is not None else 1e9)
                merged[d.yolo_class] = SceneItem(
                    d.yolo_class,
                    d.distance_m if closer else ex.distance_m,
                    d.position if closer else ex.position,
                    ex.moving_toward or d.moving_toward,
                    d.cx if closer else ex.cx,
                    d.spoken_m if closer else ex.spoken_m,
                )
        return list(merged.values())

    # Layer 2 — pick the one thing to say + the action
    def _decide(self, items, blocked, ds):
        b = self.c.hazard.bands
        if not items:
            return Decision("proceed", "low")

        stop_item = None
        for it in items:
            if it.distance_m is not None and it.distance_m <= b.immediate_stop_below_m:
                if stop_item is None or it.distance_m < stop_item.distance_m:
                    stop_item = it
        if stop_item is not None:
            return Decision("stop", "high", stop_item.yolo_class, stop_item.position, stop_item.distance_m)

        sm = _SENS.get(self.sensitivity, 1.0)
        best, best_score = None, -1.0
        for it in items:
            f = self._proximity(it.distance_m, b)
            score = self.c.objects.danger_weight(it.yolo_class) * f * \
                (self.c.hazard.moving_toward_multiplier if it.moving_toward else 1.0) * sm
            if score > best_score:
                best_score, best = score, it
        if best is None or best_score <= 0.001:
            return Decision("proceed", "low")

        d = best.distance_m
        if d is not None and d <= b.caution_below_m:
            action, urg = "caution", "medium"
        elif d is not None and d <= b.notice_below_m:
            action, urg = "caution", "low"
        elif d is None:
            action, urg = "caution", "low"
            # ponytail: flat hazards have no distance yet (ground-plane maths needs the mounted
            # camera), so they could never reach "Berhenti". Very dangerous ones (open manhole)
            # get "Awas!" + high urgency instead. Upgrade path: real distance -> stop band.
            if (self.c.objects.is_ground(best.yolo_class) and
                    self.c.objects.danger_weight(best.yolo_class) >= self.c.hazard.ground_alert_min_danger):
                urg = "high"
        else:
            return Decision("proceed", "low")

        close = d is not None and d <= b.caution_below_m   # only side-steer for a genuinely near hazard

        # Steer AWAY from the hazard. `position` stays the hazard's real side (for phrasing);
        # `avoid` is where to move.
        avoid = None
        if best.position == "center":
            open_sides = [s for s in ("left", "right") if s not in blocked]
            if len(open_sides) == 2:
                # both open -> step away from the side the object leans toward
                avoid = "right" if best.cx is not None and best.cx < 0.5 else "left"
            elif open_sides:
                avoid = open_sides[0]
            else:
                avoid = self._route(ds)
                if avoid == "straight":   # map: corridor ahead is clear -> just announce
                    avoid = None
                elif avoid is None and close:
                    return Decision("blocked", "high", best.yolo_class, "center", d,
                                    incident=self._incident(ds))
        elif close:
            other = "right" if best.position == "left" else "left"
            if other not in blocked:
                avoid = other
            elif "center" in blocked:
                avoid = self._route(ds)
                if avoid == "straight":
                    avoid = None
                elif avoid is None:
                    return Decision("blocked", "high", best.yolo_class, best.position, d,
                                    incident=self._incident(ds))
            # else: centre is open -> just announce, keep walking straight
        if avoid is not None:
            return Decision(f"steer_{avoid}", urg, best.yolo_class, best.position, d, avoid=avoid)

        # Otherwise just announce the hazard on its real side.
        return Decision(action, urg, best.yolo_class, best.position, d)

    # Layer 3 — one short spoken line
    def _language(self, dec: Decision) -> str:
        lib = self.c.phrases(self.lang)
        obj = "" if dec.yolo_class is None else self.c.objects.label(dec.yolo_class, self.lang)
        dist = "" if dec.distance_m is None else str(dec.metres())

        if dec.action == "proceed":
            base = lib.render("clear")
        elif dec.action == "stop":
            base = lib.render("stop", object=obj)
        elif dec.action == "blocked":
            base = lib.render("blocked") or lib.render("stop", object=obj)
        elif dec.action == "caution":
            if dec.distance_m is not None:
                base = lib.render("detection", object=obj,
                                  direction=lib.direction(dec.position or "center"), distance=dist)
            else:
                key = "alert" if dec.urgency == "high" else "caution"   # "Awas!" replaces "Hati-hati"
                base = lib.render(key, object=obj, direction=lib.direction(dec.position or "center"))
        elif dec.action in ("steer_left", "steer_right"):
            avoid = dec.avoid or ("left" if dec.action == "steer_left" else "right")
            avoid_word = lib.direction(avoid)
            hazard_word = lib.direction(dec.position or "center")
            if dec.distance_m is not None:
                base = lib.render("steer_hazard", object=obj, direction=hazard_word,
                                  distance=dist, avoid=avoid_word)
            else:
                base = lib.render("steer_hazard_nodist", object=obj, direction=hazard_word, avoid=avoid_word)
            if not base:  # templates absent -> simple fallback
                base = lib.render("steer", direction=avoid_word)
        else:
            base = ""

        if dec.urgency == "high" and dec.action in ("caution", "steer_left", "steer_right"):
            p = lib.urgency_prefix.get("high", "").strip()
            if p:
                return f"{p} {base}".strip()
        return base

    def _proximity(self, d, b: DistanceBands) -> float:
        if d is None:
            return 0.2
        if d <= b.immediate_stop_below_m:
            return 1.0
        if d >= b.ignore_above_m:
            return 0.0
        return max(0.0, min(1.0, (b.ignore_above_m - d) / (b.ignore_above_m - b.immediate_stop_below_m)))


def meaning_key(dec: Decision, bands: DistanceBands) -> tuple:
    """What a line MEANS, for the speech gate: object, side, action and direction to move, plus
    the distance band. '1.8 m' vs '2.0 m' is the same meaning; moving into a closer band isn't."""
    d = dec.distance_m
    band = None if d is None else ("stop" if d <= bands.immediate_stop_below_m else
                                   "caution" if d <= bands.caution_below_m else "notice")
    return (dec.action, dec.yolo_class, dec.position, dec.avoid, band)


def decision_segments(dec: Decision, content: Content, lang: str = "ms"):
    """Break a Decision into at most TWO cached clips (plus an optional "Awas!"):

        "{objek} di {arah}"  +  "{n} meter, elak ke {elak}"
        "orang di kiri"      +  "2 meter, elak ke kanan"

    Each half is rendered as one natural phrase, so a sentence has a single join instead of
    three. The tail half is a small set: distances 1..notice band x (kiri|kanan) — see
    segment_lines() in render_clips.py. Returns None when not segmentable (dynamic text such
    as OCR, or a language without a clip inventory).

    ponytail: the frame words ("di", "meter", "elak ke", "Berhenti!", "Hati-hati") mirror the
    templates in phrase_library_ms.yaml — if you reword a template, reword these together.
    """
    if lang != "ms":
        return None
    lib = content.phrases(lang)
    obj = content.objects.label(dec.yolo_class, lang) if dec.yolo_class else None
    n = dec.metres()

    def at(pos):
        return f"{obj} di {lib.direction(pos)}"

    if dec.action == "proceed":
        return [lib.render("clear")]
    if dec.action == "stop":
        return ["Berhenti!", at("center")] if obj else ["Berhenti!"]
    if dec.action == "blocked":  # mirrors the 'blocked' template
        return ["Berhenti!", "Laluan tersekat"]

    segs = []
    if dec.urgency == "high":
        p = lib.urgency_prefix.get("high", "").strip()
        if p:
            segs.append(p)
    pos = dec.position or "center"
    if dec.action == "caution":
        if n is not None:  # mirrors the 'detection' template
            return segs + [at(pos), f"{n} meter"]
        return segs + ([at(pos)] if segs else ["Hati-hati", at(pos)])   # "Awas!" replaces "Hati-hati"
    if dec.action in ("steer_left", "steer_right"):
        avoid = lib.direction(dec.avoid or ("left" if dec.action == "steer_left" else "right"))
        tail = f"{n} meter, elak ke {avoid}" if n is not None else f"elak ke {avoid}"
        return segs + [at(pos), tail]
    return None  # 'read' etc. -> dynamic text, not segmentable


if __name__ == "__main__":
    c = Content.load()
    a = RulesNavigationAgent(c, "ms")

    def check(desc, dets, action, must_contain=(), must_not=()):
        dec, line = RulesNavigationAgent(c, "ms").run(dets)   # fresh agent: no held steer
        print(f"{desc:20} -> {dec.action:11} | {line}")
        assert dec.action == action, f"{desc}: action {dec.action} != {action}"
        for w in must_contain:
            assert w in line, f"{desc}: '{w}' missing from '{line}'"
        for w in must_not:
            assert w not in line, f"{desc}: '{w}' unexpectedly in '{line}'"

    check("car @0.9m center", [Detection("car", 0.95, 0.9, "center", True)], "stop", ["kereta"])
    check("empty scene", [], "proceed")
    check("person @2m center", [Detection("person", 0.9, 2.0, "center", False)],
          "steer_left", ["orang", "elak ke kiri"])
    # THE requested case: hazard on the LEFT, close -> steer right, naming it + distance
    check("person @2m LEFT", [Detection("person", 0.9, 2.0, "left", False)],
          "steer_right", ["orang", "kiri", "elak ke kanan"])
    # side hazard but far (beyond caution band) -> announce only, no steer
    check("person @5m LEFT", [Detection("person", 0.9, 5.0, "left", False)],
          "caution", must_not=["elak"])
    check("low confidence", [Detection("car", 0.2, 0.9, "center", True)], "proceed")

    def box(cls, dist, cx, w=0.15):
        pos = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"
        return Detection(cls, 0.9, dist, pos, cx=cx, width_n=w)

    # A: two chairs — the second one must still block its side (merge used to hide it)
    check("A chair C + chair L", [box("chair", 2.0, 0.5), box("chair", 2.5, 0.17)],
          "steer_right", ["elak ke kanan"])
    # C: centre hazard leaning left -> step right (used to always go left)
    check("C person leans left", [box("person", 2.0, 0.40, 0.2)], "steer_right", ["elak ke kanan"])
    check("C person leans right", [box("person", 2.0, 0.60, 0.2)], "steer_left", ["elak ke kiri"])
    # D: every zone blocked, no gap in view -> say so (used to steer into a chair)
    check("D all blocked", [box("person", 2.0, 0.5, 0.25), box("chair", 2.0, 0.17), box("chair", 2.0, 0.83)],
          "blocked", ["Laluan tersekat"], must_not=["elak"])
    # map fallback: all thirds blocked, but the top-down map finds the gap
    check("map gap", [box("person", 2.8, 0.45, 0.08), box("chair", 2.8, 0.12), box("chair", 2.8, 0.88)],
          "steer_right", ["elak ke kanan"])
    # phone screenshot (2026-10-22): a far bottle flickering in/out on the left must not flip
    # "elak ke kiri" <-> "Laluan tersekat" — only obstacles within the caution band block a side
    hand = box("person", 2.0, 0.68, 0.64)
    for scene in ([hand], [hand, box("bottle", 5.4, 0.14, 0.12)]):
        check("far bottle", scene, "steer_left", ["elak ke kiri"])
    # meaning key: distance jitter within a band = same meaning; a closer band = new meaning
    k = lambda d: meaning_key(RulesNavigationAgent(c, "ms").run([box("person", d, 0.17)])[0], c.hazard.bands)
    assert k(1.8) == k(2.4) and k(2.4) != k(4.0)
    # ground hazard: no distance -> named + steered without metres, two clips
    dec, line = RulesNavigationAgent(c, "ms").run([box("pothole", None, 0.5)])
    assert line == "lubang jalan di hadapan, elak ke kiri", line
    assert decision_segments(dec, c) == ["lubang jalan di hadapan", "elak ke kiri"]
    # very dangerous ground hazard, no distance -> "Awas!" + high urgency; the clips match the text
    dec, line = RulesNavigationAgent(c, "ms").run([box("manhole_open", None, 0.85)])
    assert dec.urgency == "high" and line == "Awas! lurang terbuka di kanan", line
    assert " ".join(decision_segments(dec, c)) == line, decision_segments(dec, c)
    dec, line = RulesNavigationAgent(c, "ms").run([box("manhole_open", None, 0.5)])
    assert line == "Awas! lurang terbuka di hadapan, elak ke kiri", line
    # ...but a merely-dangerous one stays a normal caution
    dec, line = RulesNavigationAgent(c, "ms").run([box("manhole_broken", None, 0.85)])
    assert dec.urgency == "low" and line.startswith("Hati-hati"), line
    # stale map: an empty-scene snapshot must not answer for a walled-in scene a frame later
    a = RulesNavigationAgent(c, "ms")
    a.run([])
    dec, line = a.run([box("person", 2.0, 0.5, 0.25), box("chair", 2.0, 0.17), box("chair", 2.0, 0.83)])
    assert dec.action == "blocked", (dec.action, line)
    # wide box straddling the 1/3 line blocks BOTH zones it covers
    assert zones_of(box("person", 2.0, 0.33, 0.3)) == {"left", "center"}

    # B: hysteresis — a one-frame flicker must not flip the held side...
    h = RulesNavigationAgent(c, "ms")
    lean_l, lean_r = [box("person", 2.0, 0.45, 0.1)], [box("person", 2.0, 0.55, 0.1)]
    assert h.run(lean_l)[0].avoid == "right"
    assert h.run(lean_r)[0].avoid == "right", "single-frame flicker flipped the steer"
    # ...but a sustained change does flip after HOLD_FRAMES
    assert [h.run(lean_r)[0].avoid for _ in range(HOLD_FRAMES)][-1] == "left"
    # ...and a held side that becomes blocked drops immediately (safety > stability)
    h2 = RulesNavigationAgent(c, "ms")
    h2.run(lean_l)
    assert h2.run(lean_r + [box("chair", 2.0, 0.85)])[0].avoid == "left"
    print(f"{'hysteresis':20} -> holds through flicker, flips when sustained or blocked")

    # Segments: the steer case must decompose into reusable clip units.
    dec, _ = a.run([Detection("person", 0.9, 2.0, "left", False)])
    segs = decision_segments(dec, c)
    print(f"{'segments':20} -> {segs}")
    assert segs == ["orang di kiri", "2 meter, elak ke kanan"], segs
    dec, _ = a.run([Detection("car", 0.95, 0.9, "center", True)])
    assert decision_segments(dec, c) == ["Berhenti!", "kereta di hadapan"], decision_segments(dec, c)
    # spoken metres come from the tracker's held value, not a fresh round()
    dec, line = RulesNavigationAgent(c, "ms").run(
        [Detection("person", 0.9, 2.6, "left", spoken_m=2)])
    assert "2 meter" in line and decision_segments(dec, c)[-1] == "2 meter, elak ke kanan", line
    # whitelist: a class not in the catalog is ignored entirely (not named, not blocking)
    dec, line = RulesNavigationAgent(c, "ms").run(
        [box("chair", 2.0, 0.5), box("not_in_catalog", 2.0, 0.17)])
    assert dec.avoid == "left" and dec.action == "steer_left", (dec, line)
    dec, line = RulesNavigationAgent(c, "ms").run(
        [box("person", 2.0, 0.5, 0.25), box("chair", 2.0, 0.17), box("chair", 2.0, 0.83)])
    assert " ".join(decision_segments(dec, c)) == line, (decision_segments(dec, c), line)

    print("OK — rules self-test passed (zones, side-steer, blocked, map fallback, hysteresis, segments)")
