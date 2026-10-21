#!/usr/bin/env python3
"""Call Captions word lookup: highlight Japanese in the captions window to see what it means.

Runs in its own window or pane and only on the CPU, so the live captions never wait for it (their
speech recognition runs on the Neural Engine or the GPU and their translation on the GPU):

  dictionary   JMdict (EDRDG) words and set phrases with readings, instant
  ➜            the highlighted text translated by LiquidAI LFM2-350M-ENJP-MT (llama.cpp, CPU)
  ✎            a one-line explanation by LiquidAI LFM2.5-1.2B-Instruct (llama.cpp, CPU), given the
               dictionary results so it stays accurate, shown as it's written

iTerm2 copies whatever you highlight, so this watches for Japanese text being copied. Nothing
is saved, and nothing leaves the Mac.
"""

import argparse
import gzip
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DICT_DIR = os.path.join(HERE, "dict")
DICT_DB = os.path.join(DICT_DIR, "jmdict.sqlite")
JMDICT_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"

# Small models in llama.cpp's format, run on the CPU only.
TRANSLATOR = ("LiquidAI/LFM2-350M-ENJP-MT-GGUF", "LFM2-350M-ENJP-MT-Q8_0.gguf")
EXPLAINER = ("LiquidAI/LFM2.5-1.2B-Instruct-GGUF", "LFM2.5-1.2B-Instruct-Q4_K_M.gguf")
# Chosen over LFM2.5-1.2B-JP and Qwen3-1.7B on interview phrases. Without the dictionary lines in
# the message, all three got some phrases wrong or answered in Japanese.
EXPLAIN_PROMPT = (
    "You explain Japanese to an English speaker during a live job interview call. Reply in English "
    "with one sentence of at most 20 words: what the phrase means here and its politeness (humble, "
    "honorific or polite). Do not repeat the Japanese."
)
OPENERS = ("The phrase means ", "This phrase means ", "The phrase ", "This phrase ")

