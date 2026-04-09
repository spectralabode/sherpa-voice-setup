#!/usr/bin/env python3
"""
sherpa-voice TTS CLI — generate speech from text, write WAV to file or stdout.

Usage:
  tts-cli.py "Hello world"
  tts-cli.py -t "Hello world" -v kokoro -s 1 -o hello.wav
  tts-cli.py "Hello world" | aplay
  echo "Hello world" | tts-cli.py
  tts-cli.py --list-voices
"""
import argparse
import array
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as _cfg

MODELS = Path(__file__).parent / "models"
_conf  = _cfg.load()


def find_dir(pattern):
    matches = sorted(MODELS.glob(pattern), reverse=True)
    if not matches:
        raise FileNotFoundError(f"No model directory matching {pattern!r} in {MODELS}")
    return matches[0]


def find_file(directory, pattern):
    import re
    matches = sorted(
        [f for f in Path(directory).iterdir() if re.search(pattern, f.name)],
        reverse=True,
    )
    if not matches:
        raise FileNotFoundError(f"No file matching {pattern!r} in {directory}")
    return str(matches[0])


def espeak(model_dir):
    local = Path(model_dir) / "espeak-ng-data"
    return str(local) if local.is_dir() else str(MODELS / "espeak-ng-data")


def _load_piper(glob_pattern):
    import sherpa_onnx
    d = find_dir(glob_pattern)
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=find_file(d, r"\.onnx$"),
                tokens=str(d / "tokens.txt"),
                data_dir=espeak(d),
            ),
            num_threads=2, provider="cpu",
        ),
    ))


def _load_kokoro():
    import sherpa_onnx
    d = find_dir("kokoro-int8-en-*")
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=find_file(d, r"model\.int8\.onnx$"),
                tokens=str(d / "tokens.txt"),
                voices=str(d / "voices.bin"),
                data_dir=espeak(d),
            ),
            num_threads=2, provider="cpu",
        ),
    ))


def _load_kitten():
    import sherpa_onnx
    d = find_dir("kitten-nano-en-*")
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kitten=sherpa_onnx.OfflineTtsKittenModelConfig(
                model=find_file(d, r"model\.fp16\.onnx$"),
                tokens=str(d / "tokens.txt"),
                voices=str(d / "voices.bin"),
                data_dir=espeak(d),
            ),
            num_threads=2, provider="cpu",
        ),
    ))


VOICES = {
    "piper-lessac": {
        "label": "Piper Lessac (female, neutral)",
        "speakers": ["Default"],
        "load": lambda: _load_piper("vits-piper-en_US-lessac-medium*"),
    },
    "piper-amy": {
        "label": "Piper Amy (female, warm)",
        "speakers": ["Default"],
        "load": lambda: _load_piper("vits-piper-en_US-amy-medium*"),
    },
    "kokoro": {
        "label": "Kokoro (11 voices, best quality)",
        "speakers": [
            "af (default female)", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
        ],
        "load": _load_kokoro,
    },
    "kitten": {
        "label": "KittenTTS Nano (8 voices)",
        "speakers": [
            "speaker 0 (F)", "speaker 1 (F)", "speaker 2 (F)", "speaker 3 (F)",
            "speaker 4 (M)", "speaker 5 (M)", "speaker 6 (M)", "speaker 7 (M)",
        ],
        "load": _load_kitten,
    },
}


def to_wav(samples, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(
            array.array("h", [max(-32768, min(32767, int(s * 32767))) for s in samples]).tobytes()
        )
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="Offline TTS — generate WAV from text",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s "Hello world"
  %(prog)s -t "Hello world" -v kokoro -s 1 -o hello.wav
  %(prog)s "Hello world" | aplay
  echo "Hello world" | %(prog)s
  %(prog)s --list-voices""",
    )
    parser.add_argument("text", nargs="?", help="text to speak (or omit to read from stdin)")
    parser.add_argument("-t", "--text-opt", metavar="TEXT", dest="text_opt", help="text to speak")
    parser.add_argument("-v", "--voice", default=_conf["voice"],
                        choices=list(VOICES), metavar="VOICE", help=f"voice model (default: {_conf['voice']})")
    parser.add_argument("-s", "--speaker", type=int, default=_conf["speaker"], metavar="N",
                        help=f"speaker index (default: {_conf['speaker']})")
    parser.add_argument("--speed", type=float, default=_conf["speed"], metavar="X",
                        help=f"speed multiplier (default: {_conf['speed']})")
    parser.add_argument("-o", "--output", metavar="FILE", help="output WAV file (default: stdout)")
    parser.add_argument("--list-voices", action="store_true", help="list available voices and exit")
    args = parser.parse_args()

    if args.list_voices:
        for vid, v in VOICES.items():
            print(f"{vid:16}  {v['label']}")
            for i, s in enumerate(v["speakers"]):
                print(f"  speaker {i}: {s}")
        return

    # resolve text
    text = args.text_opt or args.text
    if not text:
        if sys.stdin.isatty():
            parser.error("provide text as argument, -t, or pipe via stdin")
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        parser.error("text is empty")

    # stdout mode: suppress sherpa-onnx stderr chatter so only WAV bytes flow out
    if args.output is None and not sys.stdout.isatty():
        import os
        devnull = open(os.devnull, "w")
        sys.stderr = devnull

    print(f"Loading voice: {args.voice}...", file=sys.stderr)
    tts = VOICES[args.voice]["load"]()

    print("Generating...", file=sys.stderr)
    audio = tts.generate(text, sid=args.speaker, speed=args.speed)
    wav = to_wav(audio.samples, audio.sample_rate)

    if args.output:
        Path(args.output).write_bytes(wav)
        print(f"Saved: {args.output} ({len(wav)//1024} KB)", file=sys.stderr)
    else:
        sys.stdout.buffer.write(wav)


if __name__ == "__main__":
    main()
