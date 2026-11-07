# Tech stack

- Python: client stdlib-only, >=3.10 (`bin/claude-speak` uses python3, falls back to `uv run`). Server requires >=3.10,<3.14 (kokoro-onnx), deps inline via PEP 723, run with `uv`.
- TTS: kokoro-onnx, model `kokoro-v1.0.<fp32|fp16|int8>.onnx` + `voices-v1.0.bin`, downloaded on first server start into the data dir. `Kokoro.create()` accepts a voice name or a style vector (blends possible).
- Voices: 54; name = `<lang><gender>_<name>`; lang via `LANGS` in server.py (a en-us, b en-gb, e es, f fr-fr, h hi, i it, j ja, p pt-br, z cmn). No Australian voice; French only `ff_siwis`.
- Players: afplay (macOS), pw-play/paplay/aplay/ffplay (Linux), or `CLAUDE_SPEAK_PLAYER`.
- Summaries: nested `claude -p --model haiku --tools "" --strict-mcp-config`, thinking off.
- Data dir: `$CLAUDE_PLUGIN_DATA` (installed: `~/.claude/plugins/data/claude-speak-claude-speak/`), else `CLAUDE_SPEAK_DATA`, else XDG data. Logs `server.log`, `claude-speak.log`.
- Tests: stdlib unittest; CI GitHub Actions Ubuntu + macOS, Python 3.10/3.13.