JAPANESE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
DIM, BOLD, CYAN, YELLOW, GREEN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[93m", "\033[32m", "\033[0m"

# JMdict marks politeness with these; everything else in its notes is left out to stay short.
POLITENESS = {
    "humble (kenjougo) language": "humble",
    "honorific or respectful (sonkeigo) language": "honorific",
    "polite (teineigo) language": "polite",
}


# ---------------------------------------------------------------- dictionary


def build_dictionary(gz_path=None, db_path=DICT_DB):
    """Turn JMdict's XML into a small SQLite file for instant lookups (done once at install)."""
    import xml.etree.ElementTree as ET

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if gz_path is None:
        gz_path = os.path.join(os.path.dirname(db_path), "JMdict_e.gz")
        urllib.request.urlretrieve(JMDICT_URL, gz_path)
    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE entry (id INTEGER PRIMARY KEY, score INTEGER, phrase INTEGER, kanji TEXT, kana TEXT, "
               "senses TEXT)")
    db.execute("CREATE TABLE form (form TEXT, id INTEGER)")
    rows, forms = [], []
    with gzip.open(gz_path, "rb") as f:
        for _, el in ET.iterparse(f):
            if el.tag != "entry":
                continue
            seq = int(el.findtext("ent_seq"))
            kanji = [k.findtext("keb") for k in el.findall("k_ele")]
            kana = [r.findtext("reb") for r in el.findall("r_ele")]
            score = len(el.findall("k_ele/ke_pri")) + len(el.findall("r_ele/re_pri"))  # how common
            phrase = any((p.text or "").startswith("expressions") for p in el.iter("pos"))
            senses = []
            for s in el.findall("sense"):
                glosses = [g.text for g in s.findall("gloss") if g.text]
                notes = sorted({POLITENESS[m.text] for m in s.findall("misc") if m.text in POLITENESS})
                if glosses:
                    senses.append({"g": glosses, "m": notes})
            el.clear()
            if not senses:
                continue
            rows.append((seq, score, int(phrase), json.dumps(kanji, ensure_ascii=False),
                         json.dumps(kana, ensure_ascii=False), json.dumps(senses, ensure_ascii=False)))
            forms += [(x, seq) for x in set(kanji + kana) if x]
    db.executemany("INSERT INTO entry VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.executemany("INSERT INTO form VALUES (?, ?)", forms)
    db.execute("CREATE INDEX form_index ON form (form)")
    db.commit()
    db.close()
    os.replace(tmp, db_path)
    os.remove(gz_path)
    return len(rows)


def hiragana(katakana):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in katakana)


class Dictionary:
    # Particles, endings and punctuation: never the start of a lookup, only inside a set phrase.
    GRAMMAR = ("助詞", "助動詞", "補助記号", "記号", "空白")
    SKIP = GRAMMAR + ("接尾辞",)  # a suffix (書 in 履歴書) can end a word but not start one
    # Helper verbs after て (ている, ておく …) say nothing on their own.
    HELPERS = {"いる", "居る", "ある", "有る", "おる", "居る", "くる", "来る", "いく", "行く", "おく", "置く",
               "しまう", "仕舞う", "みる", "見る", "もらう", "貰う", "くれる", "呉れる", "やる", "する", "為る"}

    def __init__(self, db_path=DICT_DB):
        import fugashi

        with open(db_path, "rb") as f:  # into memory now, so even the first highlight is instant
            while f.read(1 << 22):
                pass
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("PRAGMA mmap_size = 268435456")
        self.tagger = fugashi.Tagger()  # UniDic (unidic-lite): words, dictionary forms, readings

    def entries(self, form, phrases_only=False):
        rows = self.db.execute(
            "SELECT e.phrase, e.kanji, e.kana, e.senses FROM form f JOIN entry e ON e.id = f.id "
            "WHERE f.form = ? ORDER BY e.score DESC, e.id LIMIT 3", (form,)).fetchall()
        return [(json.loads(k), json.loads(r), json.loads(s)) for p, k, r, s in rows if p or not phrases_only]

    def words(self, text, limit=8):
        """[(text as written, reading, meaning, politeness)] for the words and set phrases in text."""
        tokens = [t for t in self.tagger(text) if t.surface]
        surfaces = [t.surface for t in tokens]

        def bases(t):
            """Dictionary forms: UniDic's lemma (its kanji tells する "do" from する "rub"), then as
            usually written."""
            forms = [getattr(t.feature, name, None) for name in ("lemma", "orthBase")]
            return list(dict.fromkeys(f.split("-")[0] for f in forms if f and f != "*")) or [t.surface]

        def helper(t):
            return t.feature.pos2 == "非自立可能" and bool(self.HELPERS & set(bases(t)))

        found, i, seen = [], 0, set()
        while i < len(tokens) and len(found) < limit:
            hit = None
            for j in range(min(len(tokens), i + 6), i, -1):  # the longest phrase first
                if tokens[i].feature.pos1 in self.SKIP:
                    break
                if j == i + 1 and (helper(tokens[i]) or tokens[i].feature.pos1 == "接頭辞"):
                    break  # a helper verb or お/ご on their own (させていただく as a whole is fine)
                written = "".join(surfaces[i:j])
                dictionary_forms = ["".join(surfaces[i:j - 1]) + b for b in bases(tokens[j - 1])]
                # one word: its dictionary form first (し → する); a phrase: as written first
                order = dictionary_forms + [written] if j == i + 1 else [written] + dictionary_forms
                # a phrase ending in a particle or ending (恐れ入りますが) must be a set phrase
                last = tokens[j - 1]
                ends_in_grammar = j > i + 1 and (last.feature.pos1 in self.GRAMMAR or helper(last))
                for form in dict.fromkeys(order):
                    entries = self.entries(form, phrases_only=ends_in_grammar)
                    if entries:
                        hit = (written, form, entries[0])
                        break
                if hit:
                    i = j
                    break
            if hit is None:
                i += 1
                continue
            written, form, (kanji, kana, senses) = hit
            if form in seen:
                continue
            seen.add(form)
            reading = kana[0] if kana and kana[0] != form else ""
            meaning = "; ".join(dict.fromkeys(g for s in senses[:2] for g in s["g"][:2]))
            notes = sorted({m for s in senses for m in s["m"]})
            found.append((form, hiragana(reading), meaning, notes))
        return found


# ---------------------------------------------------------------- small models on the CPU


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def model_path(repo_file):
    from huggingface_hub import hf_hub_download

    return hf_hub_download(*repo_file)


# Runs llama-server and stops it as soon as this program is gone, however it ended (even
# killed outright), so a model can never be left running in the background.
WATCHDOG = (
    '"$@" & srv=$!; trap \'kill $srv 2>/dev/null; exit 0\' TERM HUP INT; '
    'while kill -0 "$OWNER" 2>/dev/null && kill -0 $srv 2>/dev/null; do sleep 1; done; '
    'kill $srv 2>/dev/null'
)


class CPUModel:
    """A llama.cpp server for one model, with the GPU switched off (--device none)."""

    def __init__(self, gguf, threads):
        server = shutil.which("llama-server")
        if not server:
            raise RuntimeError("llama.cpp isn't installed (brew install llama.cpp)")
        self.port = free_port()
        base = [server, "-m", gguf, "--host", "127.0.0.1", "--port", str(self.port), "-ngl", "0",
                "-t", str(threads), "-c", "1024", "-np", "1"]
        env = {**os.environ, "OWNER": str(os.getpid())}
        self.proc = None
        for extra in (["--device", "none"], []):  # older llama.cpp has no --device
            self.proc = subprocess.Popen(["/bin/sh", "-c", WATCHDOG, "sh", *base, *extra], env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self._wait_ready():
                return
            self.close()
        raise RuntimeError(f"llama-server didn't start for {os.path.basename(gguf)}")

    def _wait_ready(self, timeout=60):
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.1)
        return False

    def chat(self, system, user, max_tokens, stream=False):
        body = json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                           "temperature": 0, "max_tokens": max_tokens, "stream": stream}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        if not stream:
            with resp:
                return json.load(resp)["choices"][0]["message"]["content"].strip()
        return self._stream(resp)

    @staticmethod
    def _stream(resp):
        with resp:
            for line in resp:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                delta = json.loads(line[5:])["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # the watchdog stops the server
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Lookup:
    def __init__(self, models=True, quiet=False):
        self.quiet = quiet
        if not os.path.exists(DICT_DB):  # the download at install didn't happen
            print(f"{DIM}Setting up the dictionary (once) …{RESET}", flush=True)
            build_dictionary()
        self.dictionary = Dictionary()
        self.translator = self.explainer = None
        if models:
            try:
                # Few threads each, so the CPU work the captions do (voice activity, the voice)
                # keeps room.
                self.translator = CPUModel(model_path(TRANSLATOR), threads=2)
                self.explainer = CPUModel(model_path(EXPLAINER), threads=4)
            except Exception as e:
                self.close()
                self.translator = self.explainer = None
                print(f"{YELLOW}! Dictionary only: the translation and explanation didn't start ({e}){RESET}",
                      flush=True)
        self.current = 0  # a newer highlight stops the explanation of the older one

    def close(self):
        for m in (self.translator, self.explainer):
            if m:
                m.close()

    def run(self, text):
        """Look text up; returns the timings (seconds) of each part."""
        out = (lambda *a: None) if self.quiet else print
        write = (lambda x: None) if self.quiet else sys.stdout.write
        self.current += 1
        mine, t0, times = self.current, time.monotonic(), {}
        out(f"\n{BOLD}{text}{RESET}")
        words = self.dictionary.words(text)
        for form, reading, meaning, notes in words:
            note = f" {GREEN}({', '.join(notes)}){RESET}" if notes else ""
            out(f"  {CYAN}{form}{RESET}  {DIM}{reading}{RESET}  {meaning}{note}")
        times["dictionary"] = time.monotonic() - t0
        facts = "; ".join(f"{f} = {m.split(';')[0]}" + (f" ({', '.join(n)})" if n else "") for f, _, m, n in words)
        if not self.translator:
            return times

        pieces, done = [], threading.Event()

        def explain():  # starts with the translation, on its own CPU threads
            try:
                held = ""  # hold the first words back until any stock opener can be cut
                stream = self.explainer.chat(EXPLAIN_PROMPT, f"Phrase: {text}\nDictionary: {facts}", 50, stream=True)
                for piece in stream:
                    if self.current != mine:
                        stream.close()  # hangs up, so llama-server stops writing it
                        break
                    if held is not None:
                        held += piece
                        if len(held) < 20:
                            continue
                        for opener in OPENERS:
                            if held.startswith(opener):
                                held = held[len(opener):]
                                break
                        piece, held = held[:1].upper() + held[1:], None
                    if not pieces:
                        times["explanation, first words"] = time.monotonic() - t0
                    pieces.append(piece)
                if held:
                    pieces.append(held)
            except Exception as e:
                pieces.append(f"(no explanation: {e})")
            finally:
                done.set()

        threading.Thread(target=explain, daemon=True).start()
        try:
            en = self.translator.chat("Translate to English.", text, 80).lstrip("-– ").strip()
        except Exception as e:
            en = f"(no translation: {e})"
        times["translation"] = time.monotonic() - t0
        out(f"  {YELLOW}➜ {en}{RESET}")

        shown = 0
        write(f"  {DIM}✎ ")
        while True:
            finished = done.wait(0.03)
            new = "".join(pieces[shown:])
            shown = len(pieces)
            write(new)
            sys.stdout.flush()
            if finished and shown == len(pieces):
                break
        write(f"{RESET}\n")
        times["explanation"] = time.monotonic() - t0
        return times


# ---------------------------------------------------------------- watching what you highlight


def clipboard():
    try:
        return subprocess.run(["pbpaste"], capture_output=True, timeout=2).stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def watch(lookup):
    print(f"{BOLD}Word lookup{RESET}  {DIM}highlight Japanese in the captions to see what it means. "
          f"Ctrl+C to close.{RESET}")
    last = clipboard()
    while True:
        time.sleep(0.15)
        text = clipboard()
        if text == last:
            continue
        last = text
        text = " ".join(text.split())
        if JAPANESE.search(text) and len(text) <= 80:
            lookup.run(text)


def main():
    ap = argparse.ArgumentParser(prog="call-captions lookup", description=__doc__.splitlines()[0])
    ap.add_argument("--build-dictionary", action="store_true", help="download and build the dictionary, then exit")
    ap.add_argument("--download-only", action="store_true", help="get the dictionary and CPU models, then exit")
    ap.add_argument("--no-models", action="store_true", help="dictionary only")
    ap.add_argument("--stdin", action="store_true", help="look up lines typed or piped in, instead of highlights")
    ap.add_argument("--quiet", action="store_true", help="don't print the lookups (for timing)")
    ap.add_argument("--bench", nargs="*", metavar="PHRASE", help="time lookups of these phrases, then exit")
    args = ap.parse_args()

    if args.build_dictionary or args.download_only:
        if not os.path.exists(DICT_DB):
            print("    dictionary (JMdict)", flush=True)
            build_dictionary()
        if args.download_only:
            for repo_file in (TRANSLATOR, EXPLAINER):
                print(f"    {repo_file[0]}", flush=True)
                model_path(repo_file)
        return

    lookup = Lookup(models=not args.no_models, quiet=args.quiet)
    try:
        if args.bench is not None:
            phrases = args.bench or ["伺ってもよろしいですか", "恐れ入りますが", "御社", "前職ではどういったお仕事を"]
            lookup.quiet, quiet = True, lookup.quiet
            lookup.run("もしもし")  # warm up
            lookup.quiet = quiet
            results = [(p, lookup.run(p)) for p in phrases]
            print(f"\n{BOLD}Lookup on the CPU, seconds after highlighting{RESET}")
            for p, t in results:
                print("  " + p + "  " + "  ".join(f"{k} {v:.2f}" for k, v in t.items()))
            print("BENCH_JSON " + json.dumps([t for _, t in results]))
        elif args.stdin:
            print("LOOKUP_READY", flush=True)
            for line in sys.stdin:
                if JAPANESE.search(line):
                    lookup.run(line.strip())
        else:
            watch(lookup)
    except KeyboardInterrupt:
        pass
    finally:
        lookup.close()


if __name__ == "__main__":
    main()
