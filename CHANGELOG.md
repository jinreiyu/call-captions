# Changelog

Milestones are versions confirmed working on a real Mac. Each is kept installable for rollback
as `brew install jinreiyu/tap/call-captions-X.Y.Z` and on the `milestone/vX.Y.Z` branch.

## 1.10.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- Word lookups are shown but no longer said: on a call, the spoken meaning came between the live
  lines. `--lookup-voice` brings it back.
- The voice talks at **2x** by default (`--voice-speed 2`); `1.5` was the default in 1.9.

## 1.9.1

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- `options.txt` now works when you start `call-captions` in a terminal (and for `test-voice`),
  as the help said: before, only the app used it (apart from `--device`). An option you type
  wins over it. The app now leaves reading it to `call-captions`.

## 1.9.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- The voice talks at **1.5x** by default, at the same pitch, whatever the voice (Siri included):
  `--voice-speed 2` for twice as fast, `1` for normal, up to 3. It speeds up the voice's audio
  itself, so it doesn't depend on the voice following `say -r`.
- `--rate` now defaults to the Mac's own speaking rate (Read & Speak) instead of 240 words a
  minute; `--voice-speed` applies on top.
- `call-captions help` lists the speed settings, which are on by default and what's left to
  make it faster.

## 1.8.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- **Turbo is the default:** speech recognition's encoder runs on the Neural Engine (0.37 s from
  the end of speech to the English on screen on an M4, against 0.40 s on the GPU). It no longer
  crashes since 1.7.1, and the GPU still takes over at once if it ever stops. `--no-turbo` keeps
  everything on the GPU (its text can differ by a word now and then). `--no-quick-end` now works
  with turbo too.
- **The Mac's system voice is the default:** choose a Siri voice as the system voice (System
  Settings → Accessibility → Read & Speak) and the English is said in Siri's voice. macOS only
  lets apps use a Siri voice that way. The window and `test-voice` say which voice is used.
  `--voice Fred` brings back the robot. If a voice makes no sound or is missing, it falls back to
  the system voice, then Samantha, and says so.
- **The word lookup works however you start Call Captions**, in the captions window itself: up
  to 1.7 it ran only in a pane the app opened, so starting `call-captions` in a terminal had no
  lookup. The meaning is now also said in your left ear (the dictionary's for one word, else the
  translation), right after the line being said, never skipped. `--no-lookup-voice` keeps it
  silent; `--no-lookup` turns it off. A new highlight cuts the previous explanation short. The app
  no longer opens a separate pane.
- `call-captions help` explains every feature and option.
- The docs no longer quote words from a real voicemail.

## 1.7.1

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- Works around the bug behind every `--turbo` crash so far, found in coremltools' own source:
  coremltools 9.0 lets Core ML release our input arrays on one of Core ML's threads without
  Python's lock, which can free them there and crash the process later, even while idle. Fixed
  upstream on 2026-09-30 (commit a207b1a) but in no coremltools release yet. The Neural Engine
  helper now keeps each input alive for its next 64 calls, so that late release never frees
  anything. If it still stops, the captions keep switching to the GPU as in 1.7.0.

## 1.7.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- **Lines finish as soon as a sentence ends** (quick end) by default: on an M4 Mac mini the English
  showed 0.40 s after the speaker stopped on average instead of 0.62 s (slowest 0.44 s instead of
  0.77 s), with one sentence per line. `--no-quick-end` brings back the old behaviour.
- **`--turbo` can't crash the captions any more:** Core ML (the Neural Engine) crashed in one of its
  own threads next to the live captioner, so it now runs in a helper process. If the helper stops,
  the captions switch to Parakeet on the GPU, loaded ahead for this, on the spot.
- `bench` compares the races' words ignoring how they're split into lines (it said "0 of 3 the same"
  when quick had only split the sentences).
- The lookup's explanation is shown as the model wrote it, a full sentence, instead of trimmed into
  a fragment.
- The word lookup stops at once when closed, models included, even with lookups still queued.

