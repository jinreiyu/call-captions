# Notes for Claude

Read `docs/PROJECT.md` first: it explains the architecture, the Homebrew install design, the
tests and the release process. `CHANGELOG.md` lists versions and milestones.

## Rules for this repo

- **This repo is public. Never commit personal data**: no names, phone numbers, email addresses
  (other than the one already in LICENSE), employers, recruiters, call contents or device-specific
  details from the owner's Mac. Use placeholders such as `株式会社サンプル 山田` in examples.
- **Never break BlackHole.** Never install a second BlackHole when one exists, never remove one,
  never change its settings. Other audio, recording or streaming apps may depend on it.
- **Keep rollback working.** Before changing behaviour, the latest milestone must stay
  installable (`call-captions-X.Y.Z` formula in jinreiyu/homebrew-tap, pinned to the
  `milestone/vX.Y.Z` branch). Don't rewrite or delete milestone branches.
- **Releases**: bump `VERSION` in `bin/call-captions`, push `main`, then point
  `Formula/call-captions.rb` in jinreiyu/homebrew-tap at the new commit tarball with its sha256.
  Only make a new milestone after the owner confirms the version works on their Mac.
- Milestone formulas are named `call-captions-X.Y.Z`, not `@X.Y.Z` (an `@` name makes Homebrew
  load the base formula, which fails after uninstall because uninstall drops tap trust).
- The Homebrew post-install step is sandboxed: network allowed, writes only inside Homebrew's
  folders, temporary HOME. Anything touching the home folder or /Applications belongs in the
  first run (`first_run` in `bin/call-captions`).
- `src/audio.c` can't be compiled on Linux without a macOS SDK. Check it with
  `clang -target arm64-apple-macos14 -isysroot <MacOSX SDK> -Wall -Wextra -c src/audio.c`.
- Test formula changes with real Homebrew where possible (a test copy without the macOS-only lines
  and with tiny stand-in models works on Linux).
