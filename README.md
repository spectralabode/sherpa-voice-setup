# sherpa-voice

Barebones Offline text-to-speech and speech-to-text on Linux — no cloud, no Docker, CPU-only.
Powered by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).

What i use it for ?

- simple command line stt and tts
- converting web pages to podcasts (uvicorn + ui*.html)
- generating voiceover for ipod managed with ipod-shuffle-4g


---

## Files

| File | Purpose |
|---|---|
| `install.sh` | Installer — downloads models, creates Python venv, copies app files |
| `app.py` | FastAPI web server (TTS + STT via browser) |
| `ui.html` | Web UI served by `app.py` |
| `run.sh` | Launcher for the web server |
| `tts` | Shell wrapper — runs `tts-cli.py` with the correct venv |
| `tts-cli.py` | CLI text-to-speech |
| `stt` | Shell wrapper — runs `stt-cli.py` with the correct venv |
| `stt-cli.py` | CLI speech-to-text |
| `configure` | Shell wrapper — runs `configure.py` with the correct venv |
| `configure.py` | Interactive configuration wizard |
| `config.py` | Shared config reader/writer (used by CLI tools) |
| `README.md` | This file |

---

## Requirements

- Python 3.9+
- `curl`
- `ffmpeg` (auto-installed if missing and `apt`/`dnf`/`pacman`/`brew` is available)
- `libportaudio2` (auto-installed; needed for microphone input)
- ~800 MB disk space for models
- Internet access during installation

---

## Installation


```bash
git clone https://github.com/spectralabode/sherpa-voice-setup.git
cd sherpa-voice-setup
bash install.sh
```

SHERPA_INSTALL_DIR defaults to ~/sherpa-voice
if you want to use a different target directory 

```bash
   export SHERPA_INSTALL_DIR=YOUR-DESTINATION-DIRECTORY
```
or use the below listed options, rest of this readme assumes default install dir's.
if your have installed it elsewhere please replace prefixes with your target install paths.

Options:

```bash
bash install.sh -i /opt/sherpa-voice          # custom install directory
bash install.sh --install-dir=/opt/sherpa-voice
bash install.sh -i ~/sherpa-voice --port=12345

SHERPA_INSTALL_DIR=~/sherpa-voice bash install.sh   # via env var
GITHUB_TOKEN=ghp_xxx bash install.sh                # avoid API rate limits
```

The installer:
1. Checks / auto-installs `ffmpeg` and `libportaudio2`
2. Creates a Python venv at `<install-dir>/venv`
3. Installs Python packages (`sherpa-onnx`, `fastapi`, `uvicorn`, `sounddevice`, `numpy`, …)
4. Downloads all models (~800 MB) to `<install-dir>/models`
5. Copies the app files into `<install-dir>`

Default install directory: `~/sherpa-voice`

---

## Web UI

Start the server:

```bash
bash ~/sherpa-voice/run.sh
```

Options:

```bash
bash ~/sherpa-voice/run.sh -p 12345
bash ~/sherpa-voice/run.sh --port=12345
SHERPA_PORT=12345 bash ~/sherpa-voice/run.sh
```

Then open **http://localhost:45678** in your browser.

The UI provides:
- **TTS**: choose voice + speaker, type text, click Speak
- **STT**: record from microphone or upload a WAV/WebM file

---

## Voice models

| ID | Description |
|---|---|
| `piper-lessac` | Piper Lessac — female, neutral (1 speaker) |
| `piper-amy` | Piper Amy — female, warm, INT8 (1 speaker) |
| `kokoro` | Kokoro INT8 — best quality (11 speakers) |
| `kitten` | KittenTTS Nano FP16 — fast (8 speakers) |

Kokoro speakers: `af` (default F), `af_bella`, `af_nicole`, `af_sarah`, `af_sky`,
`am_adam`, `am_michael`, `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis`

KittenTTS speakers: 0–3 (female), 4–7 (male)

---

## TTS CLI

```bash
~/sherpa-voice/tts "Hello world"
~/sherpa-voice/tts "Hello world" -o hello.wav
~/sherpa-voice/tts "Hello world" | aplay
echo "Hello world" | ~/sherpa-voice/tts | aplay
```

Options:

```
-t, --text TEXT       text to speak
-v, --voice VOICE     voice model (default from config, fallback: piper-lessac)
-s, --speaker N       speaker index (default from config, fallback: 0)
    --speed X         speed multiplier, e.g. 0.8 / 1.0 / 1.5 (default: 1.0)
-o, --output FILE     save to WAV file instead of stdout
    --list-voices     list available voices and speakers
```

Examples:

```bash
~/sherpa-voice/tts --list-voices

~/sherpa-voice/tts -v kokoro -s 5 "Hello, I am Adam." -o adam.wav

~/sherpa-voice/tts --speed 1.3 "Fast talker" | aplay
```

---

## STT CLI

```bash
~/sherpa-voice/stt audio.wav
~/sherpa-voice/stt audio.webm       # any format ffmpeg understands
~/sherpa-voice/stt -                # read from stdin
```

**Microphone (live streaming):**

```bash
~/sherpa-voice/stt --mic            # Ctrl+C to stop
~/sherpa-voice/stt --mic --vad      # auto-stop after silence
```

**Simulate mic with a file** (useful when no mic is connected):

```bash
~/sherpa-voice/stt --sim audio.wav
~/sherpa-voice/stt --sim audio.wav --vad
```

In streaming modes (`--mic` / `--sim`) the transcript updates in place as words
are recognised.

**Pipe TTS into STT:**

```bash
~/sherpa-voice/tts "Hello world" | ~/sherpa-voice/stt -
```

---

## Configuration (CLI defaults)

The CLI tools read default voice/speaker/speed from
`~/.config/sherpa-voice/config.json` (respects `$XDG_CONFIG_HOME`).

Interactive wizard:

```bash
~/sherpa-voice/configure              # interactive setup
~/sherpa-voice/configure --show       # print current settings
~/sherpa-voice/configure --reset      # reset to built-in defaults
```

Config file format (`~/.config/sherpa-voice/config.json`):

```json
{
  "voice": "kokoro",
  "speaker": 0,
  "speed": 1.0,
  "port": 45678
}
```

CLI flags always override saved config. The web UI manages its own state
dynamically and does not use this file.

---

## STT model note

The STT model is a **streaming** zipformer (20M, INT8). It needs a short silence at the start to warm up. 
File transcription (`stt-cli.py audio.wav`) handles this automagically. In `--sim`
mode the silence is inserted prior recording.


## Thanks 
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
[kokoro-tts](https://github.com/nazdridoy/kokoro-tts)
[GNU](https://www.gnu.org/home.en.html)
[ascii-banner](https://manytools.org/hacker-tools/ascii-banner/)
