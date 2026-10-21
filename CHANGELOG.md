# Changelog

Milestones are versions confirmed working on a real Mac. Each is kept installable for rollback
as `brew install jinreiyu/tap/call-captions-X.Y.Z` and on the `milestone/vX.Y.Z` branch.

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
