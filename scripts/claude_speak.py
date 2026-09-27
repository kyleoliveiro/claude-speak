#!/usr/bin/env python3
"""claude-speak client: hear Claude Code's replies, spoken by a local Kokoro model.

Stdlib only, so the Stop hook starts fast. The model runs in a separate,
long-lived server (server.py) that this client starts on demand with uv.

Subcommands:
  hook              Stop hook entry point (reads hook JSON on stdin)
  speak             speak the last response of a session (used by /speak)
  auto on|off|toggle|default|status
                    override the auto_speak setting (used by /speak-auto)
  config [--voice V] [--speed S] [--auto on|off] [--summarize on|off] [--reset]
                    show or change settings (used by /speak-settings)
  say TEXT          speak arbitrary text; --out FILE writes a WAV instead
  stop              stop playback
  status            show server and settings status
  setup             download the model and start the server
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = ROOT / "scripts" / "server.py"

# Replies at or under this length are spoken as-is, without a summary.
SUMMARY_THRESHOLD = 280
# Upper bound on how much text is read aloud when not summarizing.
MAX_SPOKEN_CHARS = 3000
# Marker the /speak skill replies with; never treated as a response to speak.
SPEAK_MARKER = "🔊"

SUMMARY_PROMPT = (
    "You are the voice of a coding assistant. The user message contains, inside "
    "<reply> tags, a reply the assistant just wrote to a developer. Say it out loud "
    "for a developer who is listening rather than reading: one or two short, natural "
    "sentences, at most 40 words, spoken as the assistant in the first person "
    "(\"I fixed...\", \"I found...\"), addressing the developer as \"you\". Cover "
    "what was done or found, and any question or decision waiting on the developer. "
    "Plain text only: no markdown, code, file paths, URLs or lists. Output only the "
    "words to speak."
)

# Every voice in Kokoro v1.0 (voices-v1.0.bin).
VOICES = (
    "af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river "
    "af_sarah af_sky am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck "
    "am_santa bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis "
    "ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola "
    "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa "
    "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang"
).split()

# Settings that /speak-auto and /speak-settings can change without /config.
OVERRIDABLE = ("auto_speak", "summarize", "voice", "speed")


def is_own_reply(text: str) -> bool:
    """Replies to /speak, /speak-auto and /speak-settings: status lines, not worth speaking."""
    return text.lstrip().startswith((SPEAK_MARKER, "claude-speak:", "No change."))


DEFAULTS = {
    "auto_speak": True,
    "summarize": True,
    "voice": "af_heart",
    "speed": 1.1,
    "summary_model": "haiku",
}


# --------------------------------------------------------------------------
# Settings and state
# --------------------------------------------------------------------------

def data_dir(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("CLAUDE_SPEAK_DATA")
    if not raw or raw.startswith("${"):
        raw = os.path.join(os.environ.get("XDG_DATA_HOME", "~/.local/share"), "claude-speak")
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _usable(value) -> bool:
    # Unresolved ${user_config.x} placeholders and empty strings mean "not set".
    return value is not None and str(value).strip() != "" and not str(value).startswith("${")


def _as_bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def load_overrides(data: Path) -> dict:
    return {k: v for k, v in _read_json(data / "overrides.json").items() if k in OVERRIDABLE}


def save_overrides(data: Path, changes: dict) -> None:
    """Merge changes into overrides.json; a value of None removes that override."""
    overrides = load_overrides(data)
    for key, value in changes.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    (data / "overrides.json").write_text(json.dumps(overrides, indent=2))


def load_settings(overrides: dict | None = None, data: Path | None = None,
                  use_saved_overrides: bool = True) -> dict:
    """Resolve settings, lowest priority first.

    Defaults, then the /config options (CLAUDE_PLUGIN_OPTION_* in hooks; skills
    don't get those variables, so hooks save them to options.json), then
    choices made with /speak-auto and /speak-settings, then explicit flags.
    """
    settings = dict(DEFAULTS)
    if data is not None:
        settings.update(_read_json(data / "options.json"))
    for key in DEFAULTS:
        env = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")
        if _usable(env):
            settings[key] = env
    if data is not None and use_saved_overrides:
        settings.update(load_overrides(data))
    for key, value in (overrides or {}).items():
        if _usable(value):
            settings[key] = value
    settings["auto_speak"] = _as_bool(settings["auto_speak"])
    settings["summarize"] = _as_bool(settings["summarize"])
    try:
        settings["speed"] = min(2.0, max(0.5, float(settings["speed"])))
    except ValueError:
        settings["speed"] = DEFAULTS["speed"]
    return settings


def save_options(data: Path) -> None:
    options = {key: os.environ[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] for key in DEFAULTS
               if _usable(os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}"))}
    path = data / "options.json"
    if options and (not path.exists() or json.loads(path.read_text() or "{}") != options):
        path.write_text(json.dumps(options))


def parse_bool(value) -> bool | None:
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return None


def parse_speed(value) -> float | None:
    value = str(value).strip().lower().rstrip("x×").strip()
    if value == "normal":
        return 1.0
    try:
        speed = float(value)
    except ValueError:
        return None
    return round(speed, 2) if 0.5 <= speed <= 2.0 else None


def _safe_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")


def remember_transcript(data: Path, session_id: str, transcript: str) -> None:
    if not _safe_id(session_id) or not transcript:
        return
    sessions = data / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / _safe_id(session_id)).write_text(transcript)


def find_transcript(data: Path, session_id: str) -> Path | None:
    sid = _safe_id(session_id)
    if not sid:
        return None
    recorded = data / "sessions" / sid
    if recorded.exists():
        path = Path(recorded.read_text().strip())
        if path.exists():
            return path
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    matches = sorted((config_dir / "projects").glob(f"*/{sid}.jsonl"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


# --------------------------------------------------------------------------
# Transcript parsing and text cleanup
# --------------------------------------------------------------------------

def _is_user_prompt(entry: dict) -> bool:
    """A real prompt from the user, as opposed to a tool result."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return any(isinstance(b, dict) and b.get("type") == "text" for b in content or [])


def _assistant_text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def last_response(transcript: Path) -> str:
    """Text of the most recent assistant turn that said something speakable."""
    entries = []
    with transcript.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("isSidechain"):
                continue
            entries.append(entry)

    parts: list[str] = []
    for entry in reversed(entries):
        if _is_user_prompt(entry):
            text = "\n\n".join(reversed(parts)).strip()
            if text and not is_own_reply(text) and re.search(r"\w", text):
                return text
            parts = []
        elif entry.get("type") == "assistant":
            text = _assistant_text(entry).strip()
            if text:
                parts.append(text)
    text = "\n\n".join(reversed(parts)).strip()
    return "" if is_own_reply(text) else text


def speakable(markdown: str) -> str:
    """Turn markdown into something that sounds right when read aloud."""
    text = re.sub(r"```.*?(```|$)", " (code block) ", markdown, flags=re.S)
    text = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", text, flags=re.M)          # table rules
    text = re.sub(r"^\s*\|(.*)\|\s*$",
                  lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()) + ".",
                  text, flags=re.M)                                        # table rows
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)                 # links, images
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)                                   # html tags
    text = re.sub(r"^\s{0,3}#{1,6}\s*(.*)$", r"\1.", text, flags=re.M)     # headings
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)      # list markers
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)                       # quotes
    text = re.sub(r"(\*\*|__|\*|~~)", "", text)
    text = re.sub(r"\s*→\s*", " to ", text)
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\.\.+", ".", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        text = cut[: max(cut.rfind(". "), cut.rfind("\n"), MAX_SPOKEN_CHARS // 2) + 1] + " That's the gist."
    return text


