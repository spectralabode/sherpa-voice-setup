#!/usr/bin/env python3
"""
sherpa-voice reader -- TTS from a URL or text file.

Usage:
  read-cli.py --url https://example.com/article
  read-cli.py --url URL -o article.wav
  read-cli.py --url URL | aplay
  read-cli.py article.txt
  read-cli.py article.txt -v kokoro -s 1 -o out.wav
  read-cli.py --url URL --show-text      # extract only, no TTS
"""
import argparse
import array
import io
import os
import re
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

MODELS = Path(__file__).parent / "models"


# ---- model helpers -----------------------------------------------------------

def find_dir(pattern):
    matches = sorted(MODELS.glob(pattern), reverse=True)
    if not matches:
        raise FileNotFoundError(f"No model directory matching {pattern!r} in {MODELS}")
    return matches[0]


def find_file(directory, pattern):
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


# ---- TTS loaders -------------------------------------------------------------

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
    "piper-lessac": lambda: _load_piper("vits-piper-en_US-lessac-medium*"),
    "piper-amy":    lambda: _load_piper("vits-piper-en_US-amy-medium*"),
    "kokoro":       _load_kokoro,
    "kitten":       _load_kitten,
}


# ---- article extraction ------------------------------------------------------

def _html_to_text(html: str) -> str:
    """Convert an HTML fragment to clean plain text (stdlib only)."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "br", "tr"}

        def __init__(self):
            super().__init__()
            self._buf = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip += 1
            if tag in self.BLOCK:
                self._buf.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._skip = max(0, self._skip - 1)
            if tag in self.BLOCK:
                self._buf.append("\n")

        def handle_data(self, data):
            if self._skip == 0:
                self._buf.append(data)

        def result(self):
            t = "".join(self._buf)
            t = re.sub(r"[ \t]+", " ", t)
            t = re.sub(r"\n{3,}", "\n\n", t)
            return t.strip()

    p = _P()
    p.feed(html)
    return p.result()


def extract_url(url: str) -> tuple:
    """Return (title, text) from a URL using readability + trafilatura fallback."""
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Could not fetch URL: {url}")

    title = ""
    text = ""

    # pass 1: Mozilla Reader View algorithm
    try:
        from readability import Document
        doc = Document(downloaded)
        title = doc.short_title() or doc.title()
        text = _html_to_text(doc.summary(html_partial=True))
    except ImportError:
        pass
    except Exception:
        pass

    # pass 2: trafilatura precision mode (fallback)
    if not text or len(text.split()) < 80:
        t2 = trafilatura.extract(
            downloaded,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
        )
        if t2 and (not text or len(t2.split()) > len(text.split())):
            text = t2
        if not title:
            meta = trafilatura.extract_metadata(downloaded)
            title = meta.title if meta and meta.title else ""

    if not text:
        raise RuntimeError("Could not extract article text from URL")

    return title, re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- audio helpers -----------------------------------------------------------

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


def chunk_text(text: str, max_chars: int = 800) -> list:
    """Split text into TTS-friendly chunks at paragraph/sentence boundaries."""
    chunks = []
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    current = []
    current_len = 0

    for para in paragraphs:
        if len(para) > max_chars:
            # paragraph too long — split at sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                if current_len + len(sent) > max_chars and current:
                    chunks.append(" ".join(current))
                    current = []
                    current_len = 0
                current.append(sent)
                current_len += len(sent) + 1
        else:
            if current_len + len(para) > max_chars and current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            current.append(para)
            current_len += len(para) + 1

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if c.strip()]


# ---- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline TTS — read a webpage or text file aloud",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s --url https://example.com/article
  %(prog)s --url URL -o article.wav
  %(prog)s --url URL | aplay
  %(prog)s --url URL --show-text          # extract text only, no TTS
  %(prog)s article.txt
  %(prog)s article.txt -v kokoro -s 1
  %(prog)s - < article.txt | aplay        # stdin""",
    )
    parser.add_argument("file", nargs="?",
                        help="text file to speak (use - for stdin); omit when using --url")
    parser.add_argument("--url", "-u", metavar="URL",
                        help="fetch and extract article from URL")
    parser.add_argument("--voice", "-v", metavar="VOICE",
                        help=f"voice model (choices: {', '.join(VOICES)})")
    parser.add_argument("--speaker", "-s", type=int, metavar="N",
                        help="speaker index (default from config, fallback: 0)")
    parser.add_argument("--speed", type=float, metavar="X", default=None,
                        help="speed multiplier, e.g. 0.8 / 1.0 / 1.5 (default: 1.0)")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="save to WAV file instead of stdout")
    parser.add_argument("--show-text", action="store_true",
                        help="print extracted text to stdout and exit (no TTS)")
    parser.add_argument("--list-voices", action="store_true",
                        help="list available voice IDs and exit")
    args = parser.parse_args()

    if args.list_voices:
        for k in VOICES:
            print(k)
        return

    if not args.url and not args.file:
        parser.error("provide --url URL or a text file path (or - for stdin)")

    # suppress progress/info when piping audio to stdout
    quiet = not sys.stdout.isatty() and not args.output and not args.show_text

    def info(msg):
        if not quiet:
            print(msg, file=sys.stderr)

    # ---- load config defaults ------------------------------------------------
    try:
        from config import load as load_cfg
        cfg = load_cfg()
    except Exception:
        cfg = {}

    voice_id = args.voice   or cfg.get("voice",   "piper-lessac")
    speaker  = args.speaker if args.speaker is not None else cfg.get("speaker", 0)
    speed    = args.speed   if args.speed   is not None else cfg.get("speed",   1.0)

    # ---- get text ------------------------------------------------------------
    title = ""
    if args.url:
        info(f"Fetching: {args.url}")
        title, text = extract_url(args.url)
        if title:
            info(f"Title   : {title}")
    else:
        src = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8", errors="replace")
        text = src.read()
        if args.file != "-":
            src.close()

    text = text.strip()
    if not text:
        print("Error: no text to speak.", file=sys.stderr)
        sys.exit(1)

    info(f"Words   : {len(text.split())}")

    if args.show_text:
        if title:
            print(f"# {title}\n")
        print(text)
        return

    # ---- validate voice ------------------------------------------------------
    if voice_id not in VOICES:
        print(f"Error: unknown voice {voice_id!r}. Available: {', '.join(VOICES)}", file=sys.stderr)
        sys.exit(1)

    # ---- load TTS model ------------------------------------------------------
    info(f"Voice   : {voice_id} (speaker {speaker}, speed {speed})")
    info("Loading TTS model...")
    tts = VOICES[voice_id]()

    # ---- chunk and synthesise ------------------------------------------------
    chunks = chunk_text(text)
    if title:
        chunks = [title + "."] + chunks  # speak the title first

    info(f"Chunks  : {len(chunks)}")

    all_samples = []
    sample_rate = None

    for i, chunk in enumerate(chunks, 1):
        preview = chunk[:70] + ("…" if len(chunk) > 70 else "")
        info(f"  [{i:3d}/{len(chunks)}] {preview}")
        audio = tts.generate(chunk, sid=speaker, speed=speed)
        all_samples.extend(audio.samples)
        sample_rate = audio.sample_rate

    wav = to_wav(all_samples, sample_rate)

    # ---- output --------------------------------------------------------------
    if args.output:
        Path(args.output).write_bytes(wav)
        info(f"Saved   : {args.output} ({len(wav) // 1024} KB)")
    else:
        if sys.stdout.isatty():
            print("Tip: pipe to aplay, or use -o file.wav to save.", file=sys.stderr)
        sys.stdout.buffer.write(wav)


if __name__ == "__main__":
    main()
