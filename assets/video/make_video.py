# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=2",
#     "playwright>=1.45",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Renders the claude-speak promo video to assets/video/claude-speak.mp4.

  uv run --script assets/video/make_video.py                      # full render
  uv run --script assets/video/make_video.py --stills 2,10.5,20   # preview frames only

Every line of speech is real Kokoro output from bin/claude-speak, and the
scene timing, captions and waveforms are derived from that audio. Frames come
from video.html (a pure function of time) rendered in headless Chrome.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
BUILD = HERE / "build"   # cached speech, timeline and preview stills
RATE = 24000
FPS = 30
W, H = 1920, 1080

DEMO_LINE = "I fixed the flaky auth test. It was a race in token refresh, and all tests pass."
END_LINE = "Stop babysitting the terminal."
CLIPS = {
    "demo": ("af_heart", DEMO_LINE),
    "emma": ("bf_emma", "Hi, I'm Emma."),
    "michael": ("am_michael", "I'm Michael."),
    "george": ("bm_george", "And I'm George."),
    "end": ("af_heart", END_LINE),
}
PROMPT = "fix the flaky auth test"
CMD1 = "/plugin marketplace add kyleoliveiro/claude-speak"
CMD2 = "/plugin install claude-speak@claude-speak"


# --------------------------------------------------------------------------
# Speech
# --------------------------------------------------------------------------

def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def envelope(x: np.ndarray, win: float = 0.01) -> np.ndarray:
    n = max(1, int(win * RATE))
    return np.convolve(np.abs(x), np.ones(n) / n, mode="same")


def trim(x: np.ndarray) -> np.ndarray:
    loud = np.nonzero(envelope(x) > 0.01)[0]
    return x[max(0, loud[0] - int(0.02 * RATE)): loud[-1] + int(0.06 * RATE)]


def synth(name: str, voice: str, text: str) -> np.ndarray:
    path = BUILD / f"{name}.wav"
    if not path.exists():
        subprocess.run([str(PLUGIN / "bin" / "claude-speak"), "say", "--voice", voice,
                        "--speed", "1.0", "--out", str(path), text], check=True)
    x = trim(read_wav(path))
    return (x / np.abs(x).max() * 0.8).astype(np.float32)


def pauses(x: np.ndarray) -> list[tuple[float, float]]:
    """Interior silent gaps as (start, end) seconds, longest first."""
    quiet = (envelope(x, 0.02) < 0.012).astype(np.int8)
    d = np.diff(np.concatenate([[0], quiet, [0]]))
    gaps = list(zip(np.flatnonzero(d == 1) / RATE, np.flatnonzero(d == -1) / RATE))
    end = len(x) / RATE
    gaps = [g for g in gaps if g[0] > 0.05 and g[1] < end - 0.05]
    return sorted(gaps, key=lambda g: g[0] - g[1])


def word_times(text: str, x: np.ndarray) -> list[dict]:
    """Approximate per-word timing: split phrases at the longest pauses, then by length."""
    phrases = [p for p in re.split(r"(?<=[.,!?])\s+", text) if p]
    cuts = sorted(pauses(x)[: len(phrases) - 1])
    if len(cuts) != len(phrases) - 1:
        cuts, phrases = [], [text]
    edges = [0.0] + [c for gap in cuts for c in gap] + [len(x) / RATE]
    words = []
    for i, phrase in enumerate(phrases):
        t0, t1 = edges[2 * i], edges[2 * i + 1]
        parts = phrase.split()
        weights = np.array([len(p) + 1.5 for p in parts], float)
        bounds = t0 + np.concatenate([[0], np.cumsum(weights)]) / weights.sum() * (t1 - t0)
        words += [{"w": p, "t0": round(float(bounds[j]), 3), "t1": round(float(bounds[j + 1]), 3)}
                  for j, p in enumerate(parts)]
    return words


def bars(x: np.ndarray, n: int) -> list[float]:
    size = len(x) // n
    peaks = np.array([np.abs(x[i * size:(i + 1) * size]).max() for i in range(n)])
    return [round(float(v), 3) for v in (peaks / peaks.max()) ** 0.7]


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

