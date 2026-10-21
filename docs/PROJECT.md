# Call Captions: project notes

Everything about how Call Captions works, why it is built this way, how it was tested, and how
to release and roll back versions. For installing and using it, see the [README](../README.md).

## What it is

Live English captions of a Japanese phone call, shown on a Mac while the other person is still
talking. Speech recognition and translation run on the Mac itself. No audio or text leaves the
machine, and nothing is added to the call, so the other person hears a normal call.

Target hardware: Apple Silicon Macs (M1 or newer), macOS 14 or later. With Kotoba-Whisper (the
default until 1.4.0), an M4 Mac mini took about 0.8 s to recognize a sentence and 0.2 s to
translate it, after the speaker paused. Parakeet, the default since 1.4.0, processes only the
actual audio instead of a 30-second window, so it should be several times faster; `call-captions
bench` measures it (see "Speed" below).

## How it works

```
iPhone call ──► taken on the Mac (Continuity "Calls from iPhone")
                     │
                     ▼
           "Call Captions Output"  (multi-output device made by call-captions-audio)
               │                 │
               ▼                 ▼
      your headset         BlackHole (loopback driver)
      (you hear it)              │
                                 ▼
                    live_captions.py:  Silero VAD → Parakeet-ja (Neural Engine + GPU) → LFM2-ENJP (GPU)
                                 │
                                 ▼
                  terminal window: Japanese line + English line
                  (+ word lookups: lookup.py --serve, a CPU-only child process)
```

Your own voice goes from your headset mic straight into the call and never reaches BlackHole,
so only the other person is captioned.

With the voice on (the default since 1.5.0; `--no-speak` turns it off) the routing changes while
captions run:

```
call ──► Mac sound output = BlackHole ──► live_captions.py ──► English text
                     │                                            │
                     ▼                                            ▼  macOS `say` (built-in voice)
          call-captions-audio monitor  ◄──── English audio (stdin) ┘
          (private aggregate: headset + BlackHole)
                     │
                     ▼
     headset:  left ear = English voice,  right ear = the call
```

The English goes only to the headset, never into BlackHole or the call, so it is neither
captioned nor heard by the caller (as long as it's headphones: on speakers the voice turns itself
off).

### The models

