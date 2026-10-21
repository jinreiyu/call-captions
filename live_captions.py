#!/usr/bin/env python3
"""Live Japanese -> English captions for a phone call, running entirely on the Mac.

Pipeline:  call audio (BlackHole loopback, a mic, or a file)
           -> Silero VAD (cuts speech into utterances)
           -> Japanese-only speech recognizer on MLX (Kotoba-Whisper or NVIDIA Parakeet-ja)
           -> Japanese->English-only translation model on MLX (LiquidAI LFM2-350M-ENJP-MT)
           -> terminal

Nothing is sent over the network; models are downloaded from Hugging Face once.
"""

import argparse
import queue
import re
import shutil
import sys
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

    def __call__(self, audio, final=True):
        mel = self._get_logmel(self._mx.array(audio), self.model.preprocessor_config)
        return self.model.generate(mel)[0].text.strip()


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

    def final_en(self, ja, en, asr_s, mt_s):
        with self.lock:
            self._clear_partial()
            timing = f"  {DIM}(asr {asr_s:.2f}s · mt {mt_s:.2f}s){RESET}" if self.show_timing else ""
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
    def __init__(self, args, asr, mt, screen):
        import torch
        from silero_vad import load_silero_vad

        self.args, self.asr, self.mt, self.screen = args, asr, mt, screen
        self._torch = torch
        torch.set_num_threads(1)  # VAD is tiny; extra threads only add overhead
        self.vad = load_silero_vad()
        self.end_frames = int(args.end_silence / FRAME_S)
        self.pause_frames = int(0.15 / FRAME_S)
        self.soft_max = int(args.soft_max / FRAME_S)
        self.hard_max = int(args.hard_max / FRAME_S)
        self.min_frames = int(0.25 / FRAME_S)
        self.preroll = deque(maxlen=int(0.3 / FRAME_S))
        self.reset()

    def reset(self):
        self.utt, self.in_speech, self.silence, self.voiced = [], False, 0, 0
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
        if p >= thr:
            self.silence = 0
            self.voiced += 1
        elif p < thr - 0.15:
            self.silence += 1
        n = len(self.utt)
        if (
            self.silence >= self.end_frames
            or (n >= self.soft_max and self.silence >= self.pause_frames)
            or n >= self.hard_max
        ):
            self.finish()

    def finish(self):
        frames = self.utt[: len(self.utt) - max(0, self.silence - 3)]  # drop trailing silence
        voiced = self.voiced
        self.reset()
        if voiced < self.min_frames:
            self.screen.clear()
            return
        audio = np.concatenate(frames)
        t0 = time.monotonic()
        ja = self.asr(audio, final=True)
        t1 = time.monotonic()
        if looks_bogus(ja, self.args.hint):
            self.screen.clear()
            return
        self.screen.final_ja(ja)
        if self.mt is None:
            self.screen.final_en(ja, "", t1 - t0, 0.0)
            return
        en = self.mt(ja)
        self.screen.final_en(ja, en, t1 - t0, time.monotonic() - t1)

    def maybe_partial(self):
        now = time.monotonic()
        if not self.in_speech or self.voiced < int(1.0 / FRAME_S):
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


def main():
    ap = argparse.ArgumentParser(prog="call-captions", description="Live Japanese -> English call captions, fully on-device.")
    ap.add_argument("--device", default="BlackHole", help="input device name (substring) or index [BlackHole]")
    ap.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    ap.add_argument("--file", help="caption an audio file instead of a live device (for testing)")
    ap.add_argument("--fast", action="store_true", help="with --file: process as fast as possible, not in real time")
    ap.add_argument("--asr", default="kotoba", help="kotoba | parakeet | cpu[:model] | <hf repo> [kotoba]")
    ap.add_argument("--mt", default="lfm2", help="lfm2 | plamo | none | <mlx-lm hf repo> [lfm2]")
    ap.add_argument("--hint", default="",
                    help="names to expect, e.g. '株式会社サンプル 山田' (Kotoba only; biases spelling)")
    ap.add_argument("--english-only", action="store_true", help="hide the Japanese lines")
    ap.add_argument("--no-timing", action="store_true", help="hide per-line timing")
    ap.add_argument("--no-partials", action="store_true", help="don't show text while the person is still talking")
    ap.add_argument("--partial-every", type=float, default=1.0, help="seconds between in-progress updates [1.0]")
    ap.add_argument("--draft-after", type=float, default=4.0,
                    help="translate in-progress text once an utterance is this long; 0 disables [4.0]")
    ap.add_argument("--end-silence", type=float, default=0.5, help="pause (s) that ends an utterance [0.5]")
    ap.add_argument("--soft-max", type=float, default=8.0, help="after this many seconds, cut at any short pause [8]")
    ap.add_argument("--hard-max", type=float, default=15.0, help="always cut an utterance at this length [15]")
    ap.add_argument("--vad-threshold", type=float, default=0.5, help="speech probability threshold [0.5]")
    ap.add_argument("--log", help="append a transcript (time, Japanese, English) to this file")
    ap.add_argument("--prefetch", action="store_true", help="download and warm up the models, then exit")
    ap.add_argument("--download-only", action="store_true", help="download the models without loading them, then exit")
    args = ap.parse_args()

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

    captioner = Captioner(args, asr, mt, screen)
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
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear()
        if stream is not None:
            stream.stop()
            stream.close()


if __name__ == "__main__":
    main()
