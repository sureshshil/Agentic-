"""Regression tests for kanji_image.py - the PNG renderer kanji_drip.py
uses so the kanji itself shows up as a large legible glyph in Telegram
instead of small/thin plain Unicode text.

No live network calls: render_kanji_png() is tested with an injected
fallback font (PIL's built-in default) so it never has to fetch/cache
the real Noto Sans JP font, and _ensure_font()'s download path is tested
separately with requests.get mocked.

Run: python3 -m unittest tests.test_kanji_image -v   (from simple_agent/)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import kanji_image  # noqa: E402
from PIL import ImageFont  # noqa: E402


class RenderKanjiPngTest(unittest.TestCase):
    def test_produces_a_valid_png(self):
        png_bytes = kanji_image.render_kanji_png("場", font=ImageFont.load_default())
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_output_matches_canvas_size(self):
        from io import BytesIO
        from PIL import Image

        png_bytes = kanji_image.render_kanji_png("字", font=ImageFont.load_default())
        img = Image.open(BytesIO(png_bytes))
        self.assertEqual(img.size, (kanji_image.CANVAS_SIZE, kanji_image.CANVAS_SIZE))

    def test_does_not_hit_the_network_when_font_is_injected(self):
        with patch.object(kanji_image, "_ensure_font") as mock_ensure_font:
            kanji_image.render_kanji_png("字", font=ImageFont.load_default())
        mock_ensure_font.assert_not_called()


class EnsureFontTest(unittest.TestCase):
    def test_downloads_and_caches_on_first_call(self):
        with patch.object(kanji_image, "FONT_PATH") as mock_path, patch.object(
            kanji_image.requests, "get"
        ) as mock_get:
            mock_path.exists.return_value = False
            mock_path.with_suffix.return_value = MagicMock()
            mock_get.return_value = MagicMock(content=b"fake-font-bytes")

            kanji_image._ensure_font()

        mock_get.assert_called_once_with(kanji_image.FONT_URL, timeout=30)

    def test_reuses_cached_file_without_downloading(self):
        with patch.object(kanji_image, "FONT_PATH") as mock_path, patch.object(
            kanji_image.requests, "get"
        ) as mock_get:
            mock_path.exists.return_value = True
            kanji_image._ensure_font()
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
