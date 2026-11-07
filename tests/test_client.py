"""Tests for the stdlib-only client, scripts/claude_speak.py.

Run: python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import claude_speak as cs  # noqa: E402

# Settings come from the environment too; keep the developer's own out of the tests.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN_OPTION_")}


class TempDataTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


def lines(text: str) -> list[str]:
    """Spoken lines without edge spaces, which TTS ignores."""
    return [line.strip() for line in text.splitlines()]


class SpeakableTest(unittest.TestCase):
    def test_code_block_is_replaced(self):
        self.assertEqual(lines(cs.speakable("Run this:\n```py\nprint(1)\n```\nDone.")),
                         ["Run this:", "(code block)", "Done."])

    def test_unclosed_code_block_is_replaced(self):
        self.assertEqual(lines(cs.speakable("Here:\n```\nx = 1")), ["Here:", "(code block)"])

    def test_inline_code_keeps_identifier(self):
        self.assertEqual(cs.speakable("Call `load_settings` now."), "Call load_settings now.")

    def test_links_and_urls(self):
        self.assertEqual(cs.speakable("See [the docs](https://x.io) or https://y.io/z."),
                         "See the docs or a link")

    def test_heading_list_quote_emphasis(self):
        self.assertEqual(cs.speakable("## Result\n- **fixed** it\n> quoted"),
                         "Result.\nfixed it\nquoted")

    def test_arrows_and_dashes(self):
        self.assertEqual(cs.speakable("a → b — c"), "a to b, c")

    def test_table_rows_become_sentences(self):
        self.assertEqual(cs.speakable("| a | b |\n|---|---|\n| 1 | 2 |"), "a, b.\n1, 2.")

    def test_long_text_is_cut_at_a_sentence(self):
        text = cs.speakable("Short sentence. " * 400)
        self.assertLessEqual(len(text), cs.MAX_SPOKEN_CHARS + len(" That's the gist."))
        self.assertTrue(text.endswith(". That's the gist."))


class CueTest(unittest.TestCase):
    def test_leading_cue_is_parsed(self):
        m = cs.CUE.match("[happy thumbup] I fixed it.")
        self.assertEqual(m.groups(), ("happy", "thumbup"))
        self.assertEqual("[happy thumbup] I fixed it."[m.end():], "I fixed it.")

    def test_cue_must_lead(self):
        self.assertIsNone(cs.CUE.match("I fixed it [happy thumbup]."))

    def test_unknown_cue_values_are_not_sent(self):
        with mock.patch.dict(os.environ, {"CLAUDE_SPEAK_AVATAR_URL": "http://127.0.0.1:9"}), \
                mock.patch.object(cs.urllib.request, "urlopen") as urlopen:
            cs.send_cue("ecstatic", "backflip")
        urlopen.assert_not_called()

    def test_no_avatar_url_sends_nothing(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SPEAK_AVATAR_URL"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cs.urllib.request, "urlopen") as urlopen:
            cs.send_cue("happy", "thumbup")
        urlopen.assert_not_called()


class OwnReplyTest(unittest.TestCase):
    def test_status_lines_are_own_replies(self):
        for text in ("🔊 Speaking.", "  claude-speak: saved.", "No change."):
            self.assertTrue(cs.is_own_reply(text), text)

    def test_normal_reply_is_not(self):
        self.assertFalse(cs.is_own_reply("I fixed the test."))


class TranscriptTest(unittest.TestCase):
    def test_last_response_joins_the_turn_and_skips_sidechains(self):
        self.assertEqual(cs.last_response(FIXTURES / "turn.jsonl"),
                         "Looking at it.\n\nFixed the race in token refresh; all tests pass.")

    def test_last_response_skips_own_replies(self):
        self.assertEqual(cs.last_response(FIXTURES / "own_reply.jsonl"),
                         "The cache keys on session id.")

    def test_meta_entries_do_not_end_the_turn(self):
        self.assertEqual(cs.last_response(FIXTURES / "meta_entry.jsonl"), "Working.\n\nDone.")

    def test_current_status_is_latest_text_of_this_turn(self):
        self.assertEqual(cs.current_status(FIXTURES / "turn.jsonl"),
                         ("a3", "Fixed the race in token refresh; all tests pass."))
        self.assertEqual(cs.current_status(FIXTURES / "mid_turn.jsonl"),
                         ("a1", "Reading the module first."))

    def test_current_status_stops_at_the_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            rows = [{"type": "summary"}, {"type": "assistant", "uuid": "a0",
                                          "message": {"content": [{"type": "text", "text": "Old."}]}},
                    {"type": "user", "uuid": "u1", "message": {"content": "next"}}]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(cs.current_status(path), ("", ""))


@mock.patch.dict(os.environ, CLEAN_ENV, clear=True)
class SettingsTest(TempDataTest):
    def test_defaults(self):
        s = cs.load_settings(data=self.data)
        self.assertEqual((s["voice"], s["speed"], s["auto_speak"], s["summarize"]),
                         ("af_heart", 1.1, True, True))

    def test_precedence(self):
        (self.data / "options.json").write_text(json.dumps({"voice": "am_adam", "speed": "1.3"}))
        self.assertEqual(cs.load_settings(data=self.data)["voice"], "am_adam")
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_VOICE": "am_echo"}):
            self.assertEqual(cs.load_settings(data=self.data)["voice"], "am_echo")
            cs.save_overrides(self.data, {"voice": "bm_george"})
            self.assertEqual(cs.load_settings(data=self.data)["voice"], "bm_george")
            self.assertEqual(cs.load_settings({"voice": "bf_emma"}, data=self.data)["voice"], "bf_emma")
            self.assertEqual(cs.load_settings(data=self.data, use_saved_overrides=False)["voice"], "am_echo")
        self.assertEqual(cs.load_settings(data=self.data)["speed"], 1.3)

    def test_unresolved_placeholders_are_ignored(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_VOICE": "${user_config.voice}"}):
            self.assertEqual(cs.load_settings(data=self.data)["voice"], "af_heart")

    def test_values_are_normalised(self):
        s = cs.load_settings({"speed": "9", "auto_speak": "off", "summarize": "yes"})
        self.assertEqual((s["speed"], s["auto_speak"], s["summarize"]), (2.0, False, True))
        self.assertEqual(cs.load_settings({"speed": "fast"})["speed"], cs.DEFAULTS["speed"])

    def test_overrides_keep_only_overridable_keys_and_none_removes(self):
        (self.data / "overrides.json").write_text(json.dumps({"voice": "am_adam", "summary_model": "opus"}))
        self.assertEqual(cs.load_overrides(self.data), {"voice": "am_adam"})
        cs.save_overrides(self.data, {"voice": None, "speed": 1.5})
        self.assertEqual(cs.load_overrides(self.data), {"speed": 1.5})


class SummaryCacheTest(TempDataTest):
    def test_same_reply_and_model_hits(self):
        cs.save_summary(self.data, "s1", "Final message.", "haiku", "[neutral none] Short.")
        self.assertEqual(cs.cached_summary(self.data, "s1", "Final message.", "haiku"),
                         "[neutral none] Short.")

    def test_whole_turn_ending_with_the_reply_hits(self):
        cs.save_summary(self.data, "s1", "Final message.", "haiku", "Short.")
        self.assertEqual(cs.cached_summary(self.data, "s1", "Earlier.\n\nFinal message.", "haiku"), "Short.")

    def test_misses(self):
        cs.save_summary(self.data, "s1", "Final message.", "haiku", "Short.")
        self.assertIsNone(cs.cached_summary(self.data, "s1", "Other.", "haiku"))
        self.assertIsNone(cs.cached_summary(self.data, "s1", "Final message.", "sonnet"))
        self.assertIsNone(cs.cached_summary(self.data, "s2", "Final message.", "haiku"))

    def test_unsafe_session_ids_are_sanitised(self):
        self.assertEqual(cs._safe_id("../../etc/passwd"), "etcpasswd")
        self.assertIsNone(cs._summary_file(self.data, "../"))


class PrepareTest(unittest.TestCase):
    SETTINGS = {"summarize": True, "summary_model": "haiku"}

    def test_short_text_is_not_summarised(self):
        with mock.patch.object(cs, "summarize") as summarize:
            self.assertEqual(cs.prepare("Done.", self.SETTINGS), "Done.")
        summarize.assert_not_called()

    def test_long_text_is_summarised(self):
        with mock.patch.object(cs, "summarize", return_value="**Fixed** it.") as summarize:
            self.assertEqual(cs.prepare("x" * (cs.SUMMARY_THRESHOLD + 1), self.SETTINGS), "Fixed it.")
        summarize.assert_called_once()

    def test_failed_summary_falls_back_to_text(self):
        with mock.patch.object(cs, "summarize", return_value=None):
            text = "word " * 100
            self.assertEqual(cs.prepare(text, self.SETTINGS), text.strip())

    def test_force_full_skips_summary(self):
        with mock.patch.object(cs, "summarize") as summarize:
            cs.prepare("x" * 500, self.SETTINGS, force_full=True)
        summarize.assert_not_called()


class QuestionTextTest(unittest.TestCase):
    def test_question_is_spoken_with_recommendation(self):
        text = cs.question_text({"questions": [{"question": "Which runner?", "options": [
            {"label": "unittest (Recommended)"}, {"label": "pytest"}]}]})
        self.assertIn("Which runner?", text)
        self.assertIn("unittest", text)

    def test_empty_input(self):
        self.assertEqual(cs.question_text({}), "")


if __name__ == "__main__":
    unittest.main()