| Step | Model | Size | License |
| --- | --- | --- | --- |
| Find speech | [Silero VAD](https://github.com/snakers4/silero-vad) | 2 MB | MIT |
| Japanese speech → text | [NVIDIA Parakeet TDT-CTC 0.6B ja](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja), [MLX build](https://huggingface.co/mlx-community/parakeet-tdt_ctc-0.6b-ja) | 0.6B | CC-BY-4.0 |
| Japanese → English | [LiquidAI LFM2-350M-ENJP-MT](https://huggingface.co/LiquidAI/LFM2-350M-ENJP-MT) | 0.35B | LFM Open License |
| Optional speech model | [Kotoba-Whisper v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0), [MLX build](https://huggingface.co/kaiinui/kotoba-whisper-v2.0-mlx) (`--asr kotoba`, default until 1.4.0) | 0.76B | Apache-2.0 |
| Optional speech model | [Qwen3-ASR 1.7B / 0.6B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B), [8-bit MLX builds](https://huggingface.co/moona3k/mlx-qwen3-asr-1.7b-8bit) via [mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) (`--asr qwen3`, `qwen3-small`) | 1.7B / 0.6B | Apache-2.0 |
| Speech → text on the Neural Engine (turbo, the default) | [Parakeet 0.6B ja, Core ML build](https://huggingface.co/FluidInference/parakeet-0.6b-ja-coreml) (FluidInference) | 0.6B | CC-BY-4.0 |
| Spoken English (`--speak`) | The voices built into macOS (`say`) | none | part of macOS |
| Word lookup: dictionary | [JMdict](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project) (EDRDG), split into words by [fugashi](https://github.com/polm/fugashi) + [unidic-lite](https://github.com/polm/unidic-lite) | 220k entries | CC BY-SA 4.0 / BSD |
| Word lookup: translation | [LFM2-350M-ENJP-MT GGUF](https://huggingface.co/LiquidAI/LFM2-350M-ENJP-MT-GGUF), 8-bit, CPU | 0.35B | LFM Open License |
| Word lookup: explanation | [LFM2.5-1.2B-Instruct GGUF](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF), 4-bit, CPU | 1.2B | LFM Open License |
| Optional translator | [PLaMo 2 Translate](https://huggingface.co/pfnet/plamo-2-translate) (`--mt plamo`) | 9.5B | PLaMo Community License |

The models are downloaded from Hugging Face during install and are covered by their own
licenses.

Why single-purpose models: they are much smaller than general chat models for the same job, so
they are faster and use less memory. In testing, LFM2-ENJP translated typical spoken business
Japanese better than a general 4B chat model (Qwen3-4B), and both CyberAgent CAT-Translate
models tried (1.4B, 3.3B) made meaning errors on phone-style speech.

### How the models compared

Typical interview-style lines, translated by LFM2-ENJP (10 of 11 right in the test set):

| Japanese | English |
| --- | --- |
| 日本語はどのくらい話せますか？ | How much do you speak Japanese? |
| では、来週の水曜日の午後2時に面接でいかがでしょうか。 | Then how about having an interview at 2 pm next Wednesday? |
| 履歴書を事前にメールで送っていただけますか。 | Please send me your resume in advance by email. |
| 土日も出勤できますか？ | Can I go to work on weekends too? ✗ (means "Can *you* work weekends?") |

Known weak spots: each sentence is translated on its own, so it sometimes picks the wrong person
where Japanese leaves the subject out; and names (people, companies) are often misspelled by the
speech model. `--hint` nudges Kotoba-Whisper and Qwen3-ASR (not Parakeet) toward expected names.

Rejected along the way:

- **Whisper large-v3 / large-v3-turbo**: accurate but general-purpose and heavier; replaced by the
  Japanese-only distilled Kotoba-Whisper.
- **Qwen3 4B (general chat model)** as translator: translated 来週 ("next week") as "two weeks
  from now".
- **CAT-Translate 1.4B / 3.3B**: garbled or changed meaning on spoken Japanese.
- **TurboQuant (KV-cache compression)**: no benefit here. Each sentence is short, so the KV cache
  is tiny, and LFM2 is mostly convolution layers.

### Speed: where the time goes

After the other person stops talking, a line appears after:

1. **The pause that ends a sentence**: `--end-silence`, 0.5 s. Shorter cuts sentences in two at
   the natural pauses of spoken Japanese, which hurts the translation, so it stays at 0.5 s.
2. **Speech recognition**: Kotoba-Whisper always processes a 30-second window, however short the
   sentence, so it costs about the same every time (about 0.8 s on an M4 Mac mini). Parakeet and
   Qwen3-ASR process only the actual audio.
3. **Translation**: about 0.1 to 0.3 s with LFM2-ENJP (0.35B).
4. **Voice**: making the audio with `say` takes about 0.1 to 0.2 s with Fred, the robot voice
   (a simple formant voice, the quickest to make; the default up to 1.7). Since 1.8 the default
   is the Mac's system voice, which can be a Siri voice: more natural, a little slower to make.
   The draft of a line is made ahead of time, so usually only the final line's audio is waited
   for. Since 1.9 the audio is played 1.5x as fast at the same pitch (`--voice-speed`, FFmpeg's
   `atempo` through PyAV, about 10 ms a line): it works with any voice, where `say -r` depends on
   the voice following it.

Since 1.3.0, steps 2 to 4 start at a 0.25 s pause (`--early`) instead of waiting for the full
0.5 s. If the pause turns out to end the sentence, the result is used as is: the audio is the
same, so the text is identical (checked on recorded speech). If they keep talking, it served as an
in-progress update. Also, in-progress updates no longer start as a pause begins, where one could
hold up the final text by a whole recognition. With a fast speech model, the English text and
voice can start about 0.5 s after the person stops.

Published Japanese benchmarks for the speech models (none measured phone audio on MLX, so
`call-captions bench` measures your own recordings on your Mac):

- [HEROZ, 2026](https://techblog.heroz.jp/entry/2026/08/18/120000), read speech (FLEURS /
  Common Voice / JSUT character error rate): Parakeet-ja 5.6 / 21.5 / 6.6 %, Kotoba-Whisper v2
  5.9 / 26.6 / 7.4 %, Qwen3-ASR 1.7B 5.3 / 26.3 / 8.4 %. Parakeet was the fastest of the open
  models.
- [Neosophie, 2026](https://neosophie.com/en/blog/20260226-japanese-asr-benchmark), natural
  conversation from TV and media: Qwen3-ASR 1.7B was the most accurate (14 % CER) and
  Kotoba-Whisper v2 the least (50 %; it dropped words after fillers such as えっと).
- [mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) on an M4 Pro: Qwen3-ASR 0.6B 8-bit
  0.10 s for a 2.5 s clip, 1.7B (16-bit) 0.36 s.

Parakeet is the default since 1.4.0: it is the fastest, and in the published read-speech tests
it is at least as accurate as Kotoba-Whisper. In our own test on a real voicemail, cut into
sentences the way live captions are, it got every sentence but spelled the two English company
names as it heard them (an acceptable cost for speed). It also showed one real flaw: when a
second sentence follows a short pause (0.15 to 0.4 s) in the same piece of audio, Parakeet-ja
stops after the first, with either of its decoders (TDT or CTC). It does hear the second sentence
on its own. So the captioner checks where Parakeet's last word ended and, if speech-length audio
is left, runs it again on the rest; with that, the voicemail came out complete. `bench` compares
the models on your own recordings.

The models are already the smallest that do the job well:

- **LFM2-350M-ENJP-MT** is the smallest dedicated Japanese↔English translation model, and runs at
  full precision. An 8-bit copy would save about 0.05 s per sentence but phrased a test greeting
  worse.
- **Parakeet** runs in 16-bit (bfloat16) on MLX; its 2.5 GB download is 32-bit.
- **Qwen3-ASR** uses 8-bit builds. 4-bit is about 20 % faster but roughly doubled the Japanese
  error rate in [one benchmark](https://soniqo.audio/benchmarks) (16.1 % vs 8.6 % on FLEURS).
- **Kotoba-Whisper** is already distilled (two decoder layers instead of 32). Its cost is the
  30-second window, which quantizing doesn't shrink.

Other things considered for speed:

- **A faster runtime than MLX:** on Apple Silicon, MLX on the GPU is the quickest way to run these
  models from Python. Core ML on the Neural Engine (WhisperKit, FluidAudio) mainly saves power;
  published numbers don't show it beating MLX for one live stream. The bigger gains are in the
  type of model (above) and in the pipeline (`--early`).
- **Neural voices (Kokoro and similar):** more natural, but they compete with the speech and
  translation models for the GPU and add a model to download. The built-in macOS voices cost
  nearly nothing and start quickly.
- **Direct speech-to-speech translation** (for example Meta SeamlessStreaming): one model, but
  lower translation quality than the two dedicated models here.

### The voice (on by default; `--no-speak` turns it off)

- `say -o` makes a WAV at the headset's sample rate for each English line; the captioner sends
  the samples to `call-captions-audio monitor` over a pipe.
- The monitor builds a **private** aggregate device (visible only to itself, gone when it exits):
  headset as clock, BlackHole with drift correction. Its audio callback puts the call (BlackHole)
  in the right channel and the speech in the left. The callback only copies samples, using a
  lock-free ring buffer, so it can't stall.
- `split` (default): the captioner switches the Mac's output to BlackHole once the monitor is
  running, and back to Call Captions Output when it stops. If the monitor ever exits, the call
  goes straight back to both ears. `mix`: the output stays Call Captions Output (call in both
  ears) and the monitor plays only the speech, in the left ear.
- While a line plays, the next line waits. If an even newer one comes in, the waiting one is
  skipped, so the voice never falls more than a line behind. The translation made at the early
  pause is turned into speech right away, so it is often ready before the line is final.
- The voice turns itself off on built-in speakers, displays and AirPlay. Through speakers, the
  microphone could pass the English into the call. Whenever the voice can't run, the window says
  `! Voice off:` with the reason and the captions carry on.
- `call-captions test-voice` says a line in the left ear through the monitor, then, with the
  Mac's output on BlackHole, a line through the default output, which reaches the right ear the
  way a call does. That checks both halves of the routing before a real call.

### Turbo: the Neural Engine for hearing (1.6; the default since 1.8)

The Mac has three separate engines: CPU, GPU and Neural Engine. Turbo (`--no-turbo` turns it
off):

| Job | Chip | How |
| --- | --- | --- |
| Hearing: encoder (nearly all the work) | Neural Engine | Parakeet's Core ML build via coremltools, `CPU_AND_NE` |
| Hearing: decoder | GPU | Parakeet's own TDT decoder on MLX, a few ms |
| Translating | GPU | LFM2-ENJP on MLX |
| Word lookup | CPU | a separate program, llama.cpp with `--device none` |
| Voice, voice activity | CPU | as before |

- **Measured on an M4 Mac mini** (the owner's voicemail, 3 sentences): Parakeet on the GPU took
  0.12 to 0.24 s per sentence, the Neural Engine build 0.03 to 0.07 s. The live race showed control
  lines 0.54 to 0.75 s after the end of speech.
- **One thread only.** 1.6.0 ran translation in a second thread ("lanes") while hearing went on;
  on the Mac that crashed with a segmentation fault as soon as turbo started: MLX must not be used
  from two threads. 1.6.1 removed the lanes. With hearing down to a few hundredths of a second,
  they would have saved almost nothing anyway.
- **Decoder:** the Core ML build's own CTC head was worse on the Mac (a company name in Latin
  letters came out half in katakana, a comma was lost and the translation then invented a
  sentence). Since 1.6.1 the
  Core ML encoder's output goes to Parakeet's TDT decoder on MLX instead, the same one the GPU
  version uses; on the voicemail that path gives exactly the GPU version's text. The CTC head
  stays as a fallback if the MLX model can't load.
- **Sooner at a pause (`--quick-end`, the default since 1.7):** the early transcription starts at
  a 0.15 s pause (0.25 s otherwise). If Parakeet's text then ends a sentence (。？！), the line is
  finished there instead of waiting for the 0.5 s pause. On the owner's M4 Mac mini (bench, real
  voicemail played in real time): control 0.62 s average / 0.77 s slowest from the end of speech
  to the English on screen; quick 0.40 s / 0.44 s; quick with a word lookup every second 0.42 s /
  0.48 s. Quick split the three utterances into five sentence lines, with the same words, and
  translated them better (お世話になります got its own line, "I'm sorry to bother you.", instead
  of control's run-on line that gave the caller's name twice).
- **Core ML in a helper process (1.7):** 1.6.0 to 1.6.2 crashed with a segmentation fault as the
  turbo race started. The 1.6.2 report showed every Python thread idle (the captioner waiting for
  audio), so the crash was in one of Core ML's own background threads; the same model worked in
  the bench's model test, where the live pipeline (voice-activity model, audio thread) wasn't
  running. Since 1.7 Core ML runs only in a helper process that exchanges audio and text over a
  pipe; the captioner never imports coremltools, keeps Parakeet on the GPU loaded as a backup and
  switches to it on the spot if the helper stops (its crash report goes to a log file).
- **The cause (1.7.1):** with Core ML alone in the helper, the 1.7.0 helper still crashed while
  idle, waiting for audio. coremltools' native bridge wraps each numpy input in a `MLMultiArray`
  that holds a reference to it; Core ML can release that wrapper on one of its private queues, and
  in coremltools 9.0 the release decrements the numpy array's reference count without the GIL,
  freeing it off-thread when it was the last reference. Upstream fixed it on 2026-09-30
  (a207b1a, "Fix NumPy owner release from Core ML queues"), not in a release yet. Workaround: the
  helper keeps every input dict for its next 64 predictions, so the late release never drops the
  last reference.
- **The default since 1.8:** with that workaround the owner's 1.7.1 bench ran turbo with no
  crash. Live race on an M4 Mac mini: turbo 0.37 s average / 0.39 s slowest, quick (GPU) 0.40 s /
  0.44 s, control 0.62 s / 0.77 s; turbo with a lookup every second 0.37 s / 0.41 s. The Core ML
  encoder is 8-bit (the only build there is), so now and then a word comes out differently from
  the GPU version (a name spelled another way, a comma lost), and once that changed the
  translation's wording. The owner chose speed; `--no-turbo` keeps the GPU's text.
- **The Neural Engine model:** a fixed 15 s window (shorter audio is padded). Its vocabulary is
  identical to the MLX build's. If it can't load, the captioner falls back to the GPU Parakeet.
- **Parakeet stopping after one sentence**, now handled for both decoders: on a real voicemail,
  CTC placed 「。」 at the very end of the audio and dropped the sentence before it, so "where it
  stopped" means the last word, not punctuation. CTC also spots words early, so instead of
  re-reading whatever follows the last word (which produced junk like 「あした?」 from the word's
  own tail), it re-reads only after a real pause (≥ 0.13 s, 20 dB below the speech) followed by at
  least 0.3 s of speech. Checked on the voicemail with both decoders: every sentence complete, no
  junk.

`call-captions bench AUDIO` measures all of it on the Mac itself: the speech models on the same
sentences; a live race where the recording plays in real time through control, quick (control plus
`--quick-end`) and turbo, timing each line from the end of speech to the English on screen; the
word lookup; and the fastest set-up again while a lookup runs every second. Since 1.6.2 each race
runs in a child process set up like a real session (`--race MODE`), with Python's faulthandler on:
1.6.0 and 1.6.1 both crashed with a bare segmentation fault as the turbo race started, which said
nothing about where. Now a crash ends only that race and prints the Python stack.

### Word lookup (1.6.0; in the captions window since 1.8.0)

The lookup watches for Japanese being copied (iTerm2 copies whatever is highlighted; ⌘C works
anywhere). Up to 1.7 it only ran in a pane of its own that the app opened, so starting
`call-captions` in a terminal had no lookup at all. Since 1.8 the captioner starts it as a child
process (`lookup.py --serve`, its own session, CPU only), reads its results as JSON lines and
shows them among the captions behind a magenta bar. The voice says the dictionary's first meaning
when one word was highlighted, otherwise the translation. A lookup is said after the line playing
now and is never skipped (live lines skip when a newer one is waiting). A newer highlight cuts the
explanation of the one before short. If the captioner ends, however it ends, the child sees it
(its parent changes) and stops its models. `call-captions lookup` still runs it on its own.
For each highlight:

1. **Dictionary, instant (~1 ms):** fugashi/UniDic splits the text into words with their
   dictionary forms; the longest matching JMdict entry wins, so set phrases (恐れ入りますが,
   よろしくお願いします, させていただく) come up whole. Particles and endings never start a
   lookup, helper verbs (いる, する …) aren't shown on their own, a phrase ending in grammar must be
   a JMdict "expression", and UniDic's kanji lemma picks the right entry (為る "do", not 擦る
   "rub"). Entries are ranked by JMdict's frequency marks. Humble, honorific and polite senses
   are labelled.
2. **Translation:** LFM2-350M-ENJP-MT's CPU (GGUF) build through `llama-server`.
3. **Explanation:** LFM2.5-1.2B-Instruct, given the dictionary lines, shown word by word as it's
   written. Compared on interview phrases: LFM2-1.2B answered in Japanese as if it were the
   interviewer; Qwen3-1.7B made up a reading for 恐れ入りますが; LFM2.5-1.2B-JP was right but wordy.
   Without the dictionary lines all of them got some phrases wrong; with them, LFM2.5-1.2B-Instruct
   got all eight right, even who is asking whom (「土日も出勤できますか」 → "asking if you can work
   on the weekend").

Both models run in `llama-server` processes with `--device none -ngl 0` (no GPU at all), 2 and 4
threads, so the captions keep CPU room for voice detection and the voice. Translation and
explanation start together.

### Program layout

| Path | What it is |
| --- | --- |
| `live_captions.py` | The captioner: audio input, VAD, speech recognition, translation, terminal display, bench |
| `lookup.py` | The word lookup: dictionary build and lookups, the CPU models, watching highlights (`--serve` for the captioner) |
| `bin/call-captions` | The command users run: setup, first run, sound routing, app, uninstall |
| `src/audio.c` | `call-captions-audio`, a small CoreAudio helper built during `brew install`: sets up and switches the routing, and plays the voice and the call to each ear (`monitor`) |
| `app/` | Template for `Call Captions.app` (Info.plist, launcher script, icon) |
| `requirements.txt` | Python packages |

In [jinreiyu/homebrew-tap](https://github.com/jinreiyu/homebrew-tap):
`Formula/call-captions.rb` (latest) and `Formula/call-captions-1.2.0.rb` (milestone).

## Install design (Homebrew)

`brew install jinreiyu/tap/call-captions` does all of the heavy setup:

1. **install**: compiles `src/audio.c` into `call-captions-audio` and copies the program into
   Homebrew's folder for the formula (`libexec`).
2. **post-install** (`post_install_steps` → `call-captions install-env`): creates Python 3.12
   with `uv`, installs the packages and downloads the models, all inside the formula's folder.
   On upgrade or reinstall it moves the models over from the previous version's folder instead
   of downloading several GB again.

Homebrew's rules shaped this design (checked in Homebrew's source):

- The post-install step runs in a sandbox: it may use the network, but it can only write inside
  Homebrew's folders, and it gets a temporary home folder. So everything that has to live
  elsewhere happens on the first run instead.
- A formula can't depend on a cask, and installing BlackHole needs the user's password, so
  BlackHole is never installed automatically.
- Homebrew deprecated `post_install` in favour of declarative `post_install_steps`; the formula
  uses `post_install_steps` when available and falls back to `post_install` on older Homebrew.
- Third-party taps need trust. Naming the formula in full (`jinreiyu/tap/call-captions`) counts
  as trusting it; uninstalling removes that trust.
- Homebrew 5 removed the option to skip Gatekeeper's quarantine for casks, so the app is not
  shipped as a cask. The app is built locally on first run, which macOS does not quarantine.

Because Python, the packages and the models live in the formula's folder, `brew uninstall`
removes all of them.

## First run (`call-captions` / `call-captions setup`)

Does what Homebrew isn't allowed to:

- Creates `~/call-captions/options.txt` (your standing options), using the BlackHole already on
  the Mac for `--device`. `call-captions` passes its path to `live_captions.py`
  (`CALL_CAPTIONS_OPTIONS`), which puts its lines before the typed options, so typed ones win.
- Builds `Call Captions.app` in /Applications (or ~/Applications). The app is a small launcher
  that opens iTerm2 or Terminal running `call-captions` with the options, so it uses the
  terminal's microphone permission. Opening it again while it runs brings the window forward.
- **BlackHole**: if any BlackHole (2ch, 16ch, 64ch) exists, uses it. Only if none exists does it
  ask whether to install BlackHole 2ch. It never installs a second one and never removes one.
- **Sound routing**: asks where you want to hear calls (current output suggested), then
  `call-captions-audio ensure-multi` creates **Call Captions Output**, a stacked aggregate
  (multi-output) device: your device keeps the clock, BlackHole follows with drift correction.
  The choice is saved in `~/call-captions/audio.conf`. Runs again cheaply each start and only
  rebuilds when the device or BlackHole changed.
- Warns if the Mac's input is BlackHole (the caller wouldn't hear you).
- Offers to delete leftovers of the earlier script-based install, keeping `options.txt`.

While captions run, the Mac's sound output is switched to Call Captions Output, and switched back
on exit, including Ctrl+C and closing the window. Testing a file (`--file`) doesn't switch.

## Uninstall design

`call-captions uninstall` (asks first): removes the app (only if it has this tool's bundle id),
its Automation permission, the Call Captions Output device, `~/call-captions`, and then runs
`brew uninstall` for the formula, which removes the program, Python and models. Homebrew also
autoremoves dependencies it installed only for this (such as `uv`). It never touches BlackHole
or audio devices the user created.

## Versions and rollback

- Versions follow `MAJOR.MINOR.PATCH`. The version is in `bin/call-captions` (`VERSION`) and in
  the formula.
- The formula pins an exact commit tarball plus its sha256, so a version always installs the
  same code.
- A **milestone** is a version confirmed to work on a real Mac. Each milestone gets:
  - a `milestone/vX.Y.Z` branch in this repo and in the tap, at the exact commits;
  - a formula `call-captions-X.Y.Z` that installs that code.
- Milestone formulas use `-X.Y.Z`, not `@X.Y.Z`. With an `@` name Homebrew also loads the base
  formula, and it refuses to once the base is uninstalled, since uninstalling drops the base
  formula's tap trust. That would break the rollback command.

Rollback commands are in the [tap README](https://github.com/jinreiyu/homebrew-tap#milestones-and-rollback).
`brew pin call-captions` keeps the installed version until you unpin it.

Release checklist:

1. Change code; bump `VERSION` in `bin/call-captions`.
2. Push to `main`; download the commit tarball and compute its sha256.
3. Update `Formula/call-captions.rb` (url, version, sha256) in the tap; push.
4. Once confirmed working on a Mac: push `milestone/vX.Y.Z` branches in both repos, add
   `Formula/call-captions-X.Y.Z.rb`, and list it in the tap README and the CHANGELOG.

## How it was tested

Development happened in a Linux container without a Mac, so testing was split:

- **Models**: transcription and translation compared on real Japanese audio and typical
  interview lines, on CPU (table above).
- **Captioner pipeline**: utterance splitting, live updates and display, using recorded audio fed
  at real-time speed.
- **Homebrew**: real Homebrew on Linux with a test copy of each formula (tiny stand-in models, no
  macOS-only lines): install with post-install setup, reinstall reusing models, first run,
  uninstall, rollback to the milestone and back.
- **CoreAudio helper**: compiled and linked against Apple's macOS 14.5 SDK for arm64 with
  `-Wall -Wextra`, no warnings; all symbols resolve. Its logic in `call-captions` was tested
  against a stand-in that simulates the Mac's devices, including Ctrl+C restoring the output.
- **On a real Mac**: version 1.2.0 confirmed working end to end, which made it milestone 1.2.0.
- **1.3.0** (not yet confirmed on a Mac):
  - The `monitor` helper was compiled against the macOS 14.5 SDK with no warnings. Its mixing and
    queueing code was run on Linux with a stand-in for CoreAudio, checking left = speech in
    order, right = the call, nothing written to BlackHole, dropping speech, and over 4 million
    samples through the ring buffer.
  - The voice logic was tested with stand-ins for `say` and the helper: switching the output and
    switching it back, recovery when the helper dies, refusing speakers, skipping a stale line,
    reusing a line made in advance, and falling back from a missing voice.
  - `--early` was run on recorded speech: identical lines with and without it, all of them ready
    at the pause.
- **1.6.0** (not yet confirmed on a Mac):
  - Neural Engine Parakeet: inputs and outputs taken from the Core ML model files; the decoding
    (CTC, vocabulary, picking up after a dropped sentence) run on the real voicemail using the same
    model's CTC head on Linux: all sentences complete. The Core ML calls themselves need a Mac.
  - The sentence-end shortcut: stand-in models with realistic timings on the real recording,
    played in real time: same lines in the same order, 0.38 s vs 0.60 s average.
  - On the owner's M4 Mac mini (bench): Core ML Parakeet worked, 3.5x faster than on the GPU; the
    turbo race crashed (the lanes, removed in 1.6.1).
- **1.6.1:** the hybrid decoder (Core ML encoder output → MLX TDT decoder) checked on the
  voicemail with the same model's encoder standing in for Core ML: identical text to the GPU
  version on all three sentences.
  - Word lookup: run for real on Linux (dictionary, llama.cpp CPU build, both models) on interview
    phrases. On that 4-core cloud machine the translation took about 1–1.5 s and the explanation
    about 2–2.5 s; the M4's CPU should be several times faster. `bench` measures it.
  - The whole `bench` ran end to end on Linux with stand-ins for the Apple-only models.
  - Homebrew: the upgrade to 1.6.0 built the dictionary inside the post-install sandbox and the
    next upgrade carried it over. This test caught that the formula didn't install `lookup.py`.

## Known limits and troubleshooting

- Start captions **before** the call so the call uses Call Captions Output.
- The volume keys don't change a multi-output device: set the headset volume before the call.
- Bluetooth headsets (AirPods) switch to a lower-quality mode when their mic is in use, which can
  disturb a multi-output device; a USB headset is the most reliable.
- Apps that boost one app's volume can mute that app when the output is a multi-output device.
- If the terminal app lacks Microphone permission, macOS gives it silence instead of an error.

## Could it run on iPhone?

Researched in 2026 for iPhone 17:

- **Hardware: yes.** Apple's A19 chips run these models fast enough; public benchmarks of
  similar speech models put the iPhone 17 Pro near an M4 Mac.
- **Call audio: no.** iOS gives apps no access to the audio of a normal cellular or FaceTime
  call on the same iPhone (confirmed by Apple developer support in 2026). Apple's own call
  recording and Live Translation announce themselves to the other person.
- **Ways around it:** show the Mac's captions on the iPhone; or an app that carries the call
  itself over VoIP (forwarding calls to an internet number), at the cost of the audio passing
  through the VoIP provider.

## Credits

Built with [MLX](https://github.com/ml-explore/mlx), [mlx-whisper](https://github.com/ml-explore/mlx-examples),
[mlx-lm](https://github.com/ml-explore/mlx-lm), [parakeet-mlx](https://github.com/senstella/parakeet-mlx),
[Silero VAD](https://github.com/snakers4/silero-vad), [BlackHole](https://github.com/ExistentialAudio/BlackHole),
[uv](https://github.com/astral-sh/uv), [coremltools](https://github.com/apple/coremltools),
[llama.cpp](https://github.com/ggml-org/llama.cpp), [fugashi](https://github.com/polm/fugashi),
[unidic-lite](https://github.com/polm/unidic-lite) and [Homebrew](https://brew.sh).

The word lookup uses the [JMdict](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project)
dictionary files. These files are the property of the
[Electronic Dictionary Research and Development Group](https://www.edrdg.org/), and are used in
conformance with the Group's [licence](https://www.edrdg.org/edrdg/licence.html).
