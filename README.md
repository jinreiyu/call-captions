# Call Captions

Live English captions of a Japanese phone call, on your Mac. Speech recognition and translation
both run on the Mac (Apple Silicon, MLX). No audio is sent anywhere, and nothing is added to the
call: the other person hears a normal call.

```
14:02:11  では、来週の水曜日の午後2時に面接でいかがでしょうか。
          ➜ Then how about having an interview at 2 pm next Wednesday?   (asr 0.83s · mt 0.21s)

  … 前職ではどういったお仕事を                          ← still talking (updates every second)
```

Speech recognition uses NVIDIA Parakeet (the fastest of the Japanese models compared), and translation uses
LiquidAI LFM2-ENJP (the smallest Japanese-to-English model). `call-captions bench` times them on
your Mac.

## Install

Needs an Apple Silicon Mac (M1 or newer) with macOS 14 or later, and [Homebrew](https://brew.sh).

```bash
brew install jinreiyu/tap/call-captions
```

That one command installs everything: the program, its own Python, the packages and the speech
and translation models, the word lookup's dictionary and models (about 5 GB in all) and
[llama.cpp](https://github.com/ggml-org/llama.cpp). All of it goes into Homebrew's folder, so
`brew uninstall` removes all of it again. Upgrades (`brew upgrade call-captions`) reuse the models.

Then start it once:

```bash
call-captions
```

The first run adds **Call Captions** to your Applications folder and creates your settings file
(`~/call-captions/options.txt`). It also:

- **BlackHole:** call audio reaches the captioner through a
  [BlackHole](https://github.com/ExistentialAudio/BlackHole) loopback driver. If you already have
  any BlackHole (2ch, 16ch or 64ch) it uses yours and sets it in your settings. Only if you have
  none does it **ask** whether to install BlackHole 2ch. It never installs a second one.
- **Sound routing:** it asks where you want to hear calls (your headset is suggested) and creates
  **Call Captions Output**, a multi-output device that plays the call to that headset and into
  BlackHole at the same time. You don't need to touch Audio MIDI Setup. Change the headset later
  with `call-captions audio`.
- **Older installs:** if an earlier version left Python and models in `~/call-captions`, it asks
  whether to delete them, keeping your `options.txt`.

`call-captions setup` runs the same checks without starting the captions.

## Route the call into the Mac

The sound routing is automatic (see above). Two things only you can do:

1. **A headset with a mic.** A Mac mini has no built-in microphone. A USB headset is the most
   reliable; AirPods switch to a lower-quality mode when their mic is in use, so test them first.
   Set it as the Mac's input: System Settings → Sound → **Input**.
2. **Take iPhone calls on the Mac.** iPhone: Settings → Apps → Phone → Calls on Other Devices →
   on, and turn on your Mac. Mac: FaceTime → Settings → **Calls from iPhone**. Same Apple ID,
   Wi-Fi and Bluetooth on.

While captions run, the Mac's sound output is **Call Captions Output**; when you stop them it
switches back to what you had. The volume keys don't change a multi-output device, so set your
headset's volume before the call or use the buttons on the headset.

<details>
<summary>Doing the routing by hand instead</summary>

Open **Audio MIDI Setup** → **+** → **Create Multi-Output Device**. Tick your headset first, then
your BlackHole, and tick **Drift Correction** on the BlackHole row. Then set System Settings →
Sound → Output to that device before a call, and back afterwards.
</details>

## Use it

Double-click **Call Captions** in Applications **before** the call, then answer or place the call
**on the Mac**. It opens an iTerm (or Terminal) window with the captions and switches the sound
output; Ctrl+C stops the captions and switches the sound back. The first time, macOS
asks to let Call Captions control the terminal and the terminal to use the microphone: allow both.

- Cyan line: what they said. Yellow ➜ line: the English.
- Dim `…` line: text while they are still talking. Dim `~` line: a draft translation of a long
  sentence. The final version replaces both.
- Make the text bigger with **Cmd +**.

The app reads its options from `~/call-captions/options.txt`, one flag per line, for example:

```
--device "BlackHole 64ch"
--hint "株式会社サンプル 山田"
```

From a terminal it's the same: `call-captions --hint "株式会社サンプル 山田"`.

### Test before a real call

- A recording: `call-captions --file ~/Downloads/voicemail.m4a`
- Live: run `call-captions` and play a Japanese video, or call someone from the Mac.

## Hear the English in your left ear

On by default since 1.5.0: a robot voice says each English line in your **left ear**, while the
call plays only in your **right ear**. It's the speech built into macOS (free, no download, almost
no CPU). The English goes only to your headset, never into the call. **Headphones only**: on the
Mac's speakers it turns itself off, since your microphone could pass it to the caller.

Check it once before a call:

```bash
call-captions test-voice
```

You should hear "Left ear…" on the left and "Right ear…" on the right (the right one plays the
way a call does).

- `--no-speak`: captions only. `--speak mix`: the call stays in both ears, the English is added
  on the left.
- `--voice Fred` (default) is the classic robot voice, the quickest to make. Any voice from
  `say -v '?'` works, for example `--voice Samantha` for a clearer one.
- `--rate 240` is the speed in words per minute; faster keeps up better.
- If they keep talking, the voice skips a line rather than falling behind (the line stays on
  screen), so it always speaks the newest sentence.
- Set the volume on your headset: while the call is in your right ear only, the Mac's volume
  keys change BlackHole's level instead.
- The window says `Voice on` when it starts, or `! Voice off:` and why.

Try it on a recording: `call-captions --file ~/Downloads/voicemail.m4a`.

## Look up a word

Highlight Japanese in the captions with the mouse. The **lookup pane** beside the captions shows:

```
伺ってもよろしいですか
  伺う    うかがう   to call on someone; to ask; to inquire (humble)       ← dictionary, instant
  宜しい  よろしい   good; OK (honorific)
  ➜ May I ask?                                                          ← translation
  ✎ "Would it be okay to ask?" It's polite and respectful.              ← explanation
```

- The app opens the pane for you (an iTerm2 split, or a second Terminal window). By hand:
  `call-captions lookup`. `--no-lookup` in `options.txt` stops the app from opening it.
- It runs **on the CPU only**, in its own program, so it never slows the captions or the voice:
  the dictionary is [JMdict](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project),
  the translation the same LFM2-ENJP model (CPU build), the explanation LiquidAI LFM2.5-1.2B, both
  through llama.cpp with the GPU switched off.
- iTerm2 copies whatever you highlight, which is how the pane sees it. In Terminal, press Cmd+C
  after highlighting. Nothing is saved and nothing leaves the Mac.

## Turbo: every chip on its own job

```bash
call-captions --turbo
```

The heavy part of speech recognition (Parakeet's encoder) moves to the Mac's **Neural Engine**,
about 3x faster on an M4 Mac mini (that part measured 0.03 to 0.07 s per sentence, against 0.12
to 0.24 s for the whole GPU version). The
words are still read off by Parakeet's own decoder, so the text matches the normal version. A line
also shows as soon as a sentence ends (。？) instead of waiting out the full pause. If the Neural
Engine model can't load, it falls back to the GPU version by itself.

| Job | Normal | `--turbo` |
| --- | --- | --- |
| Hearing Japanese | GPU | Neural Engine (encoder), GPU (decoder, a few ms) |
| Translating | GPU | GPU |
| Word lookup | CPU | CPU |
| Voice | CPU | CPU |

### Race the two on your Mac

```bash
call-captions bench ~/Downloads/voicemail.m4a
```

Plays your recording in real time through three set-ups and times each line from the end of
speech to the English on screen: **control** (the current version), **quick** (the same, plus
`--quick-end`: a line finishes as soon as a sentence ends) and **turbo** (`--turbo`: quick plus the
Neural Engine). Each runs in a process of its own, like a real session, so if one crashes the bench
says where and carries on. Then it times the word lookup and runs the fastest set-up again while
lookups fire every second, to show the lookup doesn't slow it down.
Add `--models parakeet,parakeet-ane,qwen3,kotoba` to also compare other speech models (first use
downloads them). Put the winner (`--quick-end` or `--turbo`) in `options.txt`.

## Options

| Option | What it does |
| --- | --- |
| `--turbo` | Speech recognition's encoder on the Neural Engine, and lines finish at sentence ends. |
| `--quick-end` | A line finishes as soon as a sentence ends (。？) instead of after the full pause. Any speech model. |
| `--asr parakeet` | Default. NVIDIA Parakeet TDT-CTC 0.6B Japanese on the GPU. Adds punctuation. Spells English names as it hears them. |
| `--asr parakeet-ane` | The same Parakeet on the Neural Engine (Core ML). `--turbo` uses it. |
| `--asr qwen3` | Qwen3-ASR 1.7B (8-bit). Most accurate on natural conversation in published tests; slower. |
| `--asr qwen3-small` | Qwen3-ASR 0.6B (8-bit). Faster, less accurate. |
| `--asr kotoba` | Kotoba-Whisper v2.0, the default before 1.4.0. |
| `--mt lfm2` | Default. LiquidAI LFM2-350M-ENJP-MT. |
| `--mt plamo` | PLaMo 2 Translate (Preferred Networks), 9.5B, 4-bit, about 5.5 GB. Slower. |
| `--mt none` | Japanese captions only. |
| `--hint "..."` | Names you expect; nudges Kotoba-Whisper and Qwen3-ASR toward those spellings. |
| `--no-speak` | Captions only. By default the English is said in your left ear, the call in your right. |
| `--speak mix` | Keep the call in both ears; the English is added on the left. |
| `--voice NAME` / `--rate 240` | Voice (default Fred, the robot voice) and speed in words per minute. |
| `--english-only` | Hide the Japanese lines. |
| `--no-partials` | Only show finished sentences. |
| `--end-silence 0.4` | Pause (seconds) that ends a sentence. Default 0.5. Shorter is sooner but can cut sentences in two. |
| `--early 0.25` | Start the final text after this much pause, before knowing it ends the sentence. Same text, sooner. `0` turns it off. 0.15 with `--turbo`. |
| `--no-lookup` | (in `options.txt`) The app doesn't open the word lookup pane. |
| `--log calls.txt` | Save a transcript (time, Japanese, English). |
| `--device NAME` | Input to listen on. Default: the first device named BlackHole. |
| `--list-devices` | Show input devices. |

`call-captions help` (or `-help`) explains every feature in plain words; `call-captions help all`
lists every option.

## How it works

| Step | Model | Size |
| --- | --- | --- |
| Find speech | [Silero VAD](https://github.com/snakers4/silero-vad) | 2 MB |
| Japanese speech → text | [NVIDIA Parakeet TDT-CTC 0.6B ja](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja) (MLX) | 0.6B |
| Japanese → English | [LFM2-350M-ENJP-MT](https://huggingface.co/LiquidAI/LFM2-350M-ENJP-MT) (MLX) | 0.35B |

Both models do one job only: Parakeet recognizes only Japanese, and LFM2-ENJP translates only
between Japanese and English. Each sentence is translated on its own, so now and then it
guesses the wrong person ("I" vs "you") where Japanese leaves it out. Names are the weakest spot
for recognition.

The models are downloaded from Hugging Face during setup and are covered by their own licenses.
The word lookup uses the [JMdict](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project)
dictionary files, the property of the Electronic Dictionary Research and Development Group, used in
conformance with the Group's [licence](https://www.edrdg.org/edrdg/licence.html) (CC BY-SA 4.0); they
are downloaded from EDRDG during setup.

## Troubleshooting

- **No captions:** start the captions before the call, so the call uses **Call Captions Output**.
  `call-captions setup` rebuilds the routing if something changed (for example a different
  headset); `call-captions audio` picks a new one.
- **Silence even though audio plays:** System Settings → Privacy & Security → Microphone → allow
  your terminal app, then reopen it. Without that permission macOS gives apps silence.
- **The caller can't hear you:** System Settings → Sound → Input should be your headset mic, not
  BlackHole. `call-captions` warns about this.
- **Volume booster apps:** apps that boost one app's volume (for example per-app mixers) can
  mute that app's audio when the output is a multi-output device. Leave the calling app at 100%.
- **"falling behind real time":** add `--no-partials`.
- **No voice:** run `call-captions version` (it should be 1.5.0 or later), then
  `call-captions test-voice`. The captions window prints `! Voice off:` and the reason when the
  voice can't run (for example when the sound plays on speakers).
- **Can't hear the call in your right ear:** stop with Ctrl+C and use `--speak mix`. If the voice
  ever stops, the call goes back to both ears on its own.
- **The voice is late:** a faster `--rate` or a faster speech model (`call-captions bench`).

## Uninstall

```bash
call-captions uninstall
```

One command removes the app, the Call Captions Output device, your settings and, through
Homebrew, the program with its Python and models. Homebrew also removes helpers it installed only for Call Captions (such as `uv`);
anything you installed yourself stays. It never touches BlackHole or audio devices you made yourself.

Running only `brew uninstall call-captions` removes the program, Python and models too, and
leaves the small app launcher, your settings in `~/call-captions` and the Call Captions Output
device (so you can reinstall or roll back without setting up again).

## Versions and rollback

The latest version is 1.6.0: the word lookup pane, `--turbo` and the control-vs-turbo bench. The
voice is on by default since 1.5.0. Version 1.2.0 is a
milestone: confirmed working and kept installable. To stay on your current version,
`brew pin call-captions`. If an update ever breaks something:

```bash
brew uninstall call-captions && brew install jinreiyu/tap/call-captions-1.2.0 && call-captions
```

Your settings and sound routing are kept. See [CHANGELOG.md](CHANGELOG.md) for versions and
[docs/PROJECT.md](docs/PROJECT.md) for how everything works.

## License

Proprietary. See [LICENSE](LICENSE).
