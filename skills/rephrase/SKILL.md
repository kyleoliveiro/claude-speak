---
name: rephrase
description: Say Claude's last response again in different, plainer words. Add a note on what was unclear, e.g. "/rephrase what does the cache key on?".
argument-hint: "[what was unclear]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/claude-speak *)
---

Arguments: "$ARGUMENTS"

Run this with the Bash tool. If Arguments is not empty, append it as one single-quoted argument, with any `'` in it written as `'\''`:

`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak rephrase --data-dir ${CLAUDE_PLUGIN_DATA} --session ${CLAUDE_SESSION_ID}`

Then reply with 🔊 followed by the line it prints, reworded only if it reports a problem. Don't add anything else, and don't answer the note yourself: the spoken rephrase does that.
