"""Tests for scripts/server.py with Kokoro stubbed out: no model, no audio, no uv.

Run: python3 -m unittest discover tests
"""

import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# server.py imports numpy and kokoro_onnx at the top; stub them when absent so
# the tests run on a bare python3 like the client's.
for name in ("numpy", "kokoro_onnx"):
    try:
        __import__(name)
    except ImportError:
        stub = types.ModuleType(name)
        stub.Kokoro = object
        stub.ndarray = object
        sys.modules[name] = stub

import server  # noqa: E402


class SplitChunksTest(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(server.split_chunks("One. Two!"), ["One. Two!"])

    def test_long_text_splits_at_sentences_under_the_limit(self):
        sentence = "This is a sentence of moderate length."
        chunks = server.split_chunks(" ".join([sentence] * 20))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), server.CHUNK_CHARS)
            self.assertTrue(chunk.endswith("."))

    def test_newlines_split_and_blanks_vanish(self):
        self.assertEqual(server.split_chunks("\n  a\n\n b  \n"), ["a b"])

    def test_empty(self):
        self.assertEqual(server.split_chunks("   "), [])


def speaker() -> "server.Speaker":
    """A Speaker with queue state only: no Kokoro, player or playback thread."""
    sp = server.Speaker.__new__(server.Speaker)
    sp.state_lock = threading.Lock()
    sp.wake = threading.Condition(sp.state_lock)
    sp.queue, sp.current, sp.proc, sp.warm_proc = [], None, None, None
    sp.last_seen, sp.busy_until = {}, 0.0
    sp.voices = {"af_heart", "bm_george"}
    return sp


class SpeakerQueueTest(unittest.TestCase):
    def test_newer_speech_from_a_session_replaces_its_queued_speech(self):
        sp = speaker()
        sp.speak("old", "bm_george", 1.0, session="a")
        sp.speak("other", "bm_george", 1.0, session="b")
        sp.speak("new", "bm_george", 1.0, session="a")
        self.assertEqual([(j.session, j.text.split(": ")[-1]) for j in sp.queue],
                         [("b", "other"), ("a", "new")])

    def test_newer_speech_cancels_the_session_s_current_job(self):
        sp = speaker()
        sp.current = server.Job("a", "playing", "bm_george", 1.0)
        sp.speak("new", "bm_george", 1.0, session="a")
        self.assertTrue(sp.current.cancelled)

    def test_label_prefix_only_when_another_session_is_active(self):
        sp = speaker()
        sp.speak("alone", "bm_george", 1.0, session="a", label="claude-speak")
        self.assertEqual(sp.queue[-1].text, "alone")
        sp.speak("hello", "bm_george", 1.0, session="b", label="agent_avatar")
        self.assertEqual(sp.queue[-1].text, "agent avatar: hello")

    def test_stop_clears_everything(self):
        sp = speaker()
        sp.speak("one", "bm_george", 1.0, session="a")
        sp.speak("two", "bm_george", 1.0, session="b")
        jobs = list(sp.queue)
        sp.stop()
        self.assertEqual(sp.queue, [])
        self.assertTrue(all(j.cancelled for j in jobs))

    def test_unknown_voice_falls_back(self):
        sp = speaker()
        self.assertEqual(sp.resolve_voice("bm_george"), "bm_george")
        self.assertEqual(sp.resolve_voice("xx_nobody"), "af_heart")


if __name__ == "__main__":
    unittest.main()
