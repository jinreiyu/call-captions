# Changelog

Milestones are versions confirmed working on a real Mac. Each is kept installable for rollback
as `brew install jinreiyu/tap/call-captions-X.Y.Z` and on the `milestone/vX.Y.Z` branch.

## 1.6.0

Not a milestone yet: roll back with `call-captions-1.2.0` if anything is wrong.

- **Word lookup pane:** highlight Japanese in the captions to see each word's reading and meaning
  (JMdict, instant), a translation and a one-line explanation. It runs on the CPU only, in its own
  program, so the captions never wait for it. The app opens it beside the captions;
  `call-captions lookup` by hand, `--no-lookup` to skip it.
- **`--turbo`:** speech recognition on the Neural Engine (Parakeet's Core ML build), translation on
  the GPU side by side, and lines finish as soon as a sentence ends. Falls back to the GPU by itself.
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
