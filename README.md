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
call-captions setup
```

`setup` installs Python and the packages and downloads the models (about 2 GB) into
`~/call-captions`, then adds **Call Captions** to your Applications folder.

Call audio reaches the captioner through a [BlackHole](https://github.com/ExistentialAudio/BlackHole)
loopback driver. Any BlackHole works (2ch, 16ch or 64ch). If you have none:

```bash
brew install --cask blackhole-2ch
```

Update with `brew upgrade call-captions`. The next run updates the Python packages if needed.

## Route the call into the Mac

One-time setup. The idea: take the call on the Mac, and send the caller's voice both to your
headset and into BlackHole, which the captioner listens to. Your own voice goes from your mic into
the call and is never captioned.

1. **Headset with a mic.** A Mac mini has no built-in microphone. A USB headset is the most
   reliable; AirPods switch to a lower-quality mode when their mic is in use, so test them first.
2. **Take iPhone calls on the Mac.** iPhone: Settings → Apps → Phone → Calls on Other Devices →
   on, and turn on your Mac. Mac: FaceTime → Settings → **Calls from iPhone**. Same Apple ID,
   Wi-Fi and Bluetooth on.
3. **Split the output.** Open **Audio MIDI Setup** → **+** → **Create Multi-Output Device**. Tick
   your headset first, then your BlackHole. Tick **Drift Correction** on the BlackHole row.
4. **System Settings → Sound:** Output = **Multi-Output Device**, Input = your headset mic.

The volume keys don't work on a Multi-Output Device; set the headset volume in Audio MIDI Setup.
Switch Sound Output back to your usual device after calls.

## Use it

Double-click **Call Captions** in Applications, then answer or place the call **on the Mac**. It
opens an iTerm (or Terminal) window with the captions; Ctrl+C stops them. The first time, macOS
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

- **No captions:** `call-captions --list-devices` should list your BlackHole. Make sure that
  BlackHole is ticked in the Multi-Output Device and Sound Output is the Multi-Output Device.
  If you have several BlackHoles, set `--device` to the one you ticked.
- **Silence even though audio plays:** System Settings → Privacy & Security → Microphone → allow
  your terminal app, then reopen it. Without that permission macOS gives apps silence.
- **Volume booster apps:** apps that boost one app's volume (for example per-app mixers) can
  mute that app's audio when the output is a Multi-Output Device. Leave the calling app at 100%.
- **"falling behind real time":** add `--no-partials`, or try `--asr parakeet`.

## Uninstall

```bash
call-captions uninstall
brew uninstall call-captions
```

The first removes the app and `~/call-captions` (models and settings). Your BlackHole driver and
the Multi-Output Device are left alone.

## License

Proprietary. See [LICENSE](LICENSE).
