# Call Captions architecture: one job per chip

Call Captions turns a Japanese phone call into English text and an English voice in your ear,
live, on the Mac itself. On an M4 Mac mini the English shows up **0.37 s on average** after the
caller stops talking (0.39 s at worst). This page explains how: which chip does which job,
which jobs run at the same time, and which steps are kept in a strict order because they have to
be.

## The short version

An Apple Silicon Mac has three kinds of compute engine on one chip: the **CPU**, the **GPU** and
the **Neural Engine**. Call Captions gives each job to the engine that does it best and keeps the
side jobs away from the engines the live captions use:

| Job | Engine | Runs in | How long, per sentence |
| --- | --- | --- | --- |
| Getting the call's audio | CoreAudio real-time thread | the captioner | continuous, 32 ms blocks |
| Is someone talking? (voice activity) | CPU, 1 thread | the captioner | under 1 ms per 32 ms block |
| Hearing: Parakeet's encoder (most of the work) | **Neural Engine** | the Neural Engine helper | 0.07 to 0.13 s |
| Hearing: Parakeet's decoder | GPU | the Neural Engine helper | a few ms |
| Translating Japanese → English | **GPU** | the captioner | 0.1 to 0.3 s |
| Making the voice | CPU | `say`, one process per line | 0.1 to 0.2 s with the robot voice; Siri takes longer (not measured yet) |
| Speeding the voice up 2x, same pitch | CPU | the captioner's voice thread | about 10 ms |
| The call in one ear, the voice in the other | CoreAudio real-time thread | `call-captions-audio monitor` | continuous |
| Word lookup (dictionary, translation, explanation) | **CPU only** | the lookup + 2 llama.cpp servers | 0.02 / 0.2 / 1 s |

## The picture

```
  iPhone call (taken on the Mac) ──► Mac sound output = BlackHole (a virtual audio cable)
                                              │
                ┌─────────────────────────────┴──────────────────────────────┐
                ▼                                                            ▼
  CAPTIONER  (live_captions.py)                            MONITOR  (call-captions-audio)
  CoreAudio input thread                                   CoreAudio real-time thread
     │ 32 ms blocks                                          right ear ◄── the call
     ▼                                                       left ear  ◄── the voice
  main thread, the live chain:                                         │
     Silero VAD ..................... CPU                              ▼
     │ a 0.15 s pause                                            your headset
     ▼
     audio ──pipe──►  NEURAL ENGINE HELPER  (a process of its own)
                        Parakeet encoder ....... Neural Engine (Core ML)
                        Parakeet decoder ....... GPU (MLX)
     text  ◄──pipe───
     │
     ▼
     LFM2-ENJP translation .......... GPU (MLX)
     │
     ▼
     terminal: Japanese + English
     │
  voice thread:
     say ............................ CPU
     2x speed-up .................... CPU  ──► monitor, left ear

  lookup reader thread ◄── JSON lines ──  WORD LOOKUP  (lookup.py --serve, a process of its own)
                                            JMdict dictionary (SQLite) ...... CPU
                                            llama-server, translation ....... CPU, 2 threads
                                            llama-server, explanation ....... CPU, 4 threads
                                            (--device none: no GPU, no Neural Engine)
```

While captions run there are six programs: the captioner, the Neural Engine helper, the monitor,
the word lookup and its two llama.cpp servers, plus a short-lived `say` for each line.

## Following one sentence

The caller says 「面接の日程を確認させてください。」 and stops. From that moment:

| Time after they stop | What happens | Where |
| --- | --- | --- |
| 0 to 0.15 s | Voice activity sees silence. After 0.15 s of it, the final transcription starts, before anyone knows whether the sentence is over (`--early`). | CPU |
| about 0.15 to 0.25 s | The audio goes down a pipe to the helper. Parakeet's encoder runs on the Neural Engine, its decoder on the GPU, and the Japanese comes back. | Neural Engine, then GPU |
| about 0.25 s | The text ends in 。, so the line is finished right away instead of waiting for the full half-second pause (quick end). | CPU |
| about 0.25 to 0.37 s | LFM2-ENJP translates the sentence. | GPU |
| about 0.37 s | Japanese and English on screen. | terminal |
| right after | The voice thread makes the audio with `say`, speeds it up 2x and hands it to the monitor, which plays it in your left ear. Meanwhile the main thread is already listening for the next sentence. | CPU, CoreAudio |

If the caller keeps talking after the 0.15 s pause, that early result was simply a preview, and
the line is finished later from the full audio. If the pause does end the sentence, the early
result is used as is: same audio, same text, just sooner.

## What runs at the same time, and what doesn't

**At the same time, on different engines:**

- **Capture and playback never stop.** CoreAudio's own real-time threads read the call and mix
  the headset in their own time, whatever the captioner is doing. If the main thread is busy
  translating, the next 32 ms blocks wait in a queue and are caught up in a few milliseconds.
- **The voice overlaps the next sentence.** While line 1 is being said in your ear (CPU,
  CoreAudio), line 2 is already being heard (Neural Engine) and translated (GPU).
- **The voice gets a head start.** While the caller is still talking, drafts of the line are
  transcribed and translated, and the voice thread makes their audio ahead of time. If the final
  line matches a draft, its audio is ready.
