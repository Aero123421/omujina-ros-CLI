from __future__ import annotations

import unittest
from unittest.mock import patch

from mujina_assist.ui import ask_yes_no


class UiTest(unittest.TestCase):
    def test_ask_yes_no_accepts_japanese_yes(self) -> None:
        with patch("builtins.input", return_value="はい"):
            self.assertTrue(ask_yes_no("続けますか？"))

    def test_ask_yes_no_accepts_japanese_no(self) -> None:
        with patch("builtins.input", return_value="いいえ"):
            self.assertFalse(ask_yes_no("続けますか？", default=True))


if __name__ == "__main__":
    unittest.main()