def build_timeline(clips: dict[str, np.ndarray]) -> dict:
    dur = {k: len(v) / RATE for k, v in clips.items()}
    tl: dict = {"fps": FPS}

    # 1. The problem: kinetic type.
    tl["s1"] = {"start": 0.0, "lines": [0.35, 1.5, 2.65],
                "ticks": [3.3 + 0.16 * k for k in range(7)], "end": 4.7}

    # 2. How it works: Claude works, you leave, it tells you.
    s2 = tl["s1"]["end"]
    typing = {"start": s2 + 0.65, "cps": 30, "text": PROMPT}
    enter = typing["start"] + len(PROMPT) / typing["cps"] + 0.2
    tools = [enter + 0.3 + 0.24 * k for k in range(7)]
    cover = tools[-1] + 0.45            # another window slides over the terminal
    finish = cover + 0.75               # Claude finishes behind it
    steps = [finish + 0.24 * k for k in range(4)]
    voice = steps[-1] + 0.35
    voice_end = voice + dur["demo"]
    reveal = voice_end + 0.45
    tl["s2"] = {"start": s2, "typing": typing, "enter": enter, "tools": tools, "cover": cover,
                "finish": finish, "steps": steps, "reveal": reveal, "compare": reveal + 0.7,
                "voice": {"start": voice, "dur": dur["demo"],
                          "words": word_times(DEMO_LINE, clips["demo"]), "bars": bars(clips["demo"], 432)},
                "end": reveal + 2.7}

    # 3. Why: three beats, then the voices introduce themselves.
    s3 = tl["s2"]["end"]
    t = s3 + 2.6
    voices = []
    for name in ("emma", "michael", "george"):
        voices.append({"name": name, "voice": CLIPS[name][0], "text": CLIPS[name][1],
                       "start": round(t, 3), "dur": dur[name], "bars": bars(clips[name], 40)})
        t += dur[name] + 0.25
    tl["s3"] = {"start": s3, "cols": [s3 + 0.35, s3 + 1.05, s3 + 1.75], "voices": voices, "end": t + 0.75}

    # 4. Install.
    s4 = tl["s3"]["end"]
    cmd1 = {"start": s4 + 0.8, "cps": 40, "text": CMD1}
    out1 = cmd1["start"] + len(CMD1) / cmd1["cps"] + 0.3
    cmd2 = {"start": out1 + 0.4, "cps": 40, "text": CMD2}
    out2 = cmd2["start"] + len(CMD2) / cmd2["cps"] + 0.3
    tl["s4"] = {"start": s4, "cmd1": cmd1, "out1": out1, "cmd2": cmd2, "out2": out2, "end": out2 + 1.2}

    # 5. End card.
    s5 = tl["s4"]["end"]
    v = s5 + 1.1
    tl["s5"] = {"start": s5, "voice": {"start": v, "dur": dur["end"],
                                       "words": word_times(END_LINE, clips["end"]),
                                       "bars": bars(clips["end"], 288)},
                "end": v + dur["end"] + 2.2}
    tl["duration"] = tl["s5"]["end"]
    return tl


# --------------------------------------------------------------------------
# Sound design
# --------------------------------------------------------------------------

def place(track: np.ndarray, sound: np.ndarray, t: float, gain: float = 1.0) -> None:
    i = int(t * RATE)
    j = min(len(track), i + len(sound))
    if 0 <= i < len(track):
        track[i:j] += sound[: j - i] * gain


def lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    """One-pole lowpass, vectorised via an exponential-kernel convolution."""
    n = min(len(x), int(np.ceil(np.log(1e-4) / np.log(1 - alpha))))
    kernel = alpha * (1 - alpha) ** np.arange(n)
    return np.convolve(x, kernel)[: len(x)].astype(np.float32)


def click(rng: np.random.Generator, strength: float = 1.0) -> np.ndarray:
    t = np.arange(int(0.03 * RATE)) / RATE
    body = lowpass(rng.uniform(-1, 1, len(t)), 0.35) * np.exp(-t / 0.004)
    thock = np.sin(2 * np.pi * rng.uniform(1700, 2300) * t) * np.exp(-t / 0.002) * 0.3
    return (body + thock) * strength


def tick() -> np.ndarray:
    t = np.arange(int(0.08 * RATE)) / RATE
    return np.sin(2 * np.pi * 1320 * t) * np.exp(-t / 0.012)


def chime() -> np.ndarray:
    t = np.arange(int(1.8 * RATE)) / RATE
    out = np.zeros_like(t)
    for delay, f in ((0.0, 659.25), (0.11, 987.77)):
        tt = np.clip(t - delay, 0, None)
        note = np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt) * np.exp(-tt / 0.15)
        out += note * np.exp(-tt / 0.55) * (t >= delay) * np.clip(tt / 0.004, 0, 1)
    return out


