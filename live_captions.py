#!/usr/bin/env python3
"""Live Japanese -> English captions for a phone call, running entirely on the Mac.

Pipeline:  call audio (BlackHole loopback, a mic, or a file)
           -> Silero VAD (cuts speech into utterances)
           -> Japanese speech recognizer on MLX (Kotoba-Whisper, NVIDIA Parakeet-ja or Qwen3-ASR)
           -> Japanese->English-only translation model on MLX (LiquidAI LFM2-350M-ENJP-MT)
           -> terminal, and with --speak the Mac's built-in voice in your left ear

Nothing is sent over the network; models are downloaded from Hugging Face once.
"""

import argparse
import faulthandler
import gc
import json
import os
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime

import numpy as np

SR = 16000
FRAME = 512  # Silero VAD frame at 16 kHz (32 ms)
FRAME_S = FRAME / SR

# Japanese-only speech recognizers.
ASR_MODELS = {
    "kotoba": "kaiinui/kotoba-whisper-v2.0-mlx",  # Kotoba-Whisper v2.0, distilled for Japanese
    "parakeet": "mlx-community/parakeet-tdt_ctc-0.6b-ja",  # NVIDIA Parakeet TDT-CTC 0.6B Japanese, GPU
    "parakeet-ane": "FluidInference/parakeet-0.6b-ja-coreml",  # the same model, encoder on the Neural Engine
    "qwen3": "moona3k/mlx-qwen3-asr-1.7b-8bit",  # Alibaba Qwen3-ASR 1.7B, 8-bit
    "qwen3-small": "moona3k/mlx-qwen3-asr-0.6b-8bit",  # Alibaba Qwen3-ASR 0.6B, 8-bit
    "cpu": "kotoba-tech/kotoba-whisper-v2.0-faster",  # same Kotoba model via faster-whisper, for non-Mac testing
}
# Japanese<->English-only translation models.
MT_MODELS = {
    "lfm2": "LiquidAI/LFM2-350M-ENJP-MT",  # LiquidAI, 350M (full precision), built for near-real-time JA<->EN
    "plamo": "mlx-community/plamo-2-translate",  # Preferred Networks PLaMo 2 Translate, 9.5B, 4-bit
}

# Phrases Whisper-family models invent on silence or noise (they were trained on subtitled video).
HALLUCINATIONS = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "ご清聴ありがとうございました",
    "チャンネル登録",
    "字幕",
    "最後までご視聴",
)


# ---------------------------------------------------------------- speech recognition


class WhisperASR:
    def __init__(self, repo, hint):
        import mlx_whisper

        self._mw = mlx_whisper
        self.repo = repo
        self.hint = hint or None

    def __call__(self, audio, final=True):
        r = self._mw.transcribe(
            audio,
            path_or_hf_repo=self.repo,
            language="ja",
            task="transcribe",
            temperature=(0.0, 0.4) if final else 0.0,
            condition_on_previous_text=False,
            initial_prompt=self.hint,
            verbose=None,
        )
        return r["text"].strip()


PUNCT = set("。、？！?!,.・…「」『』 \u2581")


def is_punct(piece):
    return all(c in PUNCT for c in piece)


