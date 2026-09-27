# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = [
#     "kokoro-onnx>=0.4,<1",
#     "numpy>=2",
# ]
# ///
"""claude-speak TTS server: keeps Kokoro loaded and plays speech on request.

Listens for HTTP on a unix socket in the data dir. A new /speak interrupts
whatever is playing. Exits by itself after a period with nothing to do.

  GET  /health   model, player and voices
  POST /speak    {"text", "voice", "speed"}; returns at once, plays in the background
  POST /warm     play silence to wake the output device before speech is ready
  POST /synth    same body; returns the whole utterance as a WAV
  POST /stop     stop playback
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODELS = {
    "fp32": "kokoro-v1.0.onnx",       # ~310 MB, best quality
    "fp16": "kokoro-v1.0.fp16.onnx",  # ~170 MB
    "int8": "kokoro-v1.0.int8.onnx",  # ~90 MB, fastest on slow CPUs
}
VOICES_FILE = "voices-v1.0.bin"

# Kokoro voice names start with a language code.
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
         "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}

# Start speaking after the first chunk instead of waiting for the whole text.
CHUNK_CHARS = 250

# How long the output must be playing before speech starts, so the first words
# aren't lost while an idle device (Bluetooth especially) wakes up. Time already
# spent playing, e.g. silence from /warm while a summary is written, counts.
LEAD_IN = float(os.environ.get("CLAUDE_SPEAK_LEAD_IN", 2.0))
MIN_LEAD_IN = 0.25

# Longest /warm keeps the output awake while it waits for speech.
WARM_SECS = 20

# Output that went quiet less than this long ago is still awake. PipeWire
# suspends idle outputs after 5 seconds.
STAY_AWAKE = 3.0


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def download(name: str, dest_dir: Path) -> Path:
    dest = dest_dir / name
    if dest.exists():
        return dest
    log(f"downloading {name} ...")
    part = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(f"{RELEASE}/{name}") as resp, open(part, "wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)
    part.rename(dest)
    log(f"downloaded {name} ({dest.stat().st_size // (1 << 20)} MB)")
    return dest


def find_player() -> list[str] | None:
    """A command that plays a WAV file given as its last argument."""
    candidates = [
        ["afplay"],                                       # macOS
        ["pw-play"],                                      # PipeWire
        ["paplay"],                                       # PulseAudio
        ["aplay", "-q"],                                  # ALSA
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ]
    override = os.environ.get("CLAUDE_SPEAK_PLAYER")
    if override:
        candidates.insert(0, override.split())
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


def split_chunks(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?;:])\s+|\n+", text.strip())
    chunks, current = [], ""
    for sentence in filter(None, (s.strip() for s in sentences)):
        if current and len(current) + len(sentence) > CHUNK_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Speaker:
    """Synthesizes and plays one utterance at a time; a new one cancels the old."""

    def __init__(self, kokoro: Kokoro, player: list[str] | None, silence: Path):
        self.kokoro = kokoro
        self.player = player
        self.silence = silence
        self.voices = set(kokoro.get_voices())
        self.synth_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.generation = 0
        self.proc: subprocess.Popen | None = None
        self.warm_proc: subprocess.Popen | None = None
        self.busy_until = 0.0
        # Output-awake tracking, guarded by state_lock.
        self.playing = 0
        self.awake_since = 0.0
        self.quiet_since = 0.0

    def resolve_voice(self, voice: str) -> str:
        return voice if voice in self.voices else "af_heart"

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        voice = self.resolve_voice(voice)
        with self.synth_lock:
            return self.kokoro.create(text, voice=voice, speed=speed,
                                      lang=LANGS.get(voice[:1], "en-us"))

    def stop(self, keep_warm: bool = False) -> None:
        with self.state_lock:
            self.generation += 1
            self.busy_until = time.time()
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
            if not keep_warm:
                self._end_warm()

    def warm(self) -> None:
        with self.state_lock:
            if not self.player or any(p and p.poll() is None for p in (self.proc, self.warm_proc)):
                return
            proc = self.warm_proc = self._spawn(self.silence)
        threading.Thread(target=self._reap, args=(proc,), daemon=True).start()

    def _end_warm(self) -> None:
        if self.warm_proc and self.warm_proc.poll() is None:
            self.warm_proc.terminate()

    def _spawn(self, path: Path | str) -> subprocess.Popen:
        """Start the player; call with state_lock held."""
        now = time.time()
        if self.playing == 0 and now - self.quiet_since > STAY_AWAKE:
            self.awake_since = now
        self.playing += 1
        return subprocess.Popen(self.player + [str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _reap(self, proc: subprocess.Popen) -> None:
        proc.wait()
        with self.state_lock:
            self.playing -= 1
            if self.playing == 0:
                self.quiet_since = time.time()

    def lead_in(self) -> float:
        """Silence to put before speech so the output has been awake for LEAD_IN."""
        with self.state_lock:
            now = time.time()
            awake = self.playing > 0 or now - self.quiet_since <= STAY_AWAKE
            woke = now - self.awake_since if awake else 0.0
        return max(MIN_LEAD_IN, LEAD_IN - woke)

    def speak(self, text: str, voice: str, speed: float) -> None:
        self.stop(keep_warm=True)
        with self.state_lock:
            gen = self.generation
        self.busy_until = time.time() + 3600
        threading.Thread(target=self._run, args=(gen, text, voice, speed), daemon=True).start()

    def _current(self, gen: int) -> bool:
        return gen == self.generation

    def _run(self, gen: int, text: str, voice: str, speed: float) -> None:
        if not self.player:
            log("no audio player found (tried afplay, pw-play, paplay, aplay, ffplay)")
            return
        clips: queue.Queue = queue.Queue(maxsize=2)

        def produce():
            try:
                for i, chunk in enumerate(split_chunks(text)):
                    if not self._current(gen):
                        break
                    samples, rate = self.synth(chunk, voice, speed)
                    if i == 0:
                        lead = np.zeros(int(self.lead_in() * rate), samples.dtype)
                        samples = np.concatenate([lead, samples])
                    fd, path = tempfile.mkstemp(prefix="claude-speak-", suffix=".wav")
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(wav_bytes(samples, rate))
                    clips.put(path)
            except Exception as exc:
                log(f"synthesis failed: {exc}")
            finally:
                clips.put(None)

        threading.Thread(target=produce, daemon=True).start()
        while (path := clips.get()) is not None:
            try:
                with self.state_lock:
                    if not self._current(gen):
                        continue
                    self._end_warm()
                    proc = self.proc = self._spawn(path)
                self._reap(proc)
            finally:
                os.unlink(path)
        if self._current(gen):
            self.busy_until = time.time()


class Handler(BaseHTTPRequestHandler):
    server: "Server"

    def log_message(self, *_):  # unix sockets have no client address to log
        pass

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        self.server.touch()
        if self.path == "/health":
            speaker = self.server.speaker
            self._json(200, {"ok": True, "model": self.server.model_name,
                             "player": " ".join(speaker.player or ["none"]),
                             "voices": sorted(speaker.voices)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        self.server.touch()
        speaker = self.server.speaker
        try:
            body = self._body()
            text = str(body.get("text", "")).strip()
            voice = str(body.get("voice") or "af_heart")
            speed = min(2.0, max(0.5, float(body.get("speed") or 1.0)))
            if self.path == "/stop":
                speaker.stop()
                self._json(200, {"ok": True})
            elif self.path == "/warm":
                speaker.warm()
                self._json(202, {"ok": True})
            elif self.path == "/speak" and text:
                log(f"speak [{voice} x{speed}]: {text[:100]!r}{'...' if len(text) > 100 else ''}")
                speaker.speak(text, voice, speed)
                self._json(202, {"ok": True})
            elif self.path == "/synth" and text:
                samples, rate = speaker.synth(text, voice, speed)
                payload = wav_bytes(samples, rate)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self._json(400, {"error": "expected /speak or /synth with text, /warm or /stop"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, speaker: Speaker, model_name: str):
        self.speaker = speaker
        self.model_name = model_name
        self.last_used = time.time()
        super().__init__(path, Handler)

    def touch(self) -> None:
        self.last_used = time.time()


def socket_in_use(path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(path))
        return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--model", default=os.environ.get("CLAUDE_SPEAK_MODEL", "fp32"), choices=MODELS)
    parser.add_argument("--idle-timeout", type=float,
                        default=float(os.environ.get("CLAUDE_SPEAK_IDLE_TIMEOUT", 1800)),
                        help="seconds without requests before the server exits")
    args = parser.parse_args()

    data = Path(args.data_dir).expanduser()
    sock = Path(args.socket)
    if socket_in_use(sock):
        log("already running")
        return
    sock.unlink(missing_ok=True)

    models = data / "models"
    models.mkdir(parents=True, exist_ok=True)
    model_file = download(MODELS[args.model], models)
    voices_file = download(VOICES_FILE, models)

    started = time.time()
    kokoro = Kokoro(str(model_file), str(voices_file))
    silence = data / "silence.wav"
    silence.write_bytes(wav_bytes(np.zeros(WARM_SECS * 24000, np.float32), 24000))
    speaker = Speaker(kokoro, find_player(), silence)
    speaker.synth("Ready.", "af_heart", 1.0)  # warm up before accepting requests
    log(f"loaded {MODELS[args.model]} in {time.time() - started:.1f}s; "
        f"player: {' '.join(speaker.player or ['none'])}")

    server = Server(str(sock), speaker, MODELS[args.model])

    def idle_watch():
        while True:
            time.sleep(15)
            now = time.time()
            if now - server.last_used > args.idle_timeout and now > speaker.busy_until:
                log("idle; exiting")
                server.shutdown()
                return

    threading.Thread(target=idle_watch, daemon=True).start()
    log(f"listening on {sock}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        sock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
