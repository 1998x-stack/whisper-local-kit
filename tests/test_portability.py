"""Portable roots: no implicit dependency on author's mounted storage."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.extensions import allowed_roots, safe_directory
from fastapi import HTTPException


class DeploymentConfigTests(unittest.TestCase):
    def test_batch_roots_default_are_narrow_and_portable(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            (base / "Music").mkdir()
            (base / "Downloads").mkdir()
            with patch("app.extensions.Path.home", return_value=base), \
                 patch.dict(os.environ, {"WHISPER_BATCH_ROOTS": ""}):
                self.assertEqual(set(allowed_roots()),
                                 {(base / "Music").resolve(), (base / "Downloads").resolve()})
                with self.assertRaises(HTTPException):
                    safe_directory(str(base))

    def test_explicit_batch_root_limits_paths(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            allowed = path / 'allowed'
            denied = path / 'denied'
            allowed.mkdir()
            denied.mkdir()
            with patch.dict(os.environ, {"WHISPER_BATCH_ROOTS": str(allowed)}):
                self.assertEqual(safe_directory(str(allowed)), allowed.resolve())
                with self.assertRaises(HTTPException):
                    safe_directory(str(denied))


if __name__ == '__main__':
    unittest.main()
