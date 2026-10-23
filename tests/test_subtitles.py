import unittest
from app.subtitles import timestamp, to_srt, to_vtt


class SubtitleTests(unittest.TestCase):
    def test_timestamp(self):
        self.assertEqual(timestamp(61.234), "00:01:01,234")
        self.assertEqual(timestamp(61.234, True), "00:01:01.234")

    def test_srt(self):
        segments = [{"start": 0, "end": 1.5, "text": "你好"}]
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\n你好", to_srt(segments))
        self.assertIn("WEBVTT\n\n00:00:00.000 --> 00:00:01.500", to_vtt(segments))


if __name__ == "__main__":
    unittest.main()
