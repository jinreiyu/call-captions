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

Options you want every time go in `~/call-captions/options.txt`, one per line, for example:

```
--device "BlackHole 64ch"
--voice-speed 2
```

`call-captions`, the app and `test-voice` all use it (since 1.9.1; before, only the app did). An
option you type wins for that run: `call-captions --voice-speed 1.5`.

### Test before a real call

- A recording: `call-captions --file ~/Downloads/voicemail.m4a`
- Live: run `call-captions` and play a Japanese video, or call someone from the Mac.

## Hear the English in your left ear

On by default since 1.5.0: a voice says each English line in your **left ear**, while the
call plays only in your **right ear**. It's the speech built into macOS (free, almost no CPU), in
the Mac's **system voice**: Siri, if you choose a Siri voice there (since 1.8.0; before, the robot
voice Fred). The English goes only to your headset, never into the call. **Headphones only**: on the
Mac's speakers it turns itself off, since your microphone could pass it to the caller.

Check it once before a call:

```bash
call-captions test-voice
```

You should hear "Left ear…" on the left and "Right ear…" on the right (the right one plays the
way a call does).

- `--no-speak`: captions only. `--speak mix`: the call stays in both ears, the English is added
  on the left.
- **Siri**: System Settings → Accessibility → Read & Speak (Spoken Content before macOS 26) →
  System voice → choose a Siri voice. macOS doesn't let apps pick a Siri voice by name, only use
  it as the system voice. The window says which voice it uses when it starts.
- `--voice Fred` is the classic robot voice, the quickest to start talking. Any voice from
  `say -v '?'` works, for example `--voice Samantha`.
- `--voice-speed 1.5` (the default) plays the voice one and a half times as fast, at the same
  pitch, whatever the voice (like a podcast at 1.5x). `1` is its normal speed, `2` twice, up to 3.
  Faster keeps up better.
- `--rate 200` sets the voice's own words per minute instead (before `--voice-speed`); not every
  voice follows it.
- If they keep talking, the voice skips a line rather than falling behind (the line stays on
  screen), so it always speaks the newest sentence.
- Set the volume on your headset: while the call is in your right ear only, the Mac's volume
  keys change BlackHole's level instead.
- The window says `Voice on` when it starts, or `! Voice off:` and why.

Try it on a recording: `call-captions --file ~/Downloads/voicemail.m4a`.

## Look up a word

Highlight Japanese in the captions with the mouse. The answer shows right there, among the
captions behind a magenta bar, and the meaning is said in your left ear:

```
┃ 伺ってもよろしいですか
┃   伺う    うかがう   to call on someone; to ask; to inquire (humble)     ← dictionary, instant
┃   宜しい  よろしい   good; OK (honorific)
┃   ➜ May I ask?                                                        ← translation, 0.2 s
┃   ✎ "Would it be okay to ask?" It's polite and respectful.            ← explanation, 1 s
```

- On by default (since 1.8.0, however you start Call Captions). The voice says the dictionary
  meaning for one word, otherwise the translation, right after the line it's saying, and never
  skips it. `--no-lookup-voice` keeps it silent, `--no-lookup` turns the lookup off.
- The explanation comes from a small model and is a hint: when it disagrees with the dictionary
  line, trust the dictionary.
- `call-captions lookup` runs it on its own in any terminal window (`--stdin` to type phrases).
- It runs **on the CPU only**, in its own program, so it never slows the captions or the voice:
  the dictionary is [JMdict](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project),
  the translation the same LFM2-ENJP model (CPU build), the explanation LiquidAI LFM2.5-1.2B, both
  through llama.cpp with the GPU switched off.
- iTerm2 copies whatever you highlight, which is how the lookup sees it. In Terminal (or any
  other app), press Cmd+C after highlighting. Nothing is saved and nothing leaves the Mac.

## Speed

**Quick end (on by default since 1.7):** a line shows as soon as a sentence ends (。？) instead of
after the full half-second pause. On an M4 Mac mini, on a real voicemail played like a call, the
English appeared **0.40 s** after the speaker stopped on average (slowest 0.44 s), against 0.62 s
(slowest 0.77 s) before. Sentences also come out one per line, which the translator handles
better. `--no-quick-end` brings back the old behaviour.

**Turbo (on by default since 1.8):** the heavy part of speech recognition (Parakeet's encoder)
runs on the Mac's **Neural Engine**, about 1.7x faster than the GPU there (0.07 to 0.13 s per
sentence against 0.13 to 0.25 s). Live on an M4 Mac mini, lines showed 0.37 s after the speaker
stopped on average, against 0.40 s on the GPU. It runs in a helper process of its own: if it ever
stops, the captions switch to the GPU on the spot and carry on. Its compressed (8-bit) model can
hear a word or a name a little differently; `--no-turbo` keeps everything on the GPU.

