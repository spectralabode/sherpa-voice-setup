#!/usr/bin/env python3
"""
sherpa-voice interactive configurator.
Saves defaults to ~/.config/sherpa-voice/config.json (XDG_CONFIG_HOME respected).

Usage:
  configure.py            — interactive wizard
  configure.py --show     — print current config and exit
  configure.py --reset    — reset to built-in defaults
"""
import argparse
import sys
from pathlib import Path

# Resolve config relative to this file so it works from any directory
sys.path.insert(0, str(Path(__file__).parent))
import config as cfg

VOICES = {
    "piper-lessac": {
        "label": "Piper Lessac (female, neutral)",
        "speakers": ["Default"],
    },
    "piper-amy": {
        "label": "Piper Amy (female, warm)",
        "speakers": ["Default"],
    },
    "kokoro": {
        "label": "Kokoro (11 voices, best quality)",
        "speakers": [
            "af (default female)", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
        ],
    },
    "kitten": {
        "label": "KittenTTS Nano (8 voices)",
        "speakers": [
            "speaker 0 (F)", "speaker 1 (F)", "speaker 2 (F)", "speaker 3 (F)",
            "speaker 4 (M)", "speaker 5 (M)", "speaker 6 (M)", "speaker 7 (M)",
        ],
    },
}

# ---- helpers -----------------------------------------------------------------

R  = "\033[0;31m"
G  = "\033[0;32m"
Y  = "\033[1;33m"
B  = "\033[1;34m"
C  = "\033[0;36m"
DIM = "\033[2m"
NC = "\033[0m"

def heading(text):  print(f"\n{B}── {text} {NC}")
def ok(text):       print(f"  {G}✓{NC}  {text}")
def hint(text):     print(f"  {DIM}{text}{NC}")


def pick(prompt, options, current=None):
    """Display a numbered menu, return chosen value."""
    print()
    for i, (val, label) in enumerate(options, 1):
        marker = f"{Y}*{NC}" if val == current else " "
        print(f"  {marker} {C}{i:2}{NC}  {label}")
    print()
    while True:
        raw = input(f"  {prompt} [current: {C}{current}{NC}]: ").strip()
        if raw == "":
            return current          # keep existing
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        except ValueError:
            pass
        # also accept typing the value directly
        vals = [v for v, _ in options]
        if raw in vals:
            return raw
        print(f"  {R}Invalid — enter a number 1–{len(options)}{NC}")


def ask_int(prompt, current, lo=None, hi=None):
    while True:
        raw = input(f"  {prompt} [current: {C}{current}{NC}]: ").strip()
        if raw == "":
            return current
        try:
            val = int(raw)
            if (lo is None or val >= lo) and (hi is None or val <= hi):
                return val
        except ValueError:
            pass
        range_hint = f"{lo}–{hi}" if lo is not None and hi is not None else "integer"
        print(f"  {R}Enter a valid {range_hint}{NC}")


def ask_float(prompt, current, lo=None, hi=None):
    while True:
        raw = input(f"  {prompt} [current: {C}{current}{NC}]: ").strip()
        if raw == "":
            return current
        try:
            val = float(raw)
            if (lo is None or val >= lo) and (hi is None or val <= hi):
                return val
        except ValueError:
            pass
        print(f"  {R}Enter a valid number{NC}")


def print_config(c):
    print(f"\n  Config file : {C}{cfg.CONFIG_PATH}{NC}")
    for k, v in c.items():
        default_mark = f"  {DIM}(default){NC}" if v == cfg.DEFAULTS.get(k) else ""
        print(f"  {k:10} : {C}{v}{NC}{default_mark}")


# ---- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="sherpa-voice configurator")
    parser.add_argument("--show",  action="store_true", help="print current config and exit")
    parser.add_argument("--reset", action="store_true", help="reset to defaults")
    args = parser.parse_args()

    current = cfg.load()

    if args.show:
        print_config(current)
        return

    if args.reset:
        cfg.save(dict(cfg.DEFAULTS))
        ok(f"Reset to defaults → {cfg.CONFIG_PATH}")
        return

    # ---- wizard --------------------------------------------------------------
    print(f"\n{B}  sherpa-voice configurator{NC}")
    hint(f"Press Enter to keep the current value.")

    new = dict(current)

    # Voice
    heading("Voice model")
    voice_opts = [(vid, f"{vid:16}  {v['label']}") for vid, v in VOICES.items()]
    new["voice"] = pick("Select voice", voice_opts, current=new["voice"])

    # Speaker
    heading("Speaker")
    speakers = VOICES[new["voice"]]["speakers"]
    if len(speakers) == 1:
        new["speaker"] = 0
        hint("Only one speaker for this voice — set to 0.")
    else:
        spk_opts = [(i, f"{i}  {s}") for i, s in enumerate(speakers)]
        new["speaker"] = pick("Select speaker", spk_opts, current=new["speaker"])

    # Speed
    heading("Speech speed")
    hint("1.0 = normal, 0.5 = slow, 2.0 = fast")
    new["speed"] = ask_float("Speed", new["speed"], lo=0.1, hi=4.0)

    # Port
    heading("Web UI port")
    hint("Port the web server listens on (used by run.sh / app.py)")
    new["port"] = ask_int("Port", new["port"], lo=1024, hi=65535)

    # Confirm
    print()
    heading("Summary")
    for k, v in new.items():
        changed = v != current.get(k)
        tag = f"  {Y}(changed){NC}" if changed else ""
        print(f"  {k:10} : {C}{v}{NC}{tag}")
    print()
    confirm = input("  Save? [Y/n]: ").strip().lower()
    if confirm in ("", "y", "yes"):
        cfg.save(new)
        ok(f"Saved → {cfg.CONFIG_PATH}")
    else:
        print(f"  {DIM}Aborted — no changes saved.{NC}")


if __name__ == "__main__":
    main()
