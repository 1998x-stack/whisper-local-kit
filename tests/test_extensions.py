import tempfile
import unittest
from pathlib import Path
from app.extensions import atomic_text, list_mp3, safe_output, simple_segments
from app.subtitles import to_srt

class ExtensionUnitTests(unittest.TestCase):
    def test_offsets(self):
        result = {'segments': [{'start': 0.3, 'end': 1.3, 'text': ' 第二句话 '} ]}
        segments = simple_segments(result, offset=61.0, duration=2.0)
        self.assertEqual(segments[0]['start'], 61.3)
        self.assertIn('00:01:01,300 --> 00:01:02,300', to_srt(segments))

    def test_batch_matching_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'A.mp3').write_bytes(b'a')
            (root/'nested').mkdir()
            (root/'nested'/'B.MP3').write_bytes(b'b')
            self.assertEqual(len(list_mp3(root,False)),1)
            self.assertEqual(len(list_mp3(root,True)),2)
            out = root / 'A.srt'
            atomic_text(out,'original')
            self.assertEqual(out.read_text(),'original')
            self.assertTrue(safe_output(out,root))

if __name__ == '__main__': unittest.main()
