# claude-speak core

Claude Code plugin: speaks a short summary of each reply with local Kokoro-82M TTS. Fork `lucaschallamel/claude-speak` of `kyleoliveiro/claude-speak`; keep fork changes small and generic pieces avatar-free so they can go upstream. Sibling repo `~/Github/agent-avatar` (3D avatar) depends on this one and vice versa.

## Source map
- `scripts/claude_speak.py`: stdlib-only client (hooks must start fast; never add deps). All subcommands; `cmd_hook` dispatches on `hook_event_name`; `spawn_worker` detaches `_worker` so hooks return in ~50 ms.
- `scripts/server.py`: PEP 723 script (`uv run --script`, kokoro-onnx + numpy). Kokoro kept loaded; HTTP on a unix socket in the data dir (`/health /speak /warm /synth /stop`); per-session queue (newer speech from a session replaces its older one); idle exit.
- `hooks/hooks.json`: SessionStart, Stop, PreToolUse(AskUserQuestion), PostToolUse(*) all call `bin/claude-speak hook`.
- `skills/*/SKILL.md`: thin; `!` preprocessor runs the CLI and echoes its status line.
- `.claude-plugin/plugin.json`: version + `userConfig` settings. `assets/`: README/video generators, not runtime.

## Invariants
- Recursion guard: `_ask_claude` sets `CLAUDE_SPEAK_NESTED=1`; `cmd_hook` returns on it. Headless `CLAUDE_CODE_ENTRYPOINT=sdk*` skipped unless `CLAUDE_SPEAK_HEADLESS=1`.
- No double speech: `/speak` writes `skip-<session>`; `is_own_reply()` filters replies starting `🔊`, `claude-speak:`, `No change.`. Skill replies must keep those prefixes.
- Settings precedence (`load_settings`): DEFAULTS < `options.json` < `CLAUDE_PLUGIN_OPTION_*` < `overrides.json` (only `OVERRIDABLE` keys) < flags. Skills lack `CLAUDE_PLUGIN_OPTION_*`, so hooks copy them to `options.json`. New setting = DEFAULTS + plugin.json `userConfig` (+ OVERRIDABLE + speak-settings skill if user-toggleable).
- Summary cue `[mood gesture]` is stripped before speech and posted to `CLAUDE_SPEAK_AVATAR_URL/cue`; allowed values `AVATAR_MOODS`/`AVATAR_GESTURES`.
- Player = `CLAUDE_SPEAK_PLAYER` tokens + WAV path (server.py `_spawn`); player inherits server env.

Stack: `mem:tech_stack`. Commands: `mem:suggested_commands`. Workflow, roadmap and story rules: `mem:conventions`. Done-checklist: `mem:task_completion`.
