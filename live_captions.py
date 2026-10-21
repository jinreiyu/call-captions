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
import gc
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
    "parakeet": "mlx-community/parakeet-tdt_ctc-0.6b-ja",  # NVIDIA Parakeet TDT-CTC 0.6B Japanese
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


class ParakeetASR:
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
        return r.text.strip(), (r.sentences[-1].end if r.sentences else 0.0)

    def __call__(self, audio, final=True):
        # Parakeet-ja often stops after one sentence when another follows a short pause (seen on
        # real speech: "…幸いです。" then nothing for 「よろしくお願いします」). So whenever it
        # stops with speech-length audio left over, carry on from where it stopped.
        texts, start = [], 0
        for _ in range(6):
            text, end = self._once(audio[start:])
            if not re.search(r"[\w぀-ヿ一-鿿]", text) or end <= 0:
                break
            texts.append(text)
            start += int(end * SR)
            if len(audio) - start < int(0.4 * SR):
                break
        return "".join(texts)


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


class VoiceOff(Exception):
    """The voice can't run; captions carry on without it."""


class Voice:
    """Says each English line in your left ear with the Mac's built-in speech (`say`).

    split: the call plays only in your right ear. The Mac's sound output becomes BlackHole, and
           call-captions-audio plays BlackHole to your right ear and the English to your left.
    mix:   the call keeps playing in both ears through "Call Captions Output"; the English is
           added in your left ear.
    The English only goes to your headset, never into BlackHole or the call.
    """

    def __init__(self, mode, voice, wpm, volume, screen):
        import subprocess

        self._sp = subprocess
        self.screen, self.voice, self.wpm, self.gain = screen, voice, wpm, volume
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

    def say(self, text):
        with self.cond:
            self.pending = text
            self.cond.notify()

    def _synth(self, text):
        command = ["say", "-r", str(self.wpm), "-o", self.wav, f"--data-format=LEI16@{self.rate}", "-f", "-"]
        if self.voice:
            command[1:1] = ["-v", self.voice]
        r = self._sp.run(command, input=text.encode(), capture_output=True, timeout=30, start_new_session=True)
        if r.returncode != 0 and self.voice:
            self.screen.info(f"! the voice {self.voice!r} isn't on this Mac; using the default voice")
            self.voice = None
            return self._synth(text)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace").strip() or "say failed")
        pcm, rate = read_wav(self.wav)
        if rate != self.rate:
            import soxr

            pcm = soxr.resample(pcm, rate, self.rate)
        loud = np.flatnonzero(np.abs(pcm) > 1e-3)
        if len(loud) == 0:
            return pcm[:0]
        start = max(0, loud[0] - self.rate // 100)  # skip the silence `say` starts with
        return (pcm[start : loud[-1] + 1] * self.gain).astype(np.float32)

    def _run(self):
        while True:
            with self.cond:
                while not self.closed and self.pending is None and self.upcoming is None:
                    self.cond.wait()
                if self.closed:
                    return
                if self.pending is not None:
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
                                         "a compact voice such as --voice Samantha is faster")
                if speak:
                    self._play(pcm)
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

    def _play(self, pcm):
        # Let the line before finish. If a newer line comes in meanwhile, skip this one (it stays
        # on screen) so the voice keeps up with the call instead of falling behind.
        with self.cond:
            while not self.closed and self.pending is None and time.monotonic() < self.busy_until - 0.1:
                self.cond.wait(max(0.01, self.busy_until - 0.1 - time.monotonic()))
            behind = time.monotonic() < self.busy_until - 0.1
            if self.closed or len(pcm) == 0 or (behind and self.pending is not None):
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
                idle = self.pending is None and not self.busy
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
        if not self.in_speech:
            self.preroll.append(frame)
            if p >= thr:
                self.in_speech, self.utt, self.silence, self.voiced = True, list(self.preroll), 0, 1
                self.preroll.clear()
                self.last_partial = self.last_draft = time.monotonic()
            return
        self.utt.append(frame)
        early = False
        if p >= thr:
            self.silence = 0
            self.voiced += 1
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

    def recognize(self, audio, show=False):
        """(Japanese, English, asr seconds, mt seconds), or None if there was no real speech."""
        t0 = time.monotonic()
        ja = self.asr(audio, final=True)
        asr_s = time.monotonic() - t0
        if looks_bogus(ja, self.args.hint):
            return None
        if show:
            self.screen.final_ja(ja)
        t1 = time.monotonic()
        en = self.mt(ja) if self.mt is not None else ""
        return ja, en, asr_s, time.monotonic() - t1

    def transcribe_early(self):
        """At a short pause, transcribe and translate before knowing whether the person has
        finished. If the pause turns out to end the utterance, finish() uses this (the audio is
        the same) and the line appears that much sooner; if they keep talking, it was an
        in-progress update."""
        n = self.kept_frames()
        result = self.recognize(np.concatenate(self.utt[:n]))
        self.early = (n, result)
        self.last_partial = time.monotonic()
        if result is None:
            return
        if not self.args.no_partials:
            self.screen.partial(result[0], result[1] or None)
        if self.voice and result[1]:
            self.voice.prepare(result[1])

    def finish(self):
        n, voiced, early = self.kept_frames(), self.voiced, self.early
        frames = self.utt[:n]
        self.reset()
        if voiced < self.min_frames:
            self.screen.clear()
            return
        if self.utterances is not None:
            self.utterances.append(np.concatenate(frames))
            return
        if early is not None and early[0] == n:  # nothing was said since: use it
            result = early[1]
            if result is not None:
                self.screen.final_ja(result[0])
        else:
            early, result = None, self.recognize(np.concatenate(frames), show=True)
        if result is None:
            self.screen.clear()
            return
        ja, en, asr_s, mt_s = result
        self.screen.final_en(ja, en, asr_s, mt_s, early is not None)
        if self.voice and en:
            self.voice.say(en)

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


