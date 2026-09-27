---
name: speak-settings
description: Choose claude-speak's voice, speaking speed, auto-speak and summaries from a menu. "/speak-settings reset" goes back to your /config settings.
argument-hint: "[reset]"
disable-model-invocation: true
allowed-tools:
  - AskUserQuestion
  - Bash(${CLAUDE_PLUGIN_ROOT}/bin/claude-speak *)
---

Arguments: "$ARGUMENTS"

!`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak config --data-dir ${CLAUDE_PLUGIN_DATA}`

Above are claude-speak's current settings. Keep your final reply short, with no preamble.

**If Arguments is "reset",** run `${CLAUDE_PLUGIN_ROOT}/bin/claude-speak config --data-dir ${CLAUDE_PLUGIN_DATA} --reset` with the Bash tool and reply with only the line it prints.

**Otherwise,** show one AskUserQuestion menu with these four questions, in this order. Append " (current)" to each option label that matches a current setting. If the current voice isn't one of the four listed, add "Currently <voice>." to the end of the voice question.

1. Header "Voice", question "Which voice should read Claude's responses? Choose Other to type any Kokoro voice, e.g. af_bella, am_fenrir, bf_isabella, bm_fable.", options:
   - "af_heart", description "American English, female. Warm and natural (default)."
   - "am_michael", description "American English, male. Calm and clear."
   - "bf_emma", description "British English, female. Crisp and bright."
   - "bm_george", description "British English, male. Low and measured."
2. Header "Speed", question "How fast should it speak? Choose Other to type any value from 0.5 to 2.0.", options:
   - "1.0x", description "Normal speed."
   - "1.1x", description "A little brisker (default)."
   - "1.25x", description "Faster, still easy to follow."
   - "1.5x", description "Quick updates."
3. Header "Auto-speak", question "Speak every response automatically?", options:
   - "On", description "Speak after every response."
   - "Off", description "Only speak when you run /speak."
4. Header "Summaries", question "What should it read out?", options:
   - "Summary", description "A one or two sentence summary. Short replies are read in full."
   - "Full text", description "The whole response, minus code blocks."

Then save the answers with one Bash call. Use the voice name, the speed as a plain number, `on`/`off` for auto-speak, and `on` for Summary or `off` for Full text. Leave out any flag whose question the user skipped:

`${CLAUDE_PLUGIN_ROOT}/bin/claude-speak config --data-dir ${CLAUDE_PLUGIN_DATA} --session ${CLAUDE_SESSION_ID} --preview --voice af_heart --speed 1.1 --auto on --summarize on`

Reply with only the lines it prints. If it reports a problem, such as an unknown voice, pass that on in one sentence. If the user dismisses the menu, reply "No change." and stop.
