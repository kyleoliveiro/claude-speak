---
name: speak-auto
description: Turn automatic speaking of Claude's responses on or off (claude-speak plugin). With no argument, shows an On/Off menu.
argument-hint: "[on|off|toggle|default]"
disable-model-invocation: true
allowed-tools:
  - AskUserQuestion
  - Bash(${CLAUDE_PLUGIN_ROOT}/bin/claude-speak *)
---

Arguments: "$ARGUMENTS"

!`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak auto --data-dir ${CLAUDE_PLUGIN_DATA} $ARGUMENTS`

The line above is claude-speak's status. Keep your reply to one line, with no preamble.

**If Arguments is not empty,** the change has already been made. Reply with only the status line.

**If Arguments is empty,** show a menu with the AskUserQuestion tool: one question, header "Auto-speak", question "Speak Claude's responses aloud automatically?", and these options:

1. label "On", description "Speak a short summary after every response."
2. label "Off", description "Stay quiet. Use /speak whenever you want to hear the last response."

Append " (current)" to the label that matches the status line. Then run this with the Bash tool, using `on` or `off` for the choice:

`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak auto --data-dir ${CLAUDE_PLUGIN_DATA} on`

Reply with only the line it prints. If the user dismisses the menu, reply "No change." and stop.
