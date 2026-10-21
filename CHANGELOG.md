# Changelog

Milestones are versions confirmed working on a real Mac. Each is kept installable for rollback
as `brew install jinreiyu/tap/call-captions-X.Y.Z` and on the `milestone/vX.Y.Z` branch.

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