def after_pause(audio, start):
    """Where speech starts again after the first pause (about 0.13 s or more) past `start`, if at
    least 0.3 s of speech follows it; otherwise None."""
    n = len(audio) // FRAME
    if n == 0:
        return None
    rms = np.sqrt((audio[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))
    loud = rms > max(1e-4, 0.1 * np.percentile(rms, 90))  # 20 dB below the speech
    quiet = 0
    for i in range(start // FRAME, n):
        if not loud[i]:
            quiet += 1
            continue
        if quiet >= 4 and loud[i:].sum() * FRAME >= 0.3 * SR:
            return i * FRAME
        quiet = 0
    return None


def keep_going(once, audio):
    """Parakeet-ja often stops after one sentence when another follows a short pause (seen on real
    speech: "…幸いです。" then nothing for 「よろしくお願いします」). So when there is a pause after
    the last word it wrote and speech after that pause, carry on from there. once(audio) returns
    the text and the time its last word ended (or, with CTC, was spotted)."""
    texts, start = [], 0
    for _ in range(6):
        text, end = once(audio[start:])
        if not re.search(r"[\w\u3040-\u30ff\u4e00-\u9fff]", text) or end <= 0:
            break
        texts.append(text)
        start = after_pause(audio, start + int(end * SR))
        if start is None:
            break
    return "".join(texts)


class ParakeetASR:
    """Parakeet-ja on the GPU (MLX)."""

    def __init__(self, repo, hint):
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        self._mx = mx
        self._get_logmel = get_logmel
        self.model = from_pretrained(repo)

    def _once(self, audio):
        mel = self._get_logmel(self._mx.array(audio), self.model.preprocessor_config)
        r = self.model.generate(mel)[0]
        words = [t for t in r.tokens if not is_punct(t.text)]
        return r.text.strip(), (words[-1].end if words else 0.0)

    def __call__(self, audio, final=True):
        return keep_going(self._once, audio)


# The Core ML files ParakeetANE needs (the repo also has a second, uncompiled copy of each).
ANE_FILES = ["Preprocessor.mlmodelc/*", "Encoder.mlmodelc/*", "CtcDecoder.mlmodelc/*", "vocab.json"]


def ctc_greedy(logits, vocab, blank, step_s):
    """Text from CTC logits [steps, vocab + 1], and the time its last word ended."""
    ids, last, prev = [], -1, blank
    for t, k in enumerate(logits.argmax(axis=-1).tolist()):
        if k != blank:
            if k != prev:
                ids.append(k)
            if k >= len(vocab) or not is_punct(vocab[k]):
                last = t
        prev = k
    text = "".join(vocab[i] for i in ids if i < len(vocab)).replace("\u2581", " ").strip()
    return text, (last + 1) * step_s if last >= 0 else 0.0


class ParakeetANE:
    """Parakeet-ja with its encoder, nearly all of the work, on the Neural Engine (Core ML). Its
    output is turned into text by Parakeet's own TDT decoder on MLX, the one --asr parakeet uses,
    so the text matches the GPU version. If that decoder can't load, the Core ML build's CTC head
    does it instead (worse with names and punctuation, seen on a real voicemail). The Core ML
    build takes 15 s of audio at a time."""

    WINDOW = 15 * SR

    def __init__(self, repo, hint):
        import coremltools as ct
        from huggingface_hub import snapshot_download

        path = snapshot_download(repo, allow_patterns=ANE_FILES)
        units = ct.ComputeUnit.CPU_AND_NE

        def load(name):
            return ct.models.CompiledMLModel(os.path.join(path, f"{name}.mlmodelc"), units)

        self.prep, self.encoder, self.ctc = load("Preprocessor"), load("Encoder"), load("CtcDecoder")
        with open(os.path.join(path, "vocab.json"), encoding="utf-8") as f:
            self.vocab = json.load(f)
        self.blank = len(self.vocab)
        self.held = deque(maxlen=64)  # see _predict
        self.tdt = None
        try:
            import mlx.core as mx
            from parakeet_mlx import from_pretrained

            self._mx = mx
            self.tdt = from_pretrained(ASR_MODELS["parakeet"])
        except Exception:
            pass

    def _predict(self, model, inputs):
        # coremltools 9.0 lends each input array to Core ML, which can let go of it later on one of
        # its own threads. That release drops the array's reference count without holding Python's
        # lock and, if it was the last reference, frees the array right there: the segmentation
        # fault seen on a real Mac, in a Core ML thread while Python sat idle. (Fixed upstream in
        # coremltools commit a207b1a, "Fix NumPy owner release from Core ML queues", in no release
        # yet.) Holding on to the last 64 calls' inputs (around 60 MB) means that late release never
        # frees anything; by the time this drops them, Core ML has long let go.
        out = model.predict(inputs)
        self.held.append(inputs)
        return out

    def _once(self, audio):
        n = min(len(audio), self.WINDOW)
        x = np.zeros((1, self.WINDOW), np.float32)
        x[0, :n] = audio[:n]
        mel = self._predict(self.prep, {"audio_signal": x, "audio_length": np.array([n], np.int32)})
        enc = self._predict(self.encoder, {"mel_features": mel["mel_features"], "mel_length": mel["mel_length"]})
        features = np.asarray(enc["encoder"], np.float32)  # [1, 1024, steps of 80 ms]
        steps = int(np.asarray(enc["encoder_length"]).reshape(-1)[0]) or features.shape[2]
        if self.tdt is not None:
            mx = self._mx
            tokens = self.tdt.decode(mx.array(features[:, :, :steps].transpose(0, 2, 1)), mx.array([steps]))[0][0]
            words = [t for t in tokens if not is_punct(t.text)]
            text = "".join(t.text for t in tokens).replace("\u2581", " ").strip()
            return text, (words[-1].end if words else 0.0)
        logits = np.asarray(self._predict(self.ctc, {"encoder_output": enc["encoder"]})["ctc_logits"])[0]
        return ctc_greedy(logits[:steps], self.vocab, self.blank, 15.0 / logits.shape[0])

    def __call__(self, audio, final=True):
        return keep_going(self._once, audio)


class NeuralEngineHelper:
    """ParakeetANE in a helper process of its own. On a real Mac, Core ML crashed the whole program
    (a segmentation fault in one of its own background threads, while Python sat idle) as soon as
    it ran next to the live captioner, though it worked fine on its own. Here a crash can only end
    the helper: the captioner switches to Parakeet on the GPU, loaded ahead for that, and carries
    on. The helper's own crash report goes to LOG."""

    LOG = os.path.join(tempfile.gettempdir(), "call-captions-neural-engine.log")

    def __init__(self, repo, hint):
        import subprocess

        self.hint, self.fallback, self.on_fallback = hint, None, None
        self.log = open(self.LOG, "w")
        self.proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--neural-engine-helper", repo],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        if self.proc.stdout.readline().strip() != b"ready":
            self.proc.kill()
            raise RuntimeError(f"the Neural Engine helper didn't start (see {self.LOG})")
        self.backup = ParakeetASR(ASR_MODELS["parakeet"], hint)  # ready in case the helper stops

    def __call__(self, audio, final=True):
        if self.fallback is None:
            try:
                data = np.ascontiguousarray(audio, "<f4").tobytes()
                self.proc.stdin.write(struct.pack("<i", len(data) // 4) + data)
                self.proc.stdin.flush()
                size = struct.unpack("<i", self._read(4))[0]
                return self._read(size).decode("utf-8")
            except (OSError, ValueError, struct.error):
                self.fallback = self.backup
                if self.on_fallback:
                    self.on_fallback(f"! The Neural Engine stopped; using Parakeet on the GPU (details: {self.LOG})")
        return self.fallback(audio, final)

    def close(self):
        try:
            self.proc.stdin.close()  # the helper exits when its input ends
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def _read(self, n):
        data = self.proc.stdout.read(n)
        if len(data) != n:
            raise ValueError("the Neural Engine helper stopped")
        return data


def neural_engine_helper(repo):
    """The helper's side: hear audio sent on stdin with ParakeetANE and answer with the text."""
    asr = ParakeetANE(repo, "")
    asr(np.zeros(SR, np.float32))
    out = sys.stdout.buffer
    out.write(b"ready\n")
    out.flush()
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        n = struct.unpack("<i", head)[0]
        audio = np.frombuffer(inp.read(4 * n), "<f4")
        text = asr(audio).encode("utf-8")
        out.write(struct.pack("<i", len(text)) + text)
        out.flush()


class QwenASR:
    def __init__(self, repo, hint):
        from mlx_qwen3_asr import Session

        self.session = Session(repo)
        self.hint = hint or ""

    def __call__(self, audio, final=True):
        return self.session.transcribe(audio, language="Japanese", context=self.hint).text.strip()


class FasterWhisperASR:
    def __init__(self, name, hint):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(name, device="cpu", compute_type="int8")
        self.hint = hint or None

    def __call__(self, audio, final=True):
        segs, _ = self.model.transcribe(
            audio,
            language="ja",
            beam_size=5 if final else 1,
            condition_on_previous_text=False,
            initial_prompt=self.hint,
        )
        return "".join(s.text for s in segs).strip()


def make_asr(choice, hint):
    if choice == "cpu" or choice.startswith("cpu:"):
        return FasterWhisperASR(choice.partition(":")[2] or ASR_MODELS["cpu"], hint)
    repo = ASR_MODELS.get(choice, choice)
    if "coreml" in repo.lower():
        return NeuralEngineHelper(repo, hint)
    if "parakeet" in repo:
        return ParakeetASR(repo, hint)
    if "qwen3-asr" in repo.lower():
        return QwenASR(repo, hint)
    return WhisperASR(repo, hint)


def looks_bogus(ja, hint):
    if not ja or not re.search(r"[\w\u3040-\u30ff\u4e00-\u9fff]", ja):
        return True
    if any(h in ja for h in HALLUCINATIONS) and len(ja) < 40:
        return True
    if hint and ja.replace(" ", "") == hint.replace(" ", ""):
        return True  # Whisper echoed the prompt back
    return bool(re.search(r"(.{1,4})\1{6,}", ja))  # stuck repeating itself


def ends_sentence(ja):
    return ja.rstrip().endswith(("。", "？", "！", "?", "!"))


# ---------------------------------------------------------------- translation


class Translator:
    """A dedicated Japanese->English model on MLX. Each utterance is translated on its own."""

    PLAMO_STOP = "<|plamo:op|>"

    def __init__(self, choice):
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler

        repo = MT_MODELS.get(choice, choice)
        self.plamo = "plamo" in repo.lower()
        self.model, self.tok = load(repo, tokenizer_config={"trust_remote_code": True} if self.plamo else None)
        self._stream = stream_generate
        self._sampler = make_sampler(temp=0.0)

    def prompt(self, ja):
        if self.plamo:
            return (
                f"{self.PLAMO_STOP}dataset\ntranslation\n"
                f"{self.PLAMO_STOP}input lang=Japanese\n{ja}\n"
                f"{self.PLAMO_STOP}output lang=English\n"
            )
        # LFM2-ENJP only works with exactly this system prompt.
        messages = [
            {"role": "system", "content": "Translate to English."},
            {"role": "user", "content": ja},
        ]
        return self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

    def __call__(self, ja):
        text = ""
        for r in self._stream(
            self.model,
            self.tok,
            self.prompt(ja),
            max_tokens=min(300, 3 * len(ja) + 40),
            sampler=self._sampler,
        ):
            text += r.text
            if self.PLAMO_STOP in text:
                text = text.split(self.PLAMO_STOP)[0]
                break
        return text.strip()


# ---------------------------------------------------------------- terminal output

DIM, BOLD, CYAN, YELLOW, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[93m", "\033[0m"
GREEN, MAGENTA = "\033[32m", "\033[95m"


def display_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def tail_to_width(s, width):
    """Keep the end of s (the newest words) so it fits on one terminal line."""
    if display_width(s) <= width:
        return s
    out, w = [], 1
    for c in reversed(s):
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if w + cw > width:
            break
        out.append(c)
        w += cw
    return "…" + "".join(reversed(out))


class Screen:
    def __init__(self, english_only, show_timing, log_path):
        self.english_only = english_only
        self.show_timing = show_timing
        self.partial_lines = 0
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        self.lock = threading.Lock()

    def _clear_partial(self):
        if self.partial_lines:
            sys.stdout.write("\r\033[2K" + "\033[1A\r\033[2K" * (self.partial_lines - 1))
            self.partial_lines = 0

    def partial(self, ja, en=None):
        with self.lock:
            self._clear_partial()
            width = shutil.get_terminal_size().columns - 4
            lines = []
            if not self.english_only or not en:
                lines.append(f"{DIM}  … {tail_to_width(ja, width)}{RESET}")
            if en:
                lines.append(f"{DIM}{YELLOW}  ~ {tail_to_width(en, width)}{RESET}")
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()
            self.partial_lines = len(lines)

    def clear(self):
        with self.lock:
            self._clear_partial()
            sys.stdout.flush()

    def final_ja(self, ja):
        with self.lock:
            self._clear_partial()
            if not self.english_only:
                stamp = datetime.now().strftime("%H:%M:%S")
                sys.stdout.write(f"{DIM}{stamp}{RESET}  {CYAN}{ja}{RESET}\n")
            sys.stdout.flush()

    def final_en(self, ja, en, asr_s, mt_s, early=False):
        with self.lock:
            self._clear_partial()
            when = " · early" if early else ""  # done during the pause, before it was known to end
            timing = f"  {DIM}(asr {asr_s:.2f}s · mt {mt_s:.2f}s{when}){RESET}" if self.show_timing else ""
            if en:
                sys.stdout.write(f"          {BOLD}{YELLOW}➜ {en}{RESET}{timing}\n\n")
            else:
                sys.stdout.write(f"         {timing}\n")
            sys.stdout.flush()
            if self.log:
                self.log.write(f"{datetime.now():%H:%M:%S}\t{ja}\t{en}\n")
                self.log.flush()

    def info(self, msg):
        with self.lock:
            self._clear_partial()
            sys.stdout.write(f"{DIM}{msg}{RESET}\n")
            sys.stdout.flush()

    def lookup(self, event):
        """Part of a word lookup (see WordLookup), set apart from the captions by a magenta bar."""
        bar = f"{MAGENTA}┃{RESET} "
        lines = []
        if "words" in event:
            lines.append(f"{bar}{BOLD}{event['text']}{RESET}")
            for form, reading, meaning, notes in event["words"]:
                note = f" {GREEN}({', '.join(notes)}){RESET}" if notes else ""
                lines.append(f"{bar}  {CYAN}{form}{RESET}  {DIM}{reading}{RESET}  {meaning}{note}")
        if "en" in event:
            lines.append(f"{bar}  {YELLOW}➜ {event['en']}{RESET}")
        if "explain" in event:
            lines.append(f"{bar}  {DIM}✎ {event['explain']}{RESET}\n")
        with self.lock:
            self._clear_partial()
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------- spoken English


def read_wav(path):
    """Mono float32 samples and the sample rate of a 16-bit or float WAV file."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt, pcm = 12, None, b""
    while pos + 8 <= len(data):
        chunk, size = data[pos : pos + 4], int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = data[pos + 8 : pos + 8 + size]
        if chunk == b"fmt ":
            tag, channels, rate = struct.unpack_from("<HHI", body)
            bits = struct.unpack_from("<H", body, 14)[0]
            if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE keeps the real format in its sub-format
                tag = struct.unpack_from("<H", body, 24)[0]
            fmt = tag, channels, rate, bits
        elif chunk == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None:
        raise ValueError("WAV file without a format")
    tag, channels, rate, bits = fmt
    if (tag, bits) == (1, 16):
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float32) / 32768
    elif (tag, bits) == (3, 32):
        x = np.frombuffer(pcm[: len(pcm) // 4 * 4], "<f4").astype(np.float32)
    else:
        raise ValueError(f"unsupported WAV format {tag}/{bits}-bit")
    return x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1), rate


def speed_up(pcm, rate, speed):
    """pcm played speed times as fast, at the same pitch (FFmpeg's atempo filter, through PyAV)."""
    import av.filter
    from fractions import Fraction

    graph = av.filter.Graph()
    last = graph.add_abuffer(format="flt", sample_rate=rate, layout="mono", time_base=Fraction(1, rate))
    left = speed
    while abs(left - 1) > 1e-3:  # older FFmpeg takes 0.5 to 2 per atempo
        step = min(2.0, max(0.5, left))
        tempo = graph.add("atempo", f"{step:.4f}")
        last.link_to(tempo)
        last, left = tempo, left / step
    sink = graph.add("abuffersink")
    last.link_to(sink)
    graph.configure()
    frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1).astype(np.float32), format="flt", layout="mono")
    frame.sample_rate, frame.pts, frame.time_base = rate, 0, Fraction(1, rate)
    graph.push(frame)
    graph.push(None)
    out = []
    while True:
        try:
            out.append(graph.pull().to_ndarray().reshape(-1))
        except (av.BlockingIOError, av.EOFError):
            break
    return np.concatenate(out) if out else pcm[:0]


class VoiceOff(Exception):
    """The voice can't run; captions carry on without it."""


def siri_is_system_voice():
    """Whether the Mac's system voice for English (System Settings → Accessibility → Read & Speak)
    looks like a Siri voice in its settings. `say` can use a Siri voice only that way: it can't
    name one with -v."""
    import subprocess

    try:
        out = subprocess.run(["defaults", "read", "com.apple.Accessibility", "SpokenContentDefaultVoiceSelectionsByLanguage"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    english = re.search(r'(?ms)^\s*"?en(?:[-_][A-Za-z]+)?"?\s*=\s*(.*?);\s*$', out)
    return bool(english) and "siri" in english.group(1).lower()


SIRI_HOW = ("For Siri: System Settings → Accessibility → Read & Speak (Spoken Content before macOS 26) → "
            "System voice → choose a Siri voice.")


def voice_name(voice):
    if voice:
        return voice
    return "Siri" if siri_is_system_voice() else "the Mac's system voice"


def voice_label(args):
    """E.g. "Siri, 2x speed"."""
    rate = f", {args.rate} words a minute" if args.rate else ""
    return f"{voice_name(args.voice)}{rate}, {args.voice_speed:g}x speed"


class Voice:
    """Says each English line in your left ear with the Mac's built-in speech (`say`), in the
    Mac's system voice (Siri, if you chose it there) unless --voice names another.

    split: the call plays only in your right ear. The Mac's sound output becomes BlackHole, and
           call-captions-audio plays BlackHole to your right ear and the English to your left.
    mix:   the call keeps playing in both ears through "Call Captions Output"; the English is
           added in your left ear.
    The English only goes to your headset, never into BlackHole or the call.
    """

    def __init__(self, mode, voice, wpm, speed, volume, screen):
        import subprocess

        self._sp = subprocess
        self.screen, self.voice, self.wpm, self.speed, self.gain = screen, voice or None, wpm, speed, volume
        self.audio = os.environ.get("CALL_CAPTIONS_AUDIO", "")
        hear = os.environ.get("CALL_CAPTIONS_HEAR", "")
        blackhole = os.environ.get("CALL_CAPTIONS_BLACKHOLE", "")
        if not (shutil.which("say") and os.access(self.audio, os.X_OK) and hear and (blackhole or mode == "mix")):
            raise VoiceOff("it works on a Mac, started with the call-captions command after its sound setup")
        self.hear_name = self._check_headphones(hear)
        self.previous = self._audio("default-output").strip()
        command = [self.audio, "monitor", hear] + ([blackhole] if mode == "split" else [])
        # Its own session, so Ctrl+C reaches only this script, which then stops it in order.
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
        import select

        started = select.select([self.proc.stdout], [], [], 10)[0]
        ready = self.proc.stdout.readline().split() if started else []
        if len(ready) != 2 or ready[0] != b"ready":
            self.proc.kill()
            raise VoiceOff("couldn't start playing to your headset (call-captions-audio monitor)")
        self.rate = int(ready[1])
        self.switched = False
        if mode == "split":
            if self._sp.run([self.audio, "set-output", blackhole]).returncode == 0:
                self.switched = True
            else:
                screen.info("! couldn't move the call to your right ear; it stays in both ears")
        self.wav = os.path.join(tempfile.mkdtemp(prefix="call-captions-"), "line.wav")
        self.cond = threading.Condition()
        self.pending = None  # the newest line to say
        self.asked = None  # a word you looked up: said next, never skipped
        self.tried = set()  # voices that didn't work
        self.upcoming = None  # a line to get ready ahead of time
        self.made = {}  # text -> samples made ahead of time
        self.busy = False  # making or sending a line
        self.busy_until = 0.0  # when the speech already sent finishes playing
        self.closed = self.failed = self.warned_slow = False
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _audio(self, *args):
        return self._sp.run([self.audio, *args], capture_output=True, text=True, timeout=10).stdout

    def _check_headphones(self, hear):
        for line in self._audio("outputs").splitlines():
            uid, kind, name = (line.split("\t") + ["", ""])[:3]
            if uid != hear:
                continue
            low = name.lower()
            if "speaker" in low or kind in ("display", "airplay") or (kind == "builtin" and "headphone" not in low):
                raise VoiceOff(
                    f"{name} would play the English out loud, where your microphone could pass it into the "
                    "call. Choose headphones with: call-captions audio"
                )
            return name
        raise VoiceOff("the device you hear calls on isn't connected. Choose it with: call-captions audio")

    def prepare(self, text):
        """Make the audio for a line that is probably about to be said."""
        with self.cond:
            if text not in self.made:
                self.upcoming = text
                self.cond.notify()

    def say(self, text, lookup=False):
        with self.cond:
            if lookup:
                self.asked = text
            else:
                self.pending = text
            self.cond.notify()

    def _synth(self, text):
        command = ["say", "-o", self.wav, f"--data-format=LEI16@{self.rate}", "-f", "-"]
        if self.wpm:
            command[1:1] = ["-r", str(self.wpm)]
        if self.voice:
            command[1:1] = ["-v", self.voice]
        r = self._sp.run(command, input=text.encode(), capture_output=True, timeout=30, start_new_session=True)
        if r.returncode == 0:
            pcm, rate = read_wav(self.wav)
            if rate != self.rate:
                import soxr

                pcm = soxr.resample(pcm, rate, self.rate)
            loud = np.flatnonzero(np.abs(pcm) > 1e-3)
            if len(loud):
                start = max(0, loud[0] - self.rate // 100)  # skip the silence `say` starts with
                pcm = pcm[start : loud[-1] + 1]
                if self.speed != 1:
                    try:
                        pcm = speed_up(pcm, self.rate, self.speed)
                    except Exception as e:
                        self.screen.info(f"! couldn't speed the voice up ({e}); it talks at its normal speed")
                        self.speed = 1
                return (pcm * self.gain).astype(np.float32)
            if not any(c.isalnum() for c in text):
                return pcm[:0]
        # This voice isn't on the Mac or made no sound (a Siri voice still downloading, say): try
        # the system voice, then Samantha, which every Mac has.
        problem = "made no sound" if r.returncode == 0 else "isn't on this Mac"
        self.tried.add(self.voice)
        for other in (None, "Samantha"):
            if other not in self.tried:
                was = f"the voice {self.voice}" if self.voice else voice_name(None)
                now = f"the voice {other}" if other else voice_name(None)
                self.screen.info(f"! {was} {problem}; using {now}")
                self.voice = other
                return self._synth(text)
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or "say made no sound")

    def _run(self):
        while True:
            with self.cond:
                while not self.closed and self.pending is None and self.asked is None and self.upcoming is None:
                    self.cond.wait()
                if self.closed:
                    return
                keep = self.asked is not None
                if keep:
                    text, self.asked, speak, pcm = self.asked, None, True, None
                    self.busy = True
                elif self.pending is not None:
                    text, self.pending, speak = self.pending, None, True
                    pcm = self.made.pop(text, None)
                    if self.upcoming == text:
                        self.upcoming = None
                    self.busy = True
                else:
                    text, self.upcoming, speak, pcm = self.upcoming, None, False, None
            try:
                if pcm is None:
                    t = time.monotonic()
                    pcm = self._synth(text)
                    if speak and time.monotonic() - t > 0.6 and not self.warned_slow:
                        self.warned_slow = True
                        self.screen.info(f"! the voice took {time.monotonic() - t:.1f}s to make a line; "
                                         "--voice Fred (the robot) is the fastest")
                if speak:
                    self._play(pcm, keep)
                else:
                    with self.cond:
                        self.made = {text: pcm}
            except Exception as e:  # keep captioning even if one line can't be said
                if not self.closed:
                    self.screen.info(f"! couldn't say a line: {e}")
            finally:
                if speak:
                    with self.cond:
                        self.busy = False

    def _play(self, pcm, keep=False):
        # Let the line before finish. If a newer line comes in meanwhile, skip this one (it stays
        # on screen) so the voice keeps up with the call instead of falling behind. A word you
        # looked up (keep) is never skipped.
        with self.cond:
            while not self.closed and (keep or self.pending is None) and time.monotonic() < self.busy_until - 0.1:
                self.cond.wait(max(0.01, self.busy_until - 0.1 - time.monotonic()))
            behind = time.monotonic() < self.busy_until - 0.1
            if self.closed or len(pcm) == 0 or (behind and self.pending is not None and not keep):
                return
        try:
            self.proc.stdin.write(struct.pack("<i", len(pcm)) + pcm.astype("<f4").tobytes())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return  # _watch reports it
        self.busy_until = max(time.monotonic(), self.busy_until) + len(pcm) / self.rate

    def _watch(self):
        self.proc.wait()
        if self.closed:
            return
        self.failed = True
        if self.switched:
            self._audio("set-output", self.previous)
            self.switched = False
            self.screen.info("! the voice stopped; the call plays in both ears again")
        else:
            self.screen.info("! the voice stopped")

    def drain(self, limit=30.0):
        """Wait for the lines already handed over to be said (used at the end of a file)."""
        end = time.monotonic() + limit
        while time.monotonic() < end and not self.failed:
            with self.cond:
                idle = self.pending is None and self.asked is None and not self.busy
            if idle and time.monotonic() >= self.busy_until:
                return
            time.sleep(0.05)

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()
        if self.switched:  # back to both ears first, so the call never goes quiet
            self._audio("set-output", self.previous)
            self.switched = False
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except self._sp.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(os.path.dirname(self.wav), ignore_errors=True)


class WordLookup:
    """The word lookup (lookup.py --serve) in a process of its own, on the CPU only: highlight
    Japanese (iTerm2 copies what you highlight; ⌘C works anywhere) and its words, a translation
    and an explanation show among the captions. With --lookup-voice the meaning is also said in
    your left ear (off by default: it would come between the lines the voice is saying)."""

    LOG = os.path.join(tempfile.gettempdir(), "call-captions-lookup.log")

    def __init__(self, screen, speak=False):
        import subprocess

        self._sp = subprocess
        self.screen, self.speak, self.voice, self.closed = screen, speak, None, False
        here = os.path.dirname(os.path.abspath(__file__))
        with open(self.LOG, "w") as log:
            # Its own session: Ctrl+C reaches only the captioner, which then closes this.
            self.proc = subprocess.Popen([sys.executable, os.path.join(here, "lookup.py"), "--serve"],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, text=True,
                                         start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                if line.strip():
                    self.screen.info(line.rstrip())
                continue
            if "ready" in event:
                only = "" if event.get("models") else " (dictionary only)"
                hear = " and hear" if self.speak else ""
                self.screen.info(f"{RESET}{BOLD}Word lookup on{RESET}{DIM}{only}: highlight Japanese in the captions "
                                 f"(or copy it) to see{hear} what it means.")
                continue
            self.screen.lookup(event)
            voice = self.voice
            if voice is not None and event.get("say"):
                voice.say(event["say"], lookup=True)
        self.proc.wait()
        if not self.closed:
            self.screen.info(f"{RESET}{YELLOW}! The word lookup stopped (what happened: {self.LOG}){RESET}")

    def close(self):
        self.closed = True
        if self.proc.poll() is None:
            self.proc.terminate()  # it stops its CPU models and exits
            try:
                self.proc.wait(timeout=5)
            except self._sp.TimeoutExpired:
                self.proc.kill()  # its models' watchdog still stops them


# ---------------------------------------------------------------- audio sources


def list_devices():
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}  ({d['max_input_channels']} ch, {int(d['default_samplerate'])} Hz)")


def find_device(name):
    import sounddevice as sd

    if name is None:
        return None
    if name.isdigit():
        return int(name)
    matches = [
        (i, d["name"])
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower()
    ]
    if not matches:
        sys.exit(f"No input device matching {name!r}. Run with --list-devices to see them.")
    if len(matches) > 1:
        others = ", ".join(n for _, n in matches[1:])
        print(
            f"Using {matches[0][1]!r} (also matched: {others}). It must be the BlackHole ticked in your "
            f'Multi-Output Device; otherwise pass e.g. --device "{matches[1][1]}".',
            file=sys.stderr,
        )
    return matches[0][0]


def start_device(device, q):
    import sounddevice as sd
    import soxr

    dev = find_device(device)
    info = sd.query_devices(dev, "input")
    rate = int(info["default_samplerate"])
    resampler = soxr.ResampleStream(rate, SR, 1, dtype="float32")

    def callback(indata, frames, time_info, status):
        mono = indata.mean(axis=1, dtype=np.float32)
        q.put(resampler.resample_chunk(mono))

    stream = sd.InputStream(
        device=dev,
        channels=min(2, info["max_input_channels"]),
        samplerate=rate,
        dtype="float32",
        blocksize=int(rate * 0.032),
        callback=callback,
    )
    stream.start()
    return stream, info["name"]


def read_file(path):
    import av

    chunks = []
    with av.open(path) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    return np.concatenate(chunks).astype(np.float32)


def feed_file(pcm, q, realtime):
    step = FRAME * 4
    for i in range(0, len(pcm), step):
        q.put(pcm[i : i + step])
        if realtime:
            time.sleep(step / SR)
    q.put(np.zeros(SR, np.float32))  # trailing silence closes the last utterance
    q.put(None)


# ---------------------------------------------------------------- main loop


class Captioner:
    def __init__(self, args, asr, mt, screen, voice=None):
        import torch
        from silero_vad import load_silero_vad

        self.args, self.asr, self.mt, self.screen, self.voice = args, asr, mt, screen, voice
        # Everything runs in this one thread: MLX crashes (segfault) when a second thread uses
        # it, which an earlier two-lane turbo did on a real Mac.
        self.quick_end = getattr(args, "quick_end", False)  # finish at 。？ after the short pause
        self.on_line = None  # bench: called with the timings of every finished line
        self.frames_seen = self.last_voiced = 0
        self._torch = torch
        torch.set_num_threads(1)  # VAD is tiny; extra threads only add overhead
        self.vad = load_silero_vad()
        self.end_frames = int(args.end_silence / FRAME_S)
        self.early_frames = int(args.early / FRAME_S) if 0 < args.early < args.end_silence else 0
        self.pause_frames = int(0.15 / FRAME_S)
        self.soft_max = int(args.soft_max / FRAME_S)
        self.hard_max = int(args.hard_max / FRAME_S)
        self.min_frames = int(0.25 / FRAME_S)
        self.preroll = deque(maxlen=int(0.3 / FRAME_S))
        self.utterances = None  # a list here collects the utterances instead (bench)
        self.reset()

    def reset(self):
        self.utt, self.in_speech, self.silence, self.voiced = [], False, 0, 0
        self.early = None  # (frames, result) transcribed at a short pause, in case it ends there
        self.last_partial = self.last_draft = time.monotonic()

    def speech_prob(self, frame):
        return self.vad(self._torch.from_numpy(frame), SR).item()

    def push_frame(self, frame):
        p = self.speech_prob(frame)
        thr = self.args.vad_threshold
        self.frames_seen += 1
        if not self.in_speech:
            self.preroll.append(frame)
            if p >= thr:
                self.in_speech, self.utt, self.silence, self.voiced = True, list(self.preroll), 0, 1
                self.preroll.clear()
                self.last_partial = self.last_draft = time.monotonic()
                self.last_voiced = self.frames_seen
            return
        self.utt.append(frame)
        early = False
        if p >= thr:
            self.silence = 0
            self.voiced += 1
            self.last_voiced = self.frames_seen
            self.early = None  # they kept talking: an early guess is stale
        elif p < thr - 0.15:
            self.silence += 1
            early = self.silence == self.early_frames
        n = len(self.utt)
        if (
            self.silence >= self.end_frames
            or (n >= self.soft_max and self.silence >= self.pause_frames)
            or n >= self.hard_max
        ):
            self.finish()
        elif early and self.voiced >= self.min_frames and self.utterances is None:
            self.transcribe_early()

    def kept_frames(self):
        return len(self.utt) - max(0, self.silence - 3)  # drop trailing silence

    def hear(self, audio):
        """(Japanese, asr seconds), or None if there was no real speech."""
        t0 = time.monotonic()
        ja = self.asr(audio, final=True)
        if looks_bogus(ja, self.args.hint):
            return None
        return ja, time.monotonic() - t0

    def translate(self, ja):
        """(English, mt seconds)."""
        t0 = time.monotonic()
        en = self.mt(ja) if self.mt is not None else ""
        return en, time.monotonic() - t0

    def recognize(self, audio, show=False):
        """(Japanese, English, asr seconds, mt seconds), or None if there was no real speech."""
        heard = self.hear(audio)
        if heard is None:
            return None
        if show:
            self.screen.final_ja(heard[0])
        en, mt_s = self.translate(heard[0])
        return heard[0], en, heard[1], mt_s

    def speech_end(self):
        return self.last_voiced * FRAME_S  # audio time the person stopped talking

    def shown(self, ja, en, asr_s, mt_s, early, end_s):
        self.screen.final_en(ja, en, asr_s, mt_s, early)
        if self.on_line is not None:
            self.on_line(dict(ja=ja, en=en, asr_s=asr_s, mt_s=mt_s, early=early, end_s=end_s,
                              shown=time.monotonic()))
        if self.voice and en:
            self.voice.say(en)

    def transcribe_early(self):
        """At a short pause, transcribe and translate before knowing whether the person has
        finished. If the pause turns out to end the utterance, finish() uses this (the audio is
        the same) and the line appears that much sooner; if they keep talking, it was an
        in-progress update."""
        n = self.kept_frames()
        self.last_partial = time.monotonic()
        result = self.recognize(np.concatenate(self.utt[:n]))
        self.early = (n, result)
        if result is None:
            return
        ja = result[0]
        if not self.args.no_partials:
            self.screen.partial(ja, result[1] or None)
        if self.voice and result[1]:
            self.voice.prepare(result[1])
        if self.quick_end and ends_sentence(ja):
            self.finish()  # a finished sentence: no need to wait out the rest of the pause

    def finish(self):
        n, voiced, early, end_s = self.kept_frames(), self.voiced, self.early, self.speech_end()
        frames = self.utt[:n]
        self.reset()
        if voiced < self.min_frames:
            self.screen.clear()
            return
        if self.utterances is not None:
            self.utterances.append(np.concatenate(frames))
            return
        reuse = early is not None and early[0] == n  # nothing was said since the short pause
        if reuse:
            result = early[1]
            if result is not None:
                self.screen.final_ja(result[0])
        else:
            result = self.recognize(np.concatenate(frames), show=True)
        if result is None:
            self.screen.clear()
            return
        ja, en, asr_s, mt_s = result
        self.shown(ja, en, asr_s, mt_s, reuse, end_s)

    def maybe_partial(self):
        now = time.monotonic()
        if not self.in_speech or self.voiced < int(1.0 / FRAME_S):
            return
        if self.silence >= 2:  # a pause may be starting; keep the model free for the final text
            return
        if now - self.last_partial < self.args.partial_every:
            return
        self.last_partial = now
        ja = self.asr(np.concatenate(self.utt), final=False)
        if looks_bogus(ja, self.args.hint):
            return
        en = None
        long_enough = len(self.utt) * FRAME_S >= self.args.draft_after
        if self.mt is not None and self.args.draft_after > 0 and long_enough and now - self.last_draft >= 2.5:
            self.last_draft = now
            en = self.mt(ja)
        self.screen.partial(ja, en)

    def run(self, q, realtime=True):
        buf = np.zeros(0, np.float32)
        warned = not realtime
        while True:
            chunk = q.get()
            if chunk is None:
                if self.in_speech:
                    self.finish()
                return
            buf = np.concatenate([buf, chunk])
            while len(buf) >= FRAME:
                self.push_frame(buf[:FRAME])
                buf = buf[FRAME:]
            backlog = q.qsize() * 0.032
            if backlog > 3 and not warned:
                self.screen.info("! falling behind real time; try --asr parakeet or --no-partials")
                warned = True
            if q.empty() and not self.args.no_partials:
                self.maybe_partial()


def split_sentences(args, pcm):
    """The utterances the live captioner would cut the audio into."""
    split_args = argparse.Namespace(**{**vars(args), "no_partials": True, "early": 0})
    splitter = Captioner(split_args, None, None, Screen(False, False, None))
    splitter.utterances = []
    q = queue.Queue()
    feed_file(pcm, q, realtime=False)
    splitter.run(q, realtime=False)
    return splitter.utterances


def bench_models(args, utts, mt):
    """Each speech model on the same sentences, timed, with the translation of what it heard."""
    summary = {}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"\n{BOLD}{name}{RESET}  {DIM}{ASR_MODELS.get(name, name)} (downloads once if new){RESET}", flush=True)
        try:
            asr = make_asr(name, args.hint)
            asr(np.zeros(SR, np.float32))
        except Exception as e:
            print(f"  couldn't load it: {e}")
            continue
        times = []
        for audio in utts:
            t = time.monotonic()
            ja = asr(audio, final=True)
            times.append(time.monotonic() - t)
            print(f"  {times[-1]:5.2f}s  {CYAN}{ja}{RESET}", flush=True)
            if mt is not None and ja:
                print(f"          {YELLOW}➜ {mt(ja)}{RESET}", flush=True)
        summary[name] = (sum(times) / len(times), max(times))
        if hasattr(asr, "close"):
            asr.close()
        del asr
        gc.collect()
    return summary


class BenchScreen(Screen):
    """Prints only finished lines, with how long after the end of speech they appeared."""

    def __init__(self):
        super().__init__(False, False, None)

    def partial(self, ja, en=None):
        pass

    def final_ja(self, ja):
        pass

    def final_en(self, ja, en, asr_s, mt_s, early=False):
        pass


# The live races bench runs: (speech model, finish lines at sentence ends, what it is)
RACES = {
    "control": ("parakeet", False, "as up to 1.6: Parakeet on the GPU, a line after the full 0.5 s pause"),
    "quick": ("parakeet", True, "as in 1.7, now --no-turbo: Parakeet on the GPU; a line finishes as soon as a "
                                "sentence ends"),
    "turbo": ("parakeet-ane", True, "the default since 1.8: quick, with Parakeet's encoder on the Neural Engine in "
                                    "a helper process"),
}


def race(args, asr, mt, fast, pcm):
    """Play the recording in real time through the whole live pipeline, like a call, and time
    each line from the moment the person stopped talking to the English on screen."""
    overrides = dict(no_partials=False, quick_end=fast, early=0.15 if fast else 0.25)
    c = Captioner(argparse.Namespace(**{**vars(args), **overrides}), asr, mt, BenchScreen())
    lines = []
    c.on_line = lines.append
    q = queue.Queue()
    t0 = time.monotonic()

    def feed():  # like a live device: each 32 ms block arrives once its audio has played
        step = FRAME
        for i in range(0, len(pcm), step):
            time.sleep(max(0.0, t0 + (i + step) / SR - time.monotonic()))
            q.put(pcm[i : i + step])
        q.put(np.zeros(SR, np.float32))
        q.put(None)

    threading.Thread(target=feed, daemon=True).start()
    c.run(q)
    for line in lines:
        line["latency"] = line["shown"] - (t0 + line["end_s"])
        print(f"  {line['latency']:5.2f}s  {YELLOW}➜ {line['en']}{RESET}  {DIM}{line['ja']}{RESET}", flush=True)
    return lines


def race_child(args):
    """One live race (--race MODE) in a process of its own, set up the way a real session is, so a
    crash ends only that race and the bench can say where it happened."""
    asr_name, fast, _ = RACES[args.race]
    pcm = read_file(args.bench)
    mt = None
    if args.mt != "none":
        mt = Translator(args.mt)
        mt("もしもし、聞こえますか？")
    asr = make_asr(asr_name, "")
    if isinstance(asr, NeuralEngineHelper):
        asr.on_fallback = lambda message: print("  " + message, flush=True)
    asr(np.zeros(SR, np.float32))
    print("RACE_START", flush=True)
    lines = race(args, asr, mt, fast, pcm)
    print("RACE_JSON " + json.dumps(lines, ensure_ascii=False), flush=True)


def run_race(args, mode, during=None):
    """Run one race in a child process; its lines, or None (and where it crashed) if it failed."""
    import subprocess

    print(f"{BOLD}{mode}{RESET} {DIM}({RACES[mode][2]}){RESET}", flush=True)
    command = [sys.executable, os.path.abspath(__file__), "--race", mode, "--bench", args.bench, "--mt", args.mt]
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=err, text=True)
        lines = None
        for line in proc.stdout:
            if line.startswith("RACE_START") and during:
                threading.Thread(target=during, args=(proc,), daemon=True).start()
            elif line.startswith("RACE_JSON "):
                lines = json.loads(line[len("RACE_JSON "):])
            elif line.startswith("  "):
                print(line, end="", flush=True)
        proc.wait()
        if lines is None:
            err.seek(0)
            log = [l.rstrip() for l in err.read().splitlines() if l.strip()]
            start = next((i for i, l in enumerate(log) if l.startswith(("Fatal Python error", "Traceback"))), None)
            where = log[start:] if start is not None else log[-10:]
            where = [l for l in where if not l.startswith("Extension modules")]
            what = "crashed (segmentation fault)" if proc.returncode == -11 else f"stopped (exit {proc.returncode})"
            print(f"  {YELLOW}! {mode} {what}. Where it happened (send this):{RESET}")
            for l in where[:40]:
                print(f"    {l}")
        return lines


def start_lookup():
    """The word lookup in its own process on the CPU, reading phrases from stdin (for the bench)."""
    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.Popen([sys.executable, os.path.join(here, "lookup.py"), "--stdin", "--quiet"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ready = proc.stdout.readline().strip()
    if ready != "LOOKUP_READY":
        proc.kill()
        raise RuntimeError("the lookup didn't start (is llama.cpp installed?)")
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()  # drain its output
    return proc


def bench(args):
    """Control (the current pipeline) against turbo (Parakeet's encoder on the Neural Engine, lines
    finishing at sentence ends), then the word lookup on the CPU, and
    turbo again while lookups run, to show they don't slow the live captions down."""
    import subprocess

    pcm = read_file(args.bench)
    utts = split_sentences(args, pcm)
    print(f"{args.bench}: {len(pcm) / SR:.0f} s of audio, {len(utts)} sentences")
    if not utts:
        return
    mt = None
    if args.mt != "none":
        print(f"{DIM}Loading the translation model ({MT_MODELS.get(args.mt, args.mt)}) …{RESET}", flush=True)
        mt = Translator(args.mt)
        mt("もしもし、聞こえますか？")

    print(f"\n{BOLD}1. Speech models on the same sentences{RESET}")
    models = bench_models(args, utts, mt)

    del mt
    gc.collect()

    print(f"\n{BOLD}2. Live race: the recording played in real time, like a call{RESET}")
    print(f"{DIM}   each in a process of its own, as a real session runs{RESET}")
    results = {}
    for mode in RACES:
        lines = run_race(args, mode)
        if lines:
            results[mode] = lines

    print(f"\n{BOLD}3. Word lookup on the CPU{RESET} {DIM}(dictionary, translation, explanation){RESET}", flush=True)
    heard = next(iter(results.values()), [])
    phrases = [l["ja"] for l in heard][:4] or ["伺ってもよろしいですか", "恐れ入りますが"]
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "lookup.py"), "--bench", *phrases],
                       capture_output=True, text=True)
    lookups = []
    for line in r.stdout.splitlines():
        if line.startswith("BENCH_JSON "):
            lookups = json.loads(line[len("BENCH_JSON "):])
        elif not line.startswith("BENCH"):
            print(line)
    if r.returncode != 0 and not lookups:
        print(f"{YELLOW}! the lookup didn't run: {(r.stderr or '').strip().splitlines()[-1:]}{RESET}")

    best = next((m for m in ("turbo", "quick") if m in results), None)
    if best and lookups:
        print(f"\n{BOLD}4. {best} again, with a lookup every second on the CPU{RESET}", flush=True)
        proc = start_lookup()

        def keep_looking_up(child):
            i = 0
            try:
                while proc.poll() is None and child.poll() is None:
                    proc.stdin.write(phrases[i % len(phrases)] + "\n")
                    proc.stdin.flush()
                    i += 1
                    time.sleep(1.0)
            except (OSError, ValueError):
                pass  # the lookup was closed

        try:
            lines = run_race(args, best, during=keep_looking_up)
            if lines:
                results[f"{best} + lookups"] = lines
        finally:
            proc.terminate()  # it stops its models and exits, skipping any phrases still queued
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()  # its models' watchdog still stops them

    print(f"\n{BOLD}Summary{RESET}")
    for name, (avg, worst) in models.items():
        print(f"  speech model {name:<13} {avg:5.2f} s average  {worst:5.2f} s slowest  per sentence")
    print(f"  {DIM}live, from the end of speech to the English on screen:{RESET}")
    base = None
    for name, lines in results.items():
        if not lines:
            continue
        lat = [l["latency"] for l in lines]
        avg = sum(lat) / len(lat)
        base = base or avg
        gain = f"  {base / avg:4.1f}x sooner than control" if name != "control" else ""
        print(f"  {name:<26} {avg:5.2f} s average  {max(lat):5.2f} s slowest{gain}")
    def words(lines):  # what was heard, however it was split into lines and punctuated
        return re.sub(r"[\s、。，．,.？！?!]", "", "".join(l["ja"] for l in lines))

    for mode in ("quick", "turbo"):
        if mode in results and "control" in results:
            same = words(results[mode]) == words(results["control"])
            print(f"  {DIM}{mode}: {len(results[mode])} lines, "
                  + ("the same words as control" if same else "some words differ from control (see the lines above)")
                  + RESET)
    if lookups:
        keys = [k for k in lookups[0]]
        print("  lookup (CPU)              " + "  ".join(
            f"{k} {sum(t.get(k, 0) for t in lookups) / len(lookups):.2f} s" for k in keys))


def load_asr(args, screen):
    """The speech model; if the Neural Engine one can't load, Parakeet on the GPU instead."""
    screen.info(f"Loading speech model ({ASR_MODELS.get(args.asr, args.asr)}) …")
    try:
        return make_asr(args.asr, args.hint)
    except Exception as e:
        if args.asr != "parakeet-ane":
            raise
        screen.info(f"{RESET}{YELLOW}! The Neural Engine model didn't load ({e}); using Parakeet on the GPU{RESET}")
        args.asr = "parakeet"
        return make_asr("parakeet", args.hint)


def main():
    faulthandler.enable()  # on a crash, print where it happened
    ap = argparse.ArgumentParser(prog="call-captions", description="Live Japanese -> English call captions, fully on-device.")
    ap.add_argument("--device", default="BlackHole", help="input device name (substring) or index [BlackHole]")
    ap.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    ap.add_argument("--file", help="caption an audio file instead of a live device (for testing)")
    ap.add_argument("--fast", action="store_true", help="with --file: process as fast as possible, not in real time")
    ap.add_argument("--asr", default="parakeet",
                    help="parakeet | kotoba | qwen3 | qwen3-small | cpu[:model] | <hf repo> [parakeet]")
    ap.add_argument("--mt", default="lfm2", help="lfm2 | plamo | none | <mlx-lm hf repo> [lfm2]")
    ap.add_argument("--hint", default="",
                    help="names to expect, e.g. '株式会社サンプル 山田' (Kotoba and Qwen3 only; biases spelling)")
    ap.add_argument("--english-only", action="store_true", help="hide the Japanese lines")
    ap.add_argument("--no-timing", action="store_true", help="hide per-line timing")
    ap.add_argument("--no-partials", action="store_true", help="don't show text while the person is still talking")
    ap.add_argument("--partial-every", type=float, default=1.0, help="seconds between in-progress updates [1.0]")
    ap.add_argument("--draft-after", type=float, default=4.0,
                    help="translate in-progress text once an utterance is this long; 0 disables [4.0]")
    ap.add_argument("--speak", nargs="?", const="split", default="split", choices=["split", "mix"],
                    help="say the English in your left ear (on by default; headphones only). split: the call "
                         "only in your right ear [default], mix: the call stays in both ears")
    ap.add_argument("--no-speak", dest="speak", action="store_const", const=None, help="captions only, no voice")
    ap.add_argument("--voice", default="",
                    help="a Mac voice by name, e.g. Fred (the robot, fastest) or Samantha; list them with: say -v '?'. "
                         "Default: the Mac's system voice, Siri if you chose a Siri voice there")
    ap.add_argument("--voice-speed", type=float, default=2.0,
                    help="how much faster than normal the voice talks, at the same pitch: 1 is normal, 2 twice as "
                         "fast, 0.5 to 3 [2]")
    ap.add_argument("--rate", type=int,
                    help="words per minute for the voice itself, before --voice-speed; not every voice follows it "
                         "[the Mac's speaking rate]")
    ap.add_argument("--test-voice", action="store_true", help="say a test line in your left ear, then exit")
    ap.add_argument("--no-lookup", action="store_true", help="no word lookup when you highlight Japanese")
    ap.add_argument("--lookup-voice", action="store_true",
                    help="also say each word lookup's meaning in your left ear, between the captions' lines")
    ap.add_argument("--no-lookup-voice", dest="lookup_voice", action="store_false",
                    help="show word lookups without saying them (the default)")
    ap.add_argument("--voice-volume", type=float, default=1.0, help="loudness of the voice, 0 to 2 [1.0]")
    ap.add_argument("--turbo", action="store_true", default=True,
                    help="speech recognition with Parakeet's encoder on the Neural Engine, in a helper process; the "
                         "GPU takes over at once if it stops (the default since 1.8)")
    ap.add_argument("--no-turbo", dest="turbo", action="store_false",
                    help="speech recognition on the GPU only (the default before 1.8; a few words can differ)")
    ap.add_argument("--no-quick-end", dest="quick_end", action="store_false",
                    help="wait out the full pause before finishing a line, as before 1.7")
    ap.add_argument("--quick-end", dest="quick_end", action="store_true", default=True,
                    help="finish a line as soon as a sentence ends (。？), after a 0.15 s pause (the default "
                         "since 1.7)")
    ap.add_argument("--end-silence", type=float, default=0.5, help="pause (s) that ends an utterance [0.5]")
    ap.add_argument("--early", type=float,
                    help="start the final transcription after this pause (s), before it is known to end the "
                         "utterance; same text, sooner. 0 disables [0.15; 0.25 with --no-quick-end]")
    ap.add_argument("--soft-max", type=float, default=8.0, help="after this many seconds, cut at any short pause [8]")
    ap.add_argument("--hard-max", type=float, default=15.0, help="always cut an utterance at this length [15]")
    ap.add_argument("--vad-threshold", type=float, default=0.5, help="speech probability threshold [0.5]")
    ap.add_argument("--log", help="append a transcript (time, Japanese, English) to this file")
    ap.add_argument("--prefetch", action="store_true", help="download and warm up the models, then exit")
    ap.add_argument("--download-only", action="store_true", help="download the models without loading them, then exit")
    ap.add_argument("--bench", metavar="AUDIO", help="compare the speech models on an audio file, then exit")
    ap.add_argument("--race", choices=list(RACES), help=argparse.SUPPRESS)  # bench runs each race this way
    ap.add_argument("--neural-engine-helper", metavar="REPO", help=argparse.SUPPRESS)  # see NeuralEngineHelper
    ap.add_argument("--models", default="parakeet,parakeet-ane",
                    help="speech models for --bench, e.g. parakeet,parakeet-ane,qwen3,kotoba [parakeet,parakeet-ane]")
    argv = sys.argv[1:]
    options = os.environ.pop("CALL_CAPTIONS_OPTIONS", "")  # options.txt, set by call-captions
    if os.path.isfile(options):
        import shlex

        with open(options, encoding="utf-8") as f:
            lines = [line for line in f if not line.lstrip().startswith("#")]
        try:
            argv = [a for line in lines for a in shlex.split(line)] + argv  # typed options win
        except ValueError as e:
            ap.error(f"{options}: {e}")
    args = ap.parse_args(argv)
    if not 0.5 <= args.voice_speed <= 3:
        ap.error("--voice-speed goes from 0.5 to 3")

    if args.neural_engine_helper:
        neural_engine_helper(args.neural_engine_helper)
        return

    if args.race:
        race_child(args)
        return

    if args.bench:
        bench(args)
        return

    if args.test_voice:
        screen = Screen(False, False, None)
        try:
            voice = Voice(args.speak or "split", args.voice, args.rate, args.voice_speed, args.voice_volume, screen)
        except VoiceOff as e:
            sys.exit(f"Voice off: {e}")
        import subprocess

        try:
            print(f"Voice: {voice_label(args)}", flush=True)
            print(f"Left ear ({voice.hear_name}): the English voice …", flush=True)
            voice.say("Left ear. This is where the English goes.")
            voice.drain()
            if voice.switched:  # the Mac's output is BlackHole now: this plays the way a call would
                time.sleep(0.3)
                print("Right ear: where the call plays …", flush=True)
                subprocess.run(["say", *(["-r", str(args.rate)] if args.rate else []), "Right ear. This is where the call goes."],
                               stdin=subprocess.DEVNULL, timeout=30)
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            voice.close()  # puts the Mac's sound output back
        print("Heard both? Then it works. If the right ear was silent, use --speak mix (the call in both ears).")
        if not args.voice and not siri_is_system_voice():
            print(SIRI_HOW)
        return

    if args.list_devices:
        list_devices()
        return

    if args.download_only:
        from huggingface_hub import snapshot_download

        repos = list(dict.fromkeys([ASR_MODELS.get(args.asr, args.asr), ASR_MODELS["parakeet-ane"]]))  # + turbo's
        if args.mt != "none":
            repos.append(MT_MODELS.get(args.mt, args.mt))
        for repo in repos:
            if "/" in repo:  # a Hugging Face repo, not a local path or model alias
                print(f"    {repo}", flush=True)
                snapshot_download(repo, allow_patterns=ANE_FILES if "coreml" in repo.lower() else None)
        return

    screen = Screen(args.english_only, not args.no_timing, args.log)
    lookup = None
    if not args.no_lookup and not args.prefetch:
        lookup = WordLookup(screen, args.lookup_voice and bool(args.speak))  # loads its CPU models meanwhile
    if args.turbo and args.asr == "parakeet":
        args.asr = "parakeet-ane"
    if args.early is None:
        args.early = 0.15 if args.quick_end else 0.25
    asr = load_asr(args, screen)
    if isinstance(asr, NeuralEngineHelper):
        asr.on_fallback = screen.info
    if args.hint and isinstance(asr, (ParakeetASR, NeuralEngineHelper)):
        screen.info("  --hint has no effect on Parakeet; --asr qwen3 or --asr kotoba use it")
    t = time.monotonic()
    asr(np.zeros(SR, np.float32))
    screen.info(f"  ready, warm-up {time.monotonic() - t:.1f}s")
    if isinstance(asr, NeuralEngineHelper):
        screen.info("  turbo: Parakeet's encoder on the Neural Engine (--no-turbo: the GPU only)")

    mt = None
    if args.mt != "none":
        screen.info(f"Loading translation model ({MT_MODELS.get(args.mt, args.mt)}) …")
        mt = Translator(args.mt)
        t = time.monotonic()
        mt("もしもし、聞こえますか？")
        screen.info(f"  ready, warm-up {time.monotonic() - t:.1f}s")

    if args.prefetch:
        return

    voice = None
    if args.speak:
        try:
            voice = Voice(args.speak, args.voice, args.rate, args.voice_speed, args.voice_volume, screen)
            call = "the call in your right ear" if args.speak == "split" else "the call in both ears"
            screen.info(f"{RESET}{BOLD}Voice on{RESET}{DIM} ({voice_label(args)}): English in your left ear, {call} "
                        f"({voice.hear_name}). --no-speak turns it off.")
            if voice_name(args.voice) == "the Mac's system voice":
                screen.info("  " + SIRI_HOW)
        except VoiceOff as e:
            screen.info(f"{RESET}{YELLOW}! Voice off: {e}{RESET}")
    if lookup is not None and args.lookup_voice:
        lookup.voice = voice

    captioner = Captioner(args, asr, mt, screen, voice)
    q = queue.Queue()
    stream = None
    if args.file:
        pcm = read_file(args.file)
        threading.Thread(target=feed_file, args=(pcm, q, not args.fast), daemon=True).start()
        screen.info(f"Captioning {args.file} ({len(pcm) / SR:.1f}s). Ctrl+C to stop.\n")
    else:
        stream, name = start_device(args.device, q)
        screen.info(f"Listening on: {name}. Ctrl+C to stop.\n")

    try:
        captioner.run(q, realtime=not (args.file and args.fast))
        if voice is not None:
            voice.drain()  # the end of a file: let the last line be said
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear()
        if lookup is not None:
            lookup.close()
        if voice is not None:
            voice.close()
        if stream is not None:
            stream.stop()
            stream.close()


if __name__ == "__main__":
    main()