| Job | Default | `--no-turbo` |
| --- | --- | --- |
| Hearing Japanese | Neural Engine (encoder, in a helper process), GPU (decoder) | GPU |
| Translating | GPU | GPU |
| Word lookup | CPU | CPU |
| Voice | CPU | CPU |

### Race the two on your Mac

```bash
call-captions bench ~/Downloads/voicemail.m4a
```

Plays your recording in real time through three set-ups and times each line from the end of
speech to the English on screen: **control** (as up to 1.6: a line after the full pause),
**quick** (as in 1.7, now `--no-turbo`) and **turbo** (the default: quick plus the Neural Engine).
Each runs in a process of its own, like a real session, so if one crashes the bench
says where and carries on. Then it times the word lookup and runs the fastest set-up again while
lookups fire every second, to show the lookup doesn't slow it down.
Add `--models parakeet,parakeet-ane,qwen3,kotoba` to also compare other speech models (first use
downloads them). If quick suits you better, put `--no-turbo` in `options.txt`.

## Options

| Option | What it does |
| --- | --- |
| `--no-turbo` | Speech recognition on the GPU only, as before 1.8. (By default its encoder runs on the Neural Engine.) |
| `--no-quick-end` | Wait out the full pause before finishing a line, as up to 1.6. (By default a line finishes as soon as a sentence ends.) |
| `--asr parakeet` | Default. NVIDIA Parakeet TDT-CTC 0.6B Japanese, its encoder on the Neural Engine (GPU with `--no-turbo`). Adds punctuation. Spells English names as it hears them. |
| `--asr qwen3` | Qwen3-ASR 1.7B (8-bit). Most accurate on natural conversation in published tests; slower. |
| `--asr qwen3-small` | Qwen3-ASR 0.6B (8-bit). Faster, less accurate. |
| `--asr kotoba` | Kotoba-Whisper v2.0, the default before 1.4.0. |
| `--mt lfm2` | Default. LiquidAI LFM2-350M-ENJP-MT. |
| `--mt plamo` | PLaMo 2 Translate (Preferred Networks), 9.5B, 4-bit, about 5.5 GB. Slower. |
| `--mt none` | Japanese captions only. |
| `--hint "..."` | Names you expect; nudges Kotoba-Whisper and Qwen3-ASR toward those spellings. |
| `--no-speak` | Captions only. By default the English is said in your left ear, the call in your right. |
| `--speak mix` | Keep the call in both ears; the English is added on the left. |
| `--voice NAME` | Voice (default: the Mac's system voice, Siri if you chose it; `Fred` is the robot). |
| `--voice-speed 1.5` | How fast the voice talks, same pitch: 1 normal, 1.5 default, up to 3. |
| `--rate 200` | Words per minute for the voice itself, before `--voice-speed`. |
| `--voice-volume 1.5` | Louder voice, 0 to 2. |
| `--english-only` | Hide the Japanese lines. |
| `--no-partials` | Only show finished sentences. |
| `--end-silence 0.4` | Pause (seconds) that ends a sentence. Default 0.5. Shorter is sooner but can cut sentences in two. |
| `--early 0.15` | Start the final text after this much pause, before knowing it ends the sentence. Same text, sooner. `0` turns it off. 0.25 with `--no-quick-end`. |
| `--no-lookup` | No word lookup. |
| `--no-lookup-voice` | Show word lookups but don't say them. |
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
- **The voice is late:** a faster `--voice-speed` (2 or more), or `--voice Fred`, which starts
  talking soonest.

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

The latest version is 1.8.0: turbo (the Neural Engine) and the Mac's system voice (Siri) by
default, and the word lookup shows and speaks in the captions window. Lines finish as soon as a
sentence ends since 1.7, and the voice is on by default since 1.5.0. Version 1.2.0 is a
milestone: confirmed working and kept installable. To stay on your current version,
`brew pin call-captions`. If an update ever breaks something:

```bash
brew uninstall call-captions && brew install jinreiyu/tap/call-captions-1.2.0 && call-captions
```

Your settings and sound routing are kept. See [CHANGELOG.md](CHANGELOG.md) for versions and
[docs/PROJECT.md](docs/PROJECT.md) for how everything works.

## License

Proprietary. See [LICENSE](LICENSE).
