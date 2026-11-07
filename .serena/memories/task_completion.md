# Task completion

- `python3 -m unittest discover tests` passes; add tests for new pure logic (fixtures in `tests/fixtures/*.jsonl`).
- Hook/client change: simulate the hook (see `mem:suggested_commands`) with a temp `CLAUDE_SPEAK_DATA`; check `claude-speak.log`.
- Server change: restart it (`bin/claude-speak stop`, server idles out or gets re-spawned) and `say` something; check `server.log`.
- Manifests changed: `claude plugin validate .`. Known failure: the name `claude-speak` is reserved (`claude-` prefix), so that error is expected; any other error is real. Rename decision is still open. Release: bump `version` in `.claude-plugin/plugin.json`.
- Installed plugin runs from `~/.claude/plugins/cache/...`, not this checkout: test with `claude --plugin-dir .`.
- Update docs per `mem:conventions`, commit on the branch, push + PR, wait for CI, ask before merge.
