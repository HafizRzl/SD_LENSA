"""Shared Malay neural TTS (mesolitica/Malaysian-TTS-0.6B-v1) for the desktop beta.

Runs on CPU (DistilCodec hardcodes CUDA; we neutralise it). Used only by render_clips.py,
which the beta spawns in its own process for clips that aren't cached yet.

Speakers: 'husein' (male), 'idayu' (female), 'singaporean', ...
"""
import json
import os
import re

# torch (MKL) and numba/librosa each ship an OpenMP runtime; loading both with multi-thread
# pools crashes with 0xC0000005. Allow the duplicate AND cap OpenMP to 1 thread. Must be set
# before torch is imported. (This is the render path only — the live beta keeps full threads.)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer, AutoModelForCausalLM
from distilcodec import DistilCodec

# DistilCodec hardcodes .cuda(); on CPU-only torch make it a no-op so tensors stay on CPU.
if not torch.cuda.is_available():
    torch.Tensor.cuda = lambda self, *a, **k: self  # type: ignore[method-assign]

TTS_MODEL = "mesolitica/Malaysian-TTS-0.6B-v1"
CODEC_REPO = "IDEA-Emdoor/DistilCodec-v1.0"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SAMPLE_RATE = 24000


def _load_codec_cpu(config_path, model_path):
    """DistilCodec.from_pretrained pins to CUDA; replicate it on CPU instead."""
    with open(config_path) as f:
        cfg = json.load(f)
    codec = DistilCodec(cfg)
    codec.device = torch.device(DEVICE)
    codec.is_debug = False
    codec.ckpt_step = -1
    codec.g_ckpt_path = -1
    state = DistilCodec.load_checkpoint(model_path, codec.device)
    codec.generator.load_state_dict(state["generator"])
    codec.encoder.load_state_dict(state["encoder"])
    codec.quantizer.load_state_dict(state["quantizer"])
    codec.move_to_cuda()          # moves modules to self.device (= CPU)
    return codec.eval()


class HuseinTTS:
    def __init__(self, speaker: str = "husein"):
        self.speaker = speaker
        self.codec = None
        self.tok = None
        self.model = None

    def load(self) -> "HuseinTTS":
        cfg = hf_hub_download(CODEC_REPO, "model_config.json")
        ckpt = hf_hub_download(CODEC_REPO, "g_00204000")
        self.codec = _load_codec_cpu(cfg, ckpt)
        self.tok = AutoTokenizer.from_pretrained(TTS_MODEL)
        # bf16 (~1.2 GB) + low_cpu_mem_usage keeps peak RAM low; float32 (~2.4 GB) OOM-crashes
        # on a machine with little free RAM. The model was trained in bf16 mixed precision.
        self.model = AutoModelForCausalLM.from_pretrained(
            TTS_MODEL, dtype=torch.bfloat16, low_cpu_mem_usage=True
        ).to(DEVICE).eval()
        return self

    def synth(self, text: str) -> np.ndarray:
        """Return a float32 waveform at SAMPLE_RATE for one line."""
        prompt = f"<|im_start|>{self.speaker}: {text}<|speech_start|>"
        inputs = self.tok(prompt, return_tensors="pt", add_special_tokens=False).to(DEVICE)
        with torch.no_grad():
            out = self.model.generate(
                **inputs, max_new_tokens=1024, do_sample=True,
                temperature=0.7, repetition_penalty=1.1,
            )
        decoded = self.tok.decode(out[0]).split("<|speech_start|>")[-1].replace("<|endoftext|>", "")
        codes = list(map(int, re.findall(r"speech_(\d+)", decoded)))
        if not codes:
            return np.zeros(1, dtype=np.float32)
        wav = self.codec.decode_from_codes(codes, minus_token_offset=False)
        return wav[0, 0].detach().cpu().numpy()
