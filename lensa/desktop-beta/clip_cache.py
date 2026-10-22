"""Voice clip cache conventions — shared by the beta (player) and render_clips.py (renderer).
No heavy imports here on purpose."""
from pathlib import Path
import hashlib

HERE = Path(__file__).resolve().parent


def clip_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def cache_dir_for(speaker: str) -> Path:
    d = HERE / "voice_cache" / speaker
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_path(speaker: str, text: str) -> Path:
    return cache_dir_for(speaker) / f"{clip_key(text)}.wav"
