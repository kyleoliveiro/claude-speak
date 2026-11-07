# Conventions and workflow

- Story IDs = GitHub issue numbers per repo (no US-xxx). Say "claude-speak #2" vs "agent-avatar #2" when both are in play.
- One branch + PR per issue (`Closes #n`); after approval: merge, back to main, delete branch.
- Milestones: 1 Foundation, 2 Quiet, 3 Characters, 4 Smarter speech (agent-avatar has 3 and 4). Build order follows them; #15 (second TTS engine spike) last.
- Settled design decisions are recorded as comments on their issues; read the issue thread before building.
- Linux/Hyprland-first is fine; never break macOS (CI covers it); document Mac fallback per story.
- Generic features stay free of avatar code (upstream later); fork-only bits (avatar cues, Omarchy triggers) behind env flags or separate files.
- Comments explain why only; style matches surrounding code (section banner comments in claude_speak.py).
- Docs to update when behaviour changes: README (fork additions section), CLAUDE.md, skill files.
