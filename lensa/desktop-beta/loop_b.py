"""Loop B — the on-demand, ONLINE loop: describe the scene and read signs with Gemini vision.

Loop A (hazards) stays offline and deterministic; Loop B needs internet by design — a vision
model that can describe a street is far too big to ship inside a phone app. It never blocks
Loop A: each request runs on its own thread, and the answer is handed back as text to speak.

Setup: copy secrets.example.yaml -> secrets.yaml (gitignored) and fill in your Gemini API key
and model. Prompts: assets/context/agents/loopb_*.md (re-read on every request — edit freely).
OCR behaviour: assets/context/rules/ocr_reading_rules.yaml.

Run `python desktop-beta/loop_b.py` for the offline self-test (no network, no key needed).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import yaml

from lensa_rules import _ASSETS, _load

HERE = Path(__file__).resolve().parent
SECRETS = HERE / "secrets.yaml"
TIMEOUT_S = 20
LANG_NAME = {"ms": "Bahasa Melayu", "en": "English"}


def load_secrets() -> dict:
    """{gemini_api_key, gemini_model} from secrets.yaml; GEMINI_API_KEY env var also works."""
    s = {}
    if SECRETS.exists():
        s = yaml.safe_load(SECRETS.read_text(encoding="utf-8")) or {}
    key = os.environ.get("GEMINI_API_KEY") or s.get("gemini_api_key") or ""
    return {"key": "" if key.startswith("PASTE") else key, "model": s.get("gemini_model") or ""}


def is_api_error(e: Exception) -> bool:
    """True when Gemini itself answered with an error (4xx/5xx), False for network failures."""
    try:
        from google.genai import errors
        return isinstance(e, errors.APIError)
    except ImportError:
        return False


def describe_prompt(lang: str) -> str:
    return (_ASSETS / "agents" / "loopb_describe_scene.md").read_text(encoding="utf-8") \
        .replace("{language}", LANG_NAME.get(lang, lang))


def read_prompt(lang: str, rules: dict) -> str:
    translate = rules.get("translate_to_user_language", False)
    return (_ASSETS / "agents" / "loopb_read_sign.md").read_text(encoding="utf-8") \
        .replace("{prioritise}", ", ".join(rules.get("prioritise") or ["warning", "signboard"])) \
        .replace("{clutter_rule}", "Skip advertising and dense unrelated text."
                 if rules.get("ignore_clutter", True) else "Read all text you can see.") \
        .replace("{translate_rule}", f"Translate the text into {LANG_NAME.get(lang, lang)}."
                 if translate else "Keep the text in its original language.") \
        .replace("{max_chars}", str(rules.get("max_chars", 80)))


def sign_line(raw: str, lang: str, rules: dict, lib) -> str:
    """Model output -> the spoken line (OCR template, max_chars, 'no text' message)."""
    text = " ".join((raw or "").split())
    if not text or text.upper().strip(" .") == "NONE":
        return lib.render("ocr_none")
    limit = int(rules.get("max_chars", 80))
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return lib.render("ocr", text=text)


class GeminiVision:
    """The one place that talks to Gemini. Swap this class to change provider (or plug in an
    offline OCR such as ML Kit on the phone) — LoopB only calls ask(prompt, frame)."""

    def __init__(self, key: str, model: str):
        from google import genai            # imported lazily: Loop A never needs it
        from google.genai import types
        self.types = types
        self.model = model
        self.client = genai.Client(api_key=key,
                                   http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000))

    def ask(self, prompt: str, frame_bgr) -> str:
        ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("could not encode the camera frame")
        r = self.client.models.generate_content(
            model=self.model,
            contents=[self.types.Part.from_bytes(data=jpg.tobytes(), mime_type="image/jpeg"),
                      "Respond now, following the instructions."],
            config=self.types.GenerateContentConfig(system_instruction=prompt))
        return (r.text or "").strip()


class LoopB:
    """Runs one request at a time in the background. `say(text)` is called with the line to
    speak — the answer, or a spoken failure (no key / no internet / error)."""

    def __init__(self, lang: str, phrases, say, vision=None):
        self.lang, self.lib, self.say = lang, phrases, say
        self.busy = False
        self.last = ""                      # last answer, for the on-screen HUD
        self._vision = vision               # injectable for tests
        self._err = None

    def _client(self):
        if self._vision is None:
            s = load_secrets()
            if not s["key"] or not s["model"]:
                return None
            self._vision = GeminiVision(s["key"], s["model"])
        return self._vision

    def describe(self, frame):
        self._start(lambda v: v.ask(describe_prompt(self.lang), frame))

    def read_sign(self, frame):
        rules = _load("rules/ocr_reading_rules.yaml")
        self._start(lambda v: sign_line(v.ask(read_prompt(self.lang, rules), frame),
                                        self.lang, rules, self.lib))

    def _start(self, job):
        if self.busy:
            return                          # one request at a time; ignore repeated presses
        try:
            vision = self._client()
        except Exception as e:              # e.g. google-genai not installed
            print(f"[loopB] {e}")
            vision = None
        if vision is None:
            self._finish(self.lib.render("loopb_no_key"))
            print("[loopB] no API key/model — copy desktop-beta/secrets.example.yaml to secrets.yaml")
            return
        self.busy = True
        self.say(self.lib.render("loopb_wait"), wait_clip=True)
        threading.Thread(target=self._run, args=(job, vision), daemon=True).start()

    def _run(self, job, vision):
        try:
            text = job(vision)
        except Exception as e:
            print(f"[loopB] request failed: {type(e).__name__}: {e}")
            # Gemini answered with an error (wrong model, quota, bad key) vs no connection at all —
            # saying "no internet" for a 404 sent the user looking for the wrong problem.
            text = self.lib.render("loopb_error" if is_api_error(e) else "loopb_offline")
        self.busy = False
        self._finish(text)

    def _finish(self, text):
        self.last = text
        print(f"[loopB] {text}")
        self.say(text)


if __name__ == "__main__":
    from lensa_rules import Content
    c = Content.load()
    lib = c.phrases("ms")
    rules = _load("rules/ocr_reading_rules.yaml")
    # prompts fill every placeholder
    for p in (describe_prompt("ms"), read_prompt("ms", rules)):
        assert "{" not in p.split("## Role")[1], p
    assert "Bahasa Melayu" in describe_prompt("ms") and "English" in describe_prompt("en")
    # sign post-processing
    assert sign_line("NONE", "ms", rules, lib) == "Tiada tulisan dijumpai"
    assert sign_line("  DILARANG   MASUK ", "ms", rules, lib) == "Papan tanda: DILARANG MASUK"
    long = sign_line("A " * 200, "ms", rules, lib)
    assert len(long) <= len("Papan tanda: ") + rules["max_chars"], long

    # the loop with a fake model: answer spoken after "Sebentar"; failures become spoken lines
    class Fake:
        def __init__(self, out=None, err=None): self.out, self.err = out, err
        def ask(self, prompt, frame):
            if self.err: raise self.err
            return self.out
    import numpy as np, time
    frame = np.zeros((48, 64, 3), np.uint8)
    from google.genai import errors as gerr
    api404 = gerr.APIError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
    for vision, expect in ((Fake("Ada kedai di kiri."), "Ada kedai di kiri."),
                           (Fake(err=ConnectionError("offline")), lib.render("loopb_offline")),
                           (Fake(err=api404), lib.render("loopb_error"))):
        said = []
        lb = LoopB("ms", lib, lambda t, wait_clip=False: said.append(t), vision=vision)
        lb.describe(frame)
        for _ in range(100):
            if not lb.busy: break
            time.sleep(0.01)
        assert said == ["Sebentar", expect], said
    said = []
    lb = LoopB("ms", lib, lambda t, wait_clip=False: said.append(t), vision=Fake("NONE"))
    lb.read_sign(frame)
    for _ in range(100):
        if not lb.busy: break
        time.sleep(0.01)
    assert said == ["Sebentar", "Tiada tulisan dijumpai"], said
    print("OK — loop B self-test passed (prompts, sign lines, answer/offline/none paths)")