def summarize(text: str, model: str) -> str:
    claude = shutil.which("claude")
    if not claude:
        return text
    # Extended thinking makes a two-sentence summary take ~30s instead of ~3s.
    env = dict(os.environ, CLAUDE_SPEAK_NESTED="1", MAX_THINKING_TOKENS="0")
    try:
        result = subprocess.run(
            [claude, "-p", "--model", model, "--tools", "", "--no-session-persistence",
             "--strict-mcp-config", "--system-prompt", SUMMARY_PROMPT],
            input=f"<reply>\n{text}\n</reply>", cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=90, env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return text
    summary = result.stdout.strip()
    return summary if result.returncode == 0 and summary else text


def prepare(text: str, settings: dict, force_full: bool = False) -> str:
    if settings["summarize"] and not force_full and len(text) > SUMMARY_THRESHOLD:
        text = summarize(text, settings["summary_model"])
    return speakable(text)


# --------------------------------------------------------------------------
# Talking to the TTS server
# --------------------------------------------------------------------------

class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def socket_path(data: Path) -> Path:
    """Unix socket paths are limited to ~104 bytes, so keep it out of the data dir."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        base = tempfile.gettempdir()
    digest = hashlib.sha1(str(data.resolve()).encode()).hexdigest()[:10]
    return Path(base) / f"claude-speak-{os.getuid()}-{digest}.sock"


def request(data: Path, method: str, path: str, body: dict | None = None,
            timeout: float = 5) -> tuple[int, bytes]:
    conn = _UnixConnection(socket_path(data), timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def server_health(data: Path) -> dict | None:
    try:
        status, body = request(data, "GET", "/health", timeout=2)
        return json.loads(body) if status == 200 else None
    except (OSError, http.client.HTTPException, ValueError):
        return None


def ensure_server(data: Path, wait: float = 900) -> dict:
    """Start the TTS server if needed. The first start downloads the model."""
    health = server_health(data)
    if health:
        return health
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to run the Kokoro server: https://docs.astral.sh/uv/")

    with open(data / "server.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        health = server_health(data)
        if health:
            return health
        log = open(data / "server.log", "ab")
        server = subprocess.Popen(
            [uv, "run", "--quiet", "--script", str(SERVER_SCRIPT),
             "--data-dir", str(data), "--socket", str(socket_path(data))],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
        deadline = time.time() + wait
        while time.time() < deadline and server.poll() is None:
            time.sleep(0.5)
            health = server_health(data)
            if health:
                return health
    raise RuntimeError(f"the TTS server did not start; see {data / 'server.log'}")


def send_to_server(data: Path, text: str, settings: dict, session: str = "", label: str = "") -> None:
    """Queue speech. session lets a newer reply replace an older one; label names
    the project aloud when other sessions are speaking too."""
    ensure_server(data)
    request(data, "POST", "/speak",
            {"text": text, "voice": settings["voice"], "speed": settings["speed"],
             "session": session, "label": label})


def log_error(data: Path, message: str) -> None:
    with open(data / "claude-speak.log", "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def spawn_worker(data: Path, text: str, settings: dict, force_full: bool = False,
                 session: str = "", label: str = "") -> None:
    """Summarize and speak in a detached process so the caller returns immediately."""
    job = {"text": text, "settings": settings, "force_full": force_full,
           "session": session, "label": label}
    proc = subprocess.Popen(
        [sys.executable, __file__, "_worker", "--data-dir", str(data)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc.stdin.write(json.dumps(job).encode())
    proc.stdin.close()


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_hook(args) -> None:
    if os.environ.get("CLAUDE_SPEAK_NESTED"):
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    data = data_dir(args.data_dir)
    save_options(data)
    session_id = event.get("session_id", "")
    transcript = event.get("transcript_path", "")
    remember_transcript(data, session_id, transcript)
    if event.get("hook_event_name") == "SessionStart":
        return

    # /speak already spoke; don't also speak its acknowledgement.
    skip = data / f"skip-{_safe_id(session_id)}"
    if skip.exists():
        recent = time.time() - skip.stat().st_mtime < 600
        skip.unlink(missing_ok=True)
        if recent:
            return

    settings = load_settings(data=data)
    if not settings["auto_speak"]:
        return
    if not transcript or not Path(transcript).exists():
        return

    text = event.get("last_assistant_message") or ""
    if not isinstance(text, str) or not text.strip():
        text = last_response(Path(transcript))
    if text and not is_own_reply(text):
        spawn_worker(data, text, settings, session=session_id, label=Path(event.get("cwd") or "").name)


def cmd_worker(args) -> None:
    data = data_dir(args.data_dir)
    job = json.load(sys.stdin)
    try:
        # Wake the output device while the summary is written, if the server is up.
        if server_health(data):
            request(data, "POST", "/warm")
        text = prepare(job["text"], job["settings"], job.get("force_full", False))
        if text:
            send_to_server(data, text, job["settings"], job.get("session", ""), job.get("label", ""))
    except Exception as exc:  # detached: the log is the only place errors can go
        log_error(data, f"speak failed: {exc}")


def _skill_settings(args, data: Path) -> dict:
    return load_settings({"voice": args.voice, "speed": args.speed,
                          "summarize": args.summarize, "summary_model": args.summary_model},
                         data=data)


def cmd_speak(args) -> None:
    data = data_dir(args.data_dir)
    mode = (args.mode or "").strip().lower()
    if mode == "stop":
        cmd_stop(args)
        return

    transcript = find_transcript(data, args.session or "")
    if not transcript:
        print("claude-speak: couldn't find this session's transcript.")
        return
    text = last_response(transcript)
    if not text:
        print("claude-speak: there's no response to speak yet.")
        return

    settings = _skill_settings(args, data)
    force_full = mode == "full"
    if _safe_id(args.session or ""):
        (data / f"skip-{_safe_id(args.session)}").touch()
    spawn_worker(data, text, settings, force_full=force_full,
                 session=args.session or "", label=Path.cwd().name)

    how = "in full" if force_full or not settings["summarize"] else "as a summary"
    note = "" if server_health(data) else " (starting the voice model; the first run downloads it, ~350 MB)"
    print(f"claude-speak: speaking the last response {how}, voice {settings['voice']}{note}.")


def _source(data: Path, key: str) -> str:
    return "chosen with a /speak command" if key in load_overrides(data) else "from /config"


def cmd_auto(args) -> None:
    data = data_dir(args.data_dir)
    action = (args.action or "status").strip().lower()
    if action == "toggle":
        action = "off" if load_settings(data=data)["auto_speak"] else "on"
    if parse_bool(action) is not None:
        save_overrides(data, {"auto_speak": parse_bool(action)})
    elif action in ("default", "reset"):
        save_overrides(data, {"auto_speak": None})
    elif action != "status":
        print("claude-speak: usage: /speak-auto [on|off|toggle|default|status]")
        return

    on = load_settings(data=data)["auto_speak"]
    print(f"claude-speak: auto-speak is {'ON' if on else 'OFF'} ({_source(data, 'auto_speak')}).")


def _describe(settings: dict) -> str:
    return (f"voice {settings['voice']}, speed {settings['speed']:g}x, "
            f"auto-speak {'on' if settings['auto_speak'] else 'off'}, "
            f"summaries {'on' if settings['summarize'] else 'off'}")


def cmd_config(args) -> None:
    data = data_dir(args.data_dir)
    if args.reset:
        (data / "overrides.json").unlink(missing_ok=True)
        settings = load_settings(data=data)
        print(f"claude-speak: back to your /config settings: {_describe(settings)}.")
        return

    changes, problems = {}, []
    if args.voice is not None:
        voice = args.voice.strip().lower()
        if voice in VOICES:
            changes["voice"] = voice
        else:
            close = [v for v in VOICES if v[:2] == voice[:2]] or VOICES[:8]
            problems.append(f"unknown voice '{args.voice}'. Try one of: {', '.join(close)}")
    if args.speed is not None:
        speed = parse_speed(args.speed)
        if speed is None:
            problems.append(f"speed must be a number from 0.5 to 2.0, not '{args.speed}'")
        else:
            changes["speed"] = speed
    for key, raw in (("auto_speak", args.auto), ("summarize", args.summarize)):
        if raw is not None:
            value = parse_bool(raw)
            if value is None:
                problems.append(f"{key.replace('_', '-')} must be on or off, not '{raw}'")
            else:
                changes[key] = value

    if changes:
        save_overrides(data, changes)
    settings = load_settings(data=data)
    for problem in problems:
        print(f"claude-speak: {problem}")

    if not changes:
        print("claude-speak: current settings:")
        for key, label in (("auto_speak", "auto-speak"), ("summarize", "summaries"),
                           ("voice", "voice"), ("speed", "speed")):
            value = settings[key]
            shown = ("on" if value else "off") if isinstance(value, bool) else (
                f"{value:g}x" if key == "speed" else value)
            print(f"  {label}: {shown} ({_source(data, key)})")
        return

    print(f"claude-speak: saved. Now using {_describe(settings)}.")
    if args.preview and ("voice" in changes or "speed" in changes):
        name = settings["voice"].split("_", 1)[-1].replace("_", " ").title()
        sample = f"Hi, I'm {name}. This is how I'll sound when Claude finishes a task."
        if _safe_id(args.session or ""):
            (data / f"skip-{_safe_id(args.session)}").touch()
        spawn_worker(data, sample, dict(settings, summarize=False), session=args.session or "")
        print("claude-speak: playing a sample of the new voice.")


def cmd_say(args) -> None:
    data = data_dir(args.data_dir)
    settings = _skill_settings(args, data)
    text = speakable(" ".join(args.text))
    if args.out:
        ensure_server(data)
        status, body = request(data, "POST", "/synth",
                               {"text": text, "voice": settings["voice"], "speed": settings["speed"]},
                               timeout=300)
        if status != 200:
            sys.exit(f"claude-speak: synthesis failed: {body.decode(errors='replace')}")
        Path(args.out).write_bytes(body)
        print(f"claude-speak: wrote {args.out}")
    else:
        send_to_server(data, text, settings)


def cmd_stop(args) -> None:
    data = data_dir(args.data_dir)
    if server_health(data):
        request(data, "POST", "/stop")
    print("claude-speak: stopped.")


def cmd_status(args) -> None:
    data = data_dir(args.data_dir)
    health = server_health(data)
    settings = load_settings(data=data)
    print(f"data dir:    {data}")
    print(f"settings:    {_describe(settings)}")
    print(f"server:      {'running' if health else 'not running'}")
    if health:
        print(f"model:       {health.get('model')}")
        print(f"player:      {health.get('player')}")
        print(f"voices:      {len(health.get('voices', []))} available")


def cmd_setup(args) -> None:
    data = data_dir(args.data_dir)
    print("claude-speak: installing dependencies and downloading the Kokoro model (first run only)...")
    health = ensure_server(data)
    print(f"claude-speak: ready. Model {health.get('model')}, audio via {health.get('player')}.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="claude-speak", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def with_voice(p):
        p.add_argument("--data-dir")
        p.add_argument("--voice")
        p.add_argument("--speed")
        p.add_argument("--summarize")
        p.add_argument("--summary-model")
        return p

    sub.add_parser("hook").add_argument("--data-dir")
    sub.add_parser("_worker").add_argument("--data-dir")

    p = with_voice(sub.add_parser("speak"))
    p.add_argument("--session")
    p.add_argument("mode", nargs="?", help="'full' to skip the summary, 'stop' to stop")

    p = sub.add_parser("auto")
    p.add_argument("--data-dir")
    p.add_argument("action", nargs="?")

    p = sub.add_parser("config")
    p.add_argument("--data-dir")
    p.add_argument("--voice")
    p.add_argument("--speed")
    p.add_argument("--auto")
    p.add_argument("--summarize")
    p.add_argument("--reset", action="store_true", help="forget /speak choices, use /config")
    p.add_argument("--preview", action="store_true", help="play a sample after changing the voice")
    p.add_argument("--session")

    p = with_voice(sub.add_parser("say"))
    p.add_argument("--out")
    p.add_argument("text", nargs="+")

    for name in ("stop", "status", "setup"):
        sub.add_parser(name).add_argument("--data-dir")

    args, _unknown = parser.parse_known_args()
    handler = {"hook": cmd_hook, "_worker": cmd_worker, "speak": cmd_speak, "auto": cmd_auto,
               "config": cmd_config, "say": cmd_say, "stop": cmd_stop, "status": cmd_status, "setup": cmd_setup}[args.cmd]
    try:
        handler(args)
    except Exception as exc:
        # Never fail a hook or a skill render over speech.
        print(f"claude-speak: {exc}")
        if args.cmd in ("say", "setup"):
            sys.exit(1)


if __name__ == "__main__":
    main()
