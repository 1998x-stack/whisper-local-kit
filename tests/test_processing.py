"""Decoding quality contract; never loads a real model."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.processing import decode_file, simple_segments, transcript_text


class FakeModel:
    def __init__(self):
        self.options = None

    def transcribe(self, filename, **options):
        self.options = options
        return {"text": "hello", "segments": [{"start": 0, "end": 2, "text": "hello"}]}


class ProcessingTests(unittest.TestCase):
    def test_chinese_transcription_uses_simplified_characters(self):
        model = FakeModel()
        traditional = {
            "language": "zh",
            "text": "因為我聽過胡星樹的課",
            "segments": [{"start": 0, "end": 2, "text": "因為我聽過胡星樹的課"}],
        }
        with patch.object(model, "transcribe", return_value=traditional):
            result = decode_file(model, Path("sample.mp3"), "zh")
        self.assertEqual(result["text"], "因为我听过胡星树的课")
        self.assertEqual(result["segments"][0]["text"], "因为我听过胡星树的课")

    def test_greedy_and_retry_only_for_offline(self):
        model = FakeModel()
        decode_file(model, Path("a.wav"), "zh", live=False)
        self.assertIsNone(model.options["beam_size"])
        self.assertEqual(model.options["temperature"], (0.0, 0.2, 0.4))
        self.assertFalse(model.options["condition_on_previous_text"])
        decode_file(model, Path("a.wav"), "zh", live=True)
        self.assertEqual(model.options["temperature"], 0.0)

    def test_conservative_filter_and_scores(self):
        result = {"text": "speech", "segments": [
            {"start": 0.1, "end": 1, "text": "speech", "no_speech_prob": 0.25,
             "avg_logprob": -0.5, "compression_ratio": 1.2},
            {"start": 1, "end": 2, "text": "phantom", "no_speech_prob": 0.94,
             "avg_logprob": -1.8},
            {"start": 2, "end": 3, "text": "valid", "no_speech_prob": 0.99,
             "avg_logprob": -0.2},
        ]}
        with patch.dict(os.environ, {"WHISPER_FILTER_LOW_CONFIDENCE": "1"}):
            segments = simple_segments(result, offset=20, duration=3)
        self.assertEqual([s["text"] for s in segments], ["speech", "valid"])
        self.assertAlmostEqual(segments[0]["start"], 20.1)
        self.assertEqual(segments[0]["avg_logprob"], -0.5)
        self.assertEqual(transcript_text(result, segments), "speech valid")
        self.assertEqual(len(simple_segments(result, filter_low_confidence=False)), 3)
        with patch.dict(os.environ, {"WHISPER_FILTER_LOW_CONFIDENCE": "0"}):
            self.assertEqual(len(simple_segments(result)), 3)

    def test_declared_clip_duration_clamps_timestamps(self):
        result = {"segments": [{"start": 1.0, "end": 12.0, "text": "x"}]}
        self.assertEqual(simple_segments(result, offset=61, duration=2)[0]["end"], 63.0)
        self.assertEqual(simple_segments(result, duration=.5), [])


if __name__ == "__main__":
    unittest.main()
