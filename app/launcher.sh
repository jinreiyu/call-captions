#!/bin/bash
# Call Captions.app: opens iTerm2 (or Terminal) running `call-captions` with the options in
# the data folder's options.txt. The captioner runs inside the terminal so it uses the
# terminal's microphone permission, which reading BlackHole needs.
# `call-captions app` fills in the two paths below when it builds the app.
cli="__CLI__"
data="__DATA__"
export PATH="$PATH:/usr/bin:/bin"

alert() {
  osascript - "$1" >/dev/null 2>&1 <<'EOF'
on run argv
  display alert "Call Captions" message (item 1 of argv)
end run
EOF
}

if [[ ! -x "$cli" || ! -x "$data/.venv/bin/python" ]]; then
  alert "Call Captions isn't set up yet. In Terminal, run: call-captions setup"
  exit 1
fi

if [[ -d /Applications/iTerm.app || -d "$HOME/Applications/iTerm.app" ]]; then
  term=com.googlecode.iterm2
else
  term=com.apple.Terminal
fi

# Already running: bring its window forward instead of starting a second copy.
if pgrep -f "$data/.venv/bin/python .*live_captions.py" >/dev/null; then
  osascript -e "tell application id \"$term\" to activate" >/dev/null 2>&1
  exit 0
fi

opts=""
if [[ -f "$data/options.txt" ]]; then
  opts="$(grep -v '^[[:space:]]*#' "$data/options.txt" | tr '\n' ' ')"
fi
printf -v run '%q' "$cli"
cmd="clear; $run $opts"

if [[ "$term" == com.googlecode.iterm2 ]]; then
  osascript - "$cmd" <<'EOF'
on run argv
  set theCmd to item 1 of argv
  set wasRunning to application id "com.googlecode.iterm2" is running
  tell application id "com.googlecode.iterm2"
    activate
    if not wasRunning then
      repeat 30 times
        if (count of windows) > 0 then exit repeat
        delay 0.1
      end repeat
    end if
    if wasRunning or (count of windows) = 0 then
      set w to (create window with default profile)
    else
      set w to current window
    end if
    tell current session of w to write text theCmd
  end tell
end run
EOF
else
  osascript - "$cmd" <<'EOF'
on run argv
  set theCmd to item 1 of argv
  set wasRunning to application id "com.apple.Terminal" is running
  tell application id "com.apple.Terminal"
    activate
    if not wasRunning then
      repeat 30 times
        if (count of windows) > 0 then exit repeat
        delay 0.1
      end repeat
    end if
    if wasRunning or (count of windows) = 0 then
      do script theCmd
    else
      do script theCmd in window 1
    end if
  end tell
end run
EOF
fi

if [[ $? -ne 0 ]]; then
  alert "Couldn't open a terminal window. Allow it in System Settings → Privacy & Security → Automation → Call Captions, then open Call Captions again."
  exit 1
fi
