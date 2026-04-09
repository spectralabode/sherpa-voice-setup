#!/usr/bin/env python3
"""
sherpa-voice STT CLI — transcribe audio to text.

Usage:
  stt-cli.py audio.wav
  stt-cli.py audio.webm          # any format ffmpeg can read
  stt-cli.py -                   # read from stdin
  arecord -f S16_LE -r 16000 | stt-cli.py -
  stt-cli.py --mic               # stream from microphone (Ctrl+C to stop)
  stt-cli.py --mic --vad         # auto-stop after silence
"""
import argparse
import array
import io
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

MODELS   = Path(__file__).parent / "models"
RATE     = 16000
CHUNK    = RATE // 10   # 100 ms


# ---- model helpers -----------------------------------------------------------

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


def load_recognizer():
    import sherpa_onnx
    d = find_dir("sherpa-onnx-streaming-zipformer-*")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=find_file(d, r"encoder.*int8.*\.onnx$"),
        decoder=find_file(d, r"decoder.*int8.*\.onnx$"),
        joiner=find_file(d, r"joiner.*int8.*\.onnx$"),
        tokens=str(d / "tokens.txt"),
        num_threads=2, provider="cpu",
        decoding_method="greedy_search",
    )


def load_vad():
    import sherpa_onnx
    return sherpa_onnx.VoiceActivityDetector(
        sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=str(MODELS / "silero_vad.onnx"),
                threshold=0.5,
                min_silence_duration=1.2,  # seconds of silence before stopping
                min_speech_duration=0.25,
            ),
            sample_rate=RATE,
        ),
        buffer_size_in_seconds=30,
    )


# ---- file transcription ------------------------------------------------------

def to_pcm16_wav(data: bytes) -> bytes:
    """Convert arbitrary audio bytes to 16-bit mono 16 kHz WAV via ffmpeg if needed."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as wf:
            if wf.getframerate() == RATE and wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                return data
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
        f.write(data)
        tmp_in = f.name
    tmp_out = tmp_in + ".wav"
    try:
        ret = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in,
             "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le",
             tmp_out, "-loglevel", "quiet"],
            check=False,
        )
        if ret.returncode != 0 or not os.path.exists(tmp_out):
            raise RuntimeError("ffmpeg conversion failed")
        return open(tmp_out, "rb").read()
    finally:
        os.unlink(tmp_in)
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)


def transcribe_file(recognizer, wav_bytes: bytes) -> str:
    with wave.open(io.BytesIO(wav_bytes)) as wf:
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
    # Pass 1: single run — produces a partial result missing the start.
    # Pass 2: audio repeated in one stream — encoder is now warm, second
    #         copy decodes fully. Strip the Pass-1 prefix to get clean text.
    prev = None
    for n in range(1, 6):
        curr = _run(samples * n)
        if prev and curr and curr.startswith(prev):
            return curr[len(prev):].strip()
        prev = curr
    return prev or ""


# ---- mic streaming -----------------------------------------------------------

def _run_stream(recognizer, sample_iter, use_vad: bool, realtime: bool):
    """
    Core streaming loop shared by --mic and --sim.
    sample_iter yields lists/arrays of float32 samples of length CHUNK.
    realtime=True sleeps between chunks to mimic live audio speed.
    """
    import time

    vad      = load_vad() if use_vad else None
    st       = recognizer.create_stream()
    last_txt = ""

    def get_text():
        r = recognizer.get_result(st)
        return (r.text if hasattr(r, "text") else r).strip()

    try:
        for samples in sample_iter:
            if realtime:
                time.sleep(CHUNK / RATE)

            samples_list = samples.tolist() if hasattr(samples, "tolist") else list(samples)

            if vad is not None:
                vad.accept_waveform(samples_list)
                if not vad.is_speech_detected() and get_text():
                    break   # silence after speech — done

            st.accept_waveform(RATE, samples_list)
            while recognizer.is_ready(st):
                recognizer.decode_stream(st)

            txt = get_text()
            if txt != last_txt:
                print(f"\r\033[K{txt}", end="", flush=True)
                last_txt = txt
    except KeyboardInterrupt:
        pass

    st.input_finished()
    while recognizer.is_ready(st):
        recognizer.decode_stream(st)

    final = get_text()
    print(f"\r\033[K{final}")
    return final


def stream_mic(recognizer, use_vad: bool):
    import numpy as np
    import sounddevice as sd
    import queue

    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(indata[:, 0].copy())

    hint = "VAD active — will stop after silence" if use_vad else "Ctrl+C to stop"
    print(f"Listening... ({hint})", file=sys.stderr)

    def mic_iter():
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                            blocksize=CHUNK, callback=callback):
            while True:
                yield q.get()

    _run_stream(recognizer, mic_iter(), use_vad=use_vad, realtime=False)


def stream_sim(recognizer, wav_path: str, use_vad: bool):
    """Feed a WAV file through the same streaming path as --mic."""
    wav_bytes = to_pcm16_wav(Path(wav_path).read_bytes())
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        raw = wf.readframes(wf.getnframes())

    samples_all = array.array("h", raw)
    print(f"Simulating mic input from: {wav_path}", file=sys.stderr)

    # prepend 3 s of silence so the streaming model has time to warm up,
    # matching the behaviour a real mic gives (silence before speech starts)
    silence_chunks = (RATE * 3) // CHUNK

    def file_iter():
        for _ in range(silence_chunks):
            yield [0.0] * CHUNK
        for i in range(0, len(samples_all), CHUNK):
            chunk = samples_all[i: i + CHUNK]
            yield [s / 32768.0 for s in chunk]
        # trailing silence to flush the final frames
        for _ in range(RATE // CHUNK):
            yield [0.0] * CHUNK

    _run_stream(recognizer, file_iter(), use_vad=use_vad, realtime=True)


# ---- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline STT — transcribe audio to text",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s audio.wav
  %(prog)s audio.webm
  %(prog)s -                          # stdin
  %(prog)s --mic                      # microphone, Ctrl+C to stop
  %(prog)s --mic --vad                # microphone, auto-stop on silence
  %(prog)s --sim audio.wav            # simulate mic with a file (no mic needed)
  %(prog)s --sim audio.wav --vad      # same, auto-stop on silence""",
    )
    parser.add_argument("input", nargs="?", help="audio file, or - for stdin (omit for --mic/--sim)")
    parser.add_argument("--mic", "-m", action="store_true", help="stream from microphone")
    parser.add_argument("--sim", metavar="FILE", help="simulate mic with a WAV file (for testing)")
    parser.add_argument("--vad", action="store_true", help="with --mic/--sim: auto-stop after silence")
    args = parser.parse_args()

    if not args.mic and not args.sim and not args.input:
        parser.error("provide an input file, -, --mic, or --sim FILE")
    if args.vad and not (args.mic or args.sim):
        parser.error("--vad requires --mic or --sim")

    if not sys.stdout.isatty():
        sys.stderr = open(os.devnull, "w")

    print("Loading STT model...", file=sys.stderr)
    recognizer = load_recognizer()

    if args.mic:
        stream_mic(recognizer, use_vad=args.vad)
    elif args.sim:
        stream_sim(recognizer, args.sim, use_vad=args.vad)
    else:
        print("Transcribing...", file=sys.stderr)
        data = sys.stdin.buffer.read() if args.input == "-" else Path(args.input).read_bytes()
        wav  = to_pcm16_wav(data)
        print(transcribe_file(recognizer, wav))


if __name__ == "__main__":
    main()
