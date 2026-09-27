---
name: speak
description: Read Claude's last response aloud with local Kokoro text-to-speech. "/speak full" reads it word for word, "/speak stop" stops playback.
argument-hint: "[full|stop]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/claude-speak *)
---

!`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak speak --data-dir ${CLAUDE_PLUGIN_DATA} --session ${CLAUDE_SESSION_ID} $ARGUMENTS`

The claude-speak plugin has already handled this request; the line above is its status. Reply with 🔊 followed by that status line, reworded only if it reports a problem. Don't call any tools and don't add anything else.