def bench(args):
    """Put the same sentences of an audio file through each speech model, translate them, and
    time every step, to choose the fastest model that is still right on your own audio."""
    pcm = read_file(args.bench)
    screen = Screen(False, False, None)
    args.no_partials, args.early = True, 0
    splitter = Captioner(args, None, None, screen)
    splitter.utterances = []
    q = queue.Queue()
    feed_file(pcm, q, realtime=False)
    splitter.run(q, realtime=False)
    utts = splitter.utterances
    print(f"{args.bench}: {len(pcm) / SR:.0f} s of audio, {len(utts)} sentences")
    if not utts:
        return
    mt = None
    if args.mt != "none":
        print(f"{DIM}Loading the translation model ({MT_MODELS.get(args.mt, args.mt)}) …{RESET}", flush=True)
        mt = Translator(args.mt)
        mt("もしもし、聞こえますか？")
    summary, mt_times = [], []
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
                t = time.monotonic()
                en = mt(ja)
                mt_times.append(time.monotonic() - t)
                print(f"          {YELLOW}➜ {en}{RESET}", flush=True)
        summary.append((name, sum(times) / len(times), max(times)))
        del asr
        gc.collect()
    print(f"\n{BOLD}Speech recognition, seconds per sentence after the person stops{RESET}")
    for name, avg, worst in summary:
        print(f"  {name:<12} {avg:5.2f} average  {worst:5.2f} slowest")
    if mt_times:
        print(f"  translation  {sum(mt_times) / len(mt_times):5.2f} average  {max(mt_times):5.2f} slowest")


