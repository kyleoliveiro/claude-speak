# Suggested commands

- Tests: `python3 -m unittest discover tests`; one test: `python3 -m unittest tests.test_client.SpeakableTest.test_links_and_urls`.
- Run Claude Code with this working copy: `claude --plugin-dir .`; validate manifests: `claude plugin validate .`.
- Voice directly: `CLAUDE_SPEAK_DATA=/tmp/claude-speak ./bin/claude-speak say "hello"`; `./bin/claude-speak status|stop`.
- Simulate a hook: `echo '{"hook_event_name":"Stop","session_id":"x","transcript_path":"/path.jsonl","cwd":"'$PWD'"}' | CLAUDE_SPEAK_DATA=/tmp/claude-speak ./bin/claude-speak hook`.
- Issues/PRs: `gh issue list -R lucaschallamel/claude-speak --milestone "2 Quiet"`.