def pad(n: int) -> np.ndarray:
    t = np.arange(n) / RATE
    total = n / RATE
    out = np.zeros_like(t)
    for f in (146.83, 220.0, 329.63, 369.99, 554.37):
        for detune in (0.9985, 1.0015):
            out += np.sin(2 * np.pi * f * detune * t + f)
    out *= 1 + 0.25 * np.sin(2 * np.pi * 0.09 * t)
    out /= np.abs(out).max()
    return out * np.clip(t / 2.5, 0, 1) * np.clip((total - t) / 2.5, 0, 1)


def mix(tl: dict, clips: dict[str, np.ndarray]) -> np.ndarray:
    n = int(tl["duration"] * RATE)
    voice = np.zeros(n, np.float32)
    fx = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)

    place(voice, clips["demo"], tl["s2"]["voice"]["start"])
    for v in tl["s3"]["voices"]:
        place(voice, clips[v["name"]], v["start"])
    place(voice, clips["end"], tl["s5"]["voice"]["start"])

    for t in tl["s1"]["ticks"]:
        place(fx, tick(), t, 0.05)
    for spec in (tl["s2"]["typing"], tl["s4"]["cmd1"], tl["s4"]["cmd2"]):
        for i, ch in enumerate(spec["text"]):
            place(fx, click(rng), spec["start"] + (i + 1) / spec["cps"] + rng.uniform(-0.006, 0.006),
                  0.11 * rng.uniform(0.7, 1.1) * (0.7 if ch == " " else 1))
    for t in (tl["s2"]["enter"], tl["s4"]["out1"] - 0.3, tl["s4"]["out2"] - 0.3):
        place(fx, click(rng, 1.5), t, 0.13)
    place(fx, chime(), tl["s2"]["finish"], 0.16)

    speaking = (envelope(voice, 0.05) > 0.01).astype(np.float32)
    duck = lowpass(speaking, 0.0006)
    bed = pad(n) * 0.045 * (1 - 0.55 * duck)
    out = voice + fx + bed.astype(np.float32)
    return out / max(1.0, np.abs(out).max() / 0.95)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------

def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def open_page(pw, tl: dict):
    browser = pw.chromium.launch(channel="chrome", args=["--hide-scrollbars"])
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    page.goto((HERE / "video.html").as_uri())
    page.evaluate("""() => Promise.all([
        '700 100px "Inter Tight"', '600 100px "Inter Tight"', '500 100px "Inter Tight"',
        'italic 100px "Instrument Serif"', '400 20px "JetBrains Mono"', '500 20px "JetBrains Mono"',
    ].map(f => document.fonts.load(f))).then(() => document.fonts.ready)""")
    page.evaluate("tl => setup(tl)", tl)
    return browser, page


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stills", help="comma-separated times; write PNGs instead of the video")
    args = parser.parse_args()
    BUILD.mkdir(exist_ok=True)

    clips = {name: synth(name, voice, text) for name, (voice, text) in CLIPS.items()}
    tl = build_timeline(clips)
    (BUILD / "timeline.json").write_text(json.dumps(tl, indent=1))
    print(f"duration {tl['duration']:.2f}s")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, page = open_page(pw, tl)
        if args.stills:
            for t in (float(s) for s in args.stills.split(",")):
                page.evaluate(f"render({t})")
                page.screenshot(path=str(BUILD / f"still-{t:05.2f}.png"))
                print(f"still-{t:05.2f}.png")
            browser.close()
            return

        audio = BUILD / "audio.wav"
        write_wav(audio, mix(tl, clips))
        out = HERE / "claude-speak.mp4"
        frames = int(round(tl["duration"] * FPS))
        enc = subprocess.Popen(
            [ffmpeg(), "-y", "-loglevel", "error",
             "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "png", "-i", "-",
             "-i", str(audio),
             "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
            stdin=subprocess.PIPE)
        for i in range(frames):
            page.evaluate(f"render({i / FPS})")
            enc.stdin.write(page.screenshot(type="png"))
            if i % FPS == 0:
                print(f"\rframe {i}/{frames}", end="", flush=True)
        enc.stdin.close()
        enc.wait()
        browser.close()
        print(f"\nwrote {out.relative_to(PLUGIN)}")


if __name__ == "__main__":
    sys.exit(main())