## 1.6.2

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- `--turbo` still crashed (segmentation fault) on the Mac in 1.6.1 as its race started, so the
  second thread wasn't the cause. `bench` now runs each race in a process of its own, as a real
  session runs, and prints where a crash happened (Python's faulthandler) instead of stopping.
- `bench` races three set-ups: control, **quick** (Parakeet on the GPU plus `--quick-end`: a line
  finishes as soon as a sentence ends, no Neural Engine) and turbo.
- `--quick-end` on its own also starts the early transcription at 0.15 s.
- Closing the word lookup now waits until its two CPU models have actually stopped.

## 1.6.1

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- Fixes `--turbo` crashing (segmentation fault) on the Mac as soon as it started: translation no
  longer runs in a second thread, which MLX doesn't allow. Everything runs in one thread again.
- `--turbo` text now matches the normal version: the Neural Engine does the heavy part of speech
  recognition (about 3.5x faster on an M4 Mac mini), then Parakeet's own decoder reads off the
  words, instead of the Core ML build's simpler one that garbled names and dropped punctuation.
- The word lookup's two CPU models can no longer be left running in the background: each stops
  within a second once the lookup is gone, however it ended. (The bench used to leave them on.)
- The lookup pane loads its dictionary into memory when it opens, so the first highlight is
  instant too.

## 1.6.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- **Word lookup pane:** highlight Japanese in the captions to see each word's reading and meaning
  (JMdict, instant), a translation and a one-line explanation. It runs on the CPU only, in its own
  program, so the captions never wait for it. The app opens it beside the captions;
  `call-captions lookup` by hand, `--no-lookup` to skip it.
- **`--turbo`:** speech recognition on the Neural Engine (Parakeet's Core ML build), translation on
  the GPU side by side, and lines finish as soon as a sentence ends. Falls back to the GPU by itself.
  (Crashed on the Mac; fixed in 1.6.1.)
- **`call-captions bench AUDIO`** now races the current version (control) against turbo on your
  recording in real time, times the word lookup, and runs turbo again with lookups going.
- Parakeet no longer adds stray words at the end of a line when it re-reads a dropped sentence.
- Installs llama.cpp and about 1.6 GB more (the Neural Engine model, the dictionary and two small
  CPU models). Upgrades reuse the dictionary.

## 1.5.1

- `call-captions help` (also `-help`, `-h`, `--help`) now explains the features in plain words:
  what it does, how to use and check it, the voice, speed and accuracy, setup and rollback.
  `call-captions help all` adds every option.

## 1.5.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- The voice is on by default: the English in your left ear, the call in your right. `--no-speak`
  turns it off.
- The default voice is Fred, the classic robot voice, at 240 words per minute.
- The voice never stops the captions: if it can't run (for example on speakers), the window says
  `! Voice off:` and why, and captions carry on.
- `call-captions test-voice` plays a line in each ear to check the routing before a call.
- The captions window starts with the version number.

## 1.4.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- Parakeet (NVIDIA, Japanese) is now the default speech model: the fastest, and at least as
  accurate as Kotoba-Whisper in published read-speech tests. `--asr kotoba` brings back the old
  default. Upgrading downloads Parakeet once (about 2.5 GB).
- Parakeet sometimes stopped after the first of two sentences separated by a short pause; it now
  picks up where it stopped, so no sentence is lost.
- `--hint` says when it has no effect (Parakeet doesn't use it).
- `--speak` no longer skips a line that was still being turned into speech when the next one
  arrived; it skips only when the voice is actually behind.

## 1.3.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- `--speak`: the Mac's built-in voice says each English line in your left ear while the call plays
  in your right ear (`--speak mix`: the call stays in both). Headphones only. If they keep
  talking, it skips to the newest line instead of falling behind. `--voice`, `--rate`,
  `--voice-volume`.
- `--early` (on by default, 0.25 s): the final transcription and translation start at a short
  pause, so a line is usually ready the moment the pause turns out to end the sentence. The text
  is the same as before. In-progress updates no longer start right as a pause begins.
- `--asr qwen3` and `--asr qwen3-small`: Qwen3-ASR (Alibaba, Apache-2.0) on MLX.
- `call-captions bench AUDIO`: compares the speech models' speed and text on your own recording.

## 1.2.0 (milestone)

Code: `milestone/v1.2.0` (commit `8b6f78e`). Rollback formula: `call-captions-1.2.0`.

- Sets up the sound routing automatically: creates **Call Captions Output** (your headset +
  BlackHole) with a small CoreAudio helper built during install; no Audio MIDI Setup needed.
- Switches the Mac's sound output to it only while captions run, and back on exit (Ctrl+C too).
- `call-captions audio` picks a different listening device; warns if the input is BlackHole.
- `call-captions uninstall` also removes Call Captions Output.

## 1.1.0

- `brew install` does the whole setup: Python, packages and models in the formula's own folder,
  so `brew uninstall` removes them; upgrades reuse the models.
- First run adds the app, creates settings from the BlackHole already on the Mac, asks before
  installing BlackHole 2ch only when there is none, and offers to delete the earlier install.
- `call-captions uninstall` removes everything except BlackHole.

## 1.0.0

- First Homebrew release: `call-captions` command, `call-captions setup` downloading the models,
  `Call Captions.app` launcher.