def main():
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
    ap.add_argument("--voice", default="Fred",
                    help="Mac voice; list them with: say -v '?'. Fred is the classic robot voice [Fred]")
    ap.add_argument("--rate", type=int, default=240, help="speaking speed, words per minute [240]")
    ap.add_argument("--test-voice", action="store_true", help="say a test line in your left ear, then exit")
    ap.add_argument("--voice-volume", type=float, default=1.0, help="loudness of the voice, 0 to 2 [1.0]")
    ap.add_argument("--end-silence", type=float, default=0.5, help="pause (s) that ends an utterance [0.5]")
    ap.add_argument("--early", type=float, default=0.25,
                    help="start the final transcription after this pause (s), before it is known to end the "
                         "utterance; same text, sooner. 0 disables [0.25]")
    ap.add_argument("--soft-max", type=float, default=8.0, help="after this many seconds, cut at any short pause [8]")
    ap.add_argument("--hard-max", type=float, default=15.0, help="always cut an utterance at this length [15]")
    ap.add_argument("--vad-threshold", type=float, default=0.5, help="speech probability threshold [0.5]")
    ap.add_argument("--log", help="append a transcript (time, Japanese, English) to this file")
    ap.add_argument("--prefetch", action="store_true", help="download and warm up the models, then exit")
    ap.add_argument("--download-only", action="store_true", help="download the models without loading them, then exit")
    ap.add_argument("--bench", metavar="AUDIO", help="compare the speech models on an audio file, then exit")
    ap.add_argument("--models", default="parakeet,qwen3,kotoba",
                    help="speech models for --bench [parakeet,qwen3,kotoba]")
    args = ap.parse_args()

    if args.bench:
        bench(args)
        return

    if args.test_voice:
        screen = Screen(False, False, None)
        try:
            voice = Voice(args.speak or "split", args.voice, args.rate, args.voice_volume, screen)
        except VoiceOff as e:
            sys.exit(f"Voice off: {e}")
        import subprocess

        try:
            print(f"Left ear ({voice.hear_name}): the English voice …", flush=True)
            voice.say("Left ear. This is where the English goes.")
            voice.drain()
            if voice.switched:  # the Mac's output is BlackHole now: this plays the way a call would
                time.sleep(0.3)
                print("Right ear: where the call plays …", flush=True)
                subprocess.run(["say", "-r", str(args.rate), "Right ear. This is where the call goes."],
                               stdin=subprocess.DEVNULL, timeout=30)
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            voice.close()  # puts the Mac's sound output back
        print("Heard both? Then it works. If the right ear was silent, use --speak mix (the call in both ears).")
        return

    if args.list_devices:
        list_devices()
        return

    if args.download_only:
        from huggingface_hub import snapshot_download

        repos = [ASR_MODELS.get(args.asr, args.asr)]
        if args.mt != "none":
            repos.append(MT_MODELS.get(args.mt, args.mt))
        for repo in repos:
            if "/" in repo:  # a Hugging Face repo, not a local path or model alias
                print(f"    {repo}", flush=True)
                snapshot_download(repo)
        return

    screen = Screen(args.english_only, not args.no_timing, args.log)
    screen.info(f"Loading speech model ({ASR_MODELS.get(args.asr, args.asr)}) …")
    asr = make_asr(args.asr, args.hint)
    if args.hint and isinstance(asr, ParakeetASR):
        screen.info("  --hint has no effect on Parakeet; --asr qwen3 or --asr kotoba use it")
    t = time.monotonic()
    asr(np.zeros(SR, np.float32))
    screen.info(f"  ready, warm-up {time.monotonic() - t:.1f}s")

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
            voice = Voice(args.speak, args.voice, args.rate, args.voice_volume, screen)
            call = "the call in your right ear" if args.speak == "split" else "the call in both ears"
            screen.info(f"{RESET}{BOLD}Voice on{RESET}{DIM}: English in your left ear, {call} ({voice.hear_name}). "
                        f"--no-speak turns it off.")
        except VoiceOff as e:
            screen.info(f"{RESET}{YELLOW}! Voice off: {e}{RESET}")

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
        if voice is not None:
            voice.close()
        if stream is not None:
            stream.stop()
            stream.close()


if __name__ == "__main__":
    main()
