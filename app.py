#!/usr/bin/env python3
"""sherpa-voice -- offline TTS / STT web UI"""
import io
import wave
import array
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import uvicorn
import sherpa_onnx

PORT   = int(os.environ.get("SHERPA_PORT", "45678"))
MODELS = Path(__file__).parent / "models"


# ---------------------------------------------------------------------------
# Model path discovery -- finds dirs by glob, files by pattern within them
# ---------------------------------------------------------------------------

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
    # prefer espeak-ng-data bundled inside model dir, fall back to shared one
    local = Path(model_dir) / "espeak-ng-data"
    return str(local) if local.is_dir() else str(MODELS / "espeak-ng-data")


# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------

def _load_piper(glob_pattern):
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

_cache: dict = {}

def get_tts(voice_id: str):
    if voice_id not in VOICES:
        raise HTTPException(400, f"Unknown voice: {voice_id}")
    if voice_id not in _cache:
        print(f"Loading TTS: {voice_id}...", flush=True)
        _cache[voice_id] = VOICES[voice_id]["load"]()
    return _cache[voice_id]


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

def _load_stt():
    d = find_dir("sherpa-onnx-streaming-zipformer-*")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=find_file(d, r"encoder.*int8.*\.onnx$"),
        decoder=find_file(d, r"decoder.*int8.*\.onnx$"),
        joiner=find_file(d, r"joiner.*int8.*\.onnx$"),
        tokens=str(d / "tokens.txt"),
        num_threads=2, provider="cpu",
        decoding_method="greedy_search",
    )

print("Loading STT...", flush=True)
recognizer = _load_stt()
print("STT ready.", flush=True)
get_tts("piper-lessac")


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

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


def transcribe(data: bytes) -> str:
    buf = io.BytesIO(data)
    with wave.open(buf) as wf:
        rate = wf.getframerate()
        raw  = wf.readframes(wf.getnframes())
    samples = [s / 32768.0 for s in array.array("h", raw)]
    post    = [0.0] * int(rate * 1.0)

    def _run(audio):
        st = recognizer.create_stream()
        chunk = rate // 10
        for s in [audio, post]:
            for i in range(0, len(s), chunk):
                st.accept_waveform(rate, s[i: i + chunk])
                while recognizer.is_ready(st):
                    recognizer.decode_stream(st)
        st.input_finished()
        while recognizer.is_ready(st):
            recognizer.decode_stream(st)
        r = recognizer.get_result(st)
        return (r.text if hasattr(r, "text") else r).strip()

    # The streaming model needs right-context to decode the first frames.
    # Repeat the audio until two consecutive results satisfy:
    #   result(n+1).startswith(result(n)) and result(n) is non-empty
    # At that point result(n) is the noisy prefix and the suffix is clean text.
    prev = None
    for n in range(1, 6):
        curr = _run(samples * n)
        if prev and curr and curr.startswith(prev):
            return curr[len(prev):].strip()
        prev = curr
    return prev or ""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI()


@app.get("/voices")
async def list_voices():
    return {k: {"label": v["label"], "speakers": v["speakers"]} for k, v in VOICES.items()}


@app.post("/tts")
async def tts(text: str = Form(...), voice: str = Form("piper-lessac"), speaker: int = Form(0)):
    if not text.strip():
        raise HTTPException(400, "text is empty")
    audio = get_tts(voice).generate(text.strip(), sid=speaker, speed=1.0)
    return StreamingResponse(
        io.BytesIO(to_wav(audio.samples, audio.sample_rate)),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=tts.wav"},
    )


@app.post("/stt")
async def stt(file: UploadFile = File(...)):
    data = await file.read()
    if data[:4] != b"RIFF":
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp_in = f.name
        tmp_out = tmp_in + ".wav"
        import subprocess
        ret = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             tmp_out, "-loglevel", "quiet"],
            check=False,
        )
        os.unlink(tmp_in)
        if ret.returncode != 0 or not os.path.exists(tmp_out):
            raise HTTPException(400, "Could not decode audio")
        data = open(tmp_out, "rb").read()
        os.unlink(tmp_out)
    return {"transcript": transcribe(data)}


def _html_to_text(html: str) -> str:
    """Convert an HTML fragment to clean plain text (no extra deps)."""
    import re
    from html.parser import HTMLParser

    class _P(HTMLParser):
        BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "br", "tr"}

        def __init__(self):
            super().__init__()
            self._buf: list = []
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


@app.post("/extract")
async def extract(url: str = Form(...)):
    try:
        import re
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise HTTPException(400, "Could not fetch URL")

        title = ""
        text = ""

        # ---- pass 1: readability-lxml (Mozilla Reader View algorithm) --------
        try:
            from readability import Document
            doc = Document(downloaded)
            title = doc.short_title() or doc.title()
            text = _html_to_text(doc.summary(html_partial=True))
        except ImportError:
            pass  # readability-lxml not installed, fall through
        except Exception:
            pass

        # ---- pass 2: trafilatura precision mode (fallback) -------------------
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
            raise HTTPException(422, "Could not extract article text from URL")

        return {"title": title, "text": re.sub(r"\n{3,}", "\n\n", text).strip()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Extraction error: {e}")


@app.get("/", response_class=HTMLResponse)
async def index():
    enhanced = Path(__file__).parent / "ui-enhanced.html"
    ui = Path(__file__).parent / "ui.html"
    return (enhanced if enhanced.exists() else ui).read_text()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
