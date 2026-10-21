# Call Captions

Live English captions of a Japanese phone call, on your Mac. Speech recognition and translation
both run on the Mac (Apple Silicon, MLX). No audio is sent anywhere, and nothing is added to the
call: the other person hears a normal call.

```
14:02:11  では、来週の水曜日の午後2時に面接でいかがでしょうか。
          ➜ Then how about having an interview at 2 pm next Wednesday?   (asr 0.83s · mt 0.21s)

  … 前職ではどういったお仕事を                          ← still talking (updates every second)
```

Measured on an M4 Mac mini: about 0.8 s to recognize a sentence and 0.2 s to translate it, after
the speaker pauses.

## Install

Needs an Apple Silicon Mac (M1 or newer) with macOS 14 or later, and [Homebrew](https://brew.sh).

```bash
brew install jinreiyu/tap/call-captions
```

That one command installs everything: the program, its own Python, the packages and the speech
and translation models (about 2 GB). All of it goes into Homebrew's folder for Call Captions, so
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

## Options

| Option | What it does |
| --- | --- |
| `--asr kotoba` | Default. Kotoba-Whisper v2.0, Japanese only. |
| `--asr parakeet` | NVIDIA Parakeet TDT-CTC 0.6B Japanese. Adds punctuation. |
| `--mt lfm2` | Default. LiquidAI LFM2-350M-ENJP-MT. |
| `--mt plamo` | PLaMo 2 Translate (Preferred Networks), 9.5B, 4-bit, about 5.5 GB. Slower. |
| `--mt none` | Japanese captions only. |
| `--hint "..."` | Names you expect; nudges Kotoba-Whisper toward those spellings. |
| `--english-only` | Hide the Japanese lines. |
| `--no-partials` | Only show finished sentences. |
| `--end-silence 0.4` | Pause (seconds) that ends a sentence. Default 0.5. |
| `--log calls.txt` | Save a transcript (time, Japanese, English). |
| `--device NAME` | Input to listen on. Default: the first device named BlackHole. |
| `--list-devices` | Show input devices. |

`call-captions help` lists them all.

## How it works

| Step | Model | Size |
| --- | --- | --- |
| Find speech | [Silero VAD](https://github.com/snakers4/silero-vad) | 2 MB |
| Japanese speech → text | [Kotoba-Whisper v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0) (MLX) | 0.76B |
| Japanese → English | [LFM2-350M-ENJP-MT](https://huggingface.co/LiquidAI/LFM2-350M-ENJP-MT) (MLX) | 0.35B |

Both models do one job only: Kotoba-Whisper is distilled for Japanese, and LFM2-ENJP translates
only between Japanese and English. Each sentence is translated on its own, so now and then it
guesses the wrong person ("I" vs "you") where Japanese leaves it out. Names are the weakest spot
for recognition.

The models are downloaded from Hugging Face during setup and are covered by their own licenses.

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
- **"falling behind real time":** add `--no-partials`, or try `--asr parakeet`.

## Uninstall

```bash
call-captions uninstall
```

One command removes the app, the Call Captions Output device, your settings and, through
Homebrew, the program with its Python and models. Homebrew also removes helpers it installed only for Call Captions (such as `uv`);
anything you installed yourself stays. It never touches BlackHole or audio devices you made yourself.

Running only `brew uninstall call-captions` removes the program, Python and models too, and
leaves just the small app launcher and `~/call-captions/options.txt`.

## License

Proprietary. See [LICENSE](LICENSE).