- **The word lookup is fully separate.** Its process and its two llama.cpp servers use CPU cores
  only. On the Mac, a lookup every second during a live run left the captions at 0.37 s average
  (0.41 s at worst), the same as without lookups.
- **The Neural Engine work is in a separate process** with its own memory, so a fault there can't
  take the captions down (see below).

**Strictly one after another, on purpose:**

- **One sentence's steps form a chain.** You can't translate what hasn't been heard yet, or say
  what hasn't been translated. So hearing, translating and showing a line happen in order, each on
  its fastest engine. The speed comes from that choice of engine, from starting at a 0.15 s pause
  and from finishing at the end of a sentence, not from running these steps side by side.
- **The GPU work stays on one thread.** Apple's MLX framework must not be used from two threads
  at once. Version 1.6.0 tried translating in a second thread ("lanes") while hearing went on,
  and it crashed on a real Mac. It was removed in 1.6.1, and since hearing now takes under a tenth
  of a second, it would have saved almost nothing anyway.
- **Drafts give way to the final text.** In-progress transcriptions run about once a second while
  someone talks, but never once a pause has started, so the final transcription never waits
  behind a draft.

## Why each engine got its job

**Neural Engine: hearing.** Parakeet's encoder (a 0.6B Conformer) is nearly all of speech
recognition's work, and it's large, regular matrix math, which is what the Neural Engine is
built for. On the M4 it took 0.07 to 0.13 s per sentence against 0.13 to 0.25 s on the GPU. The
Core ML build is 8-bit (the only one there is), so now and then a word comes out a little
differently from the GPU version; `--no-turbo` keeps hearing on the GPU.

**GPU: the decoder and the translation.** Parakeet's own TDT decoder runs on the GPU through MLX
right after the encoder, because it's the same decoder the GPU version uses, which keeps the
text the same (the Core ML build's own decoder made more mistakes). Translation is LFM2-350M-ENJP,
a small model built only for Japanese↔English, on MLX.

**CPU: everything small or on the side.** Voice activity (Silero VAD, one thread, a 32 ms block
at a time), making the voice (`say`), the 2x speed-up (FFmpeg's `atempo` through PyAV, which
keeps the pitch and works with any voice, Siri included), and the whole word lookup. The lookup's
models run in llama.cpp with `--device none -ngl 0` and 2 and 4 threads, so they can never take
GPU or Neural Engine time from the captions, and they leave CPU room for the voice.

**CoreAudio threads: the sound.** The monitor (`src/audio.c`) is a small C program. It builds a
private aggregate device from your headset and BlackHole and, on CoreAudio's real-time thread,
copies the call into your right ear and the voice from a ring buffer into your left.

## Keeping it up when something fails

- **The Neural Engine helper can crash without stopping the captions.** Core ML used to crash the
  whole program, from one of Core ML's own background threads. The cause turned out to be a bug in
  coremltools 9.0: it can free a numpy input on a Core ML queue without Python's lock. It's fixed
  upstream but not yet released. The helper works around it by keeping each input alive for its
  next 64 predictions. And if the helper ever stops anyway, the captioner switches on the spot to
  Parakeet on the GPU, which it loads at the start for exactly that.
- **The lookup cleans up after itself.** It watches its parent; however the captioner ends, the
  lookup stops and its llama.cpp servers stop with it (each server also has a shell watchdog).
- **The voice never reaches the call.** It plays only to your headset through the monitor, never
  into BlackHole or "Call Captions Output", and it switches itself off on speakers, where your
  microphone could pick it up.
- **Nothing leaves the Mac.** Every model runs locally; no audio or text is sent anywhere.

## Measured on an M4 Mac mini

`call-captions bench` plays a real 20-second Japanese voicemail in real time through the whole
pipeline, each set-up in a process of its own, and times each line from the end of speech to the
English on screen:

| Set-up | Average | Slowest |
| --- | --- | --- |
| As up to 1.6: GPU, line after the full 0.5 s pause | 0.62 s | 0.77 s |
| GPU, line at the end of a sentence (`--no-turbo`) | 0.40 s | 0.44 s |
| **Default: Neural Engine + GPU, line at the end of a sentence** | **0.37 s** | **0.39 s** |
| Default, with a word lookup every second on the CPU | 0.37 s | 0.41 s |

Word lookup on the CPU, from highlight to answer: dictionary 0.02 s, translation 0.2 s,
explanation about 1 s.

To measure it on your own Mac and recording:

```bash
call-captions bench ~/Downloads/voicemail.m4a
```

## Where it lives in the code

| Part | File |
| --- | --- |
| Captioner: capture, VAD, the live chain, display, voice thread, bench | `live_captions.py` |
| Neural Engine helper | `live_captions.py` (`NeuralEngineHelper`, `ParakeetANE`, `--neural-engine-helper`) |
| Word lookup | `lookup.py` (`--serve` when the captioner runs it) |
| Sound routing and the two-ear monitor | `src/audio.c` (`call-captions-audio`) |
| Setup, options, app, uninstall | `bin/call-captions` |

More background, the models compared and the release process: [PROJECT.md](PROJECT.md).
