from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mujina_assist.app import MujinaAssistApp


class AppTest(unittest.TestCase):
    def test_missing_devices_for_serial_mode_only_requires_serial_can(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": True,
                    "/dev/usb_can": True,
                    "/dev/input/js0": False,
                    "can0": False,
                },
            ):
                missing = app._missing_devices_for_can_mode(
                    "serial",
                    include_imu=True,
                    include_joy=False,
                )

            self.assertEqual(missing, [])

    def test_select_can_mode_prefers_available_requested_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch(
                "mujina_assist.app.detect_real_devices",
                return_value={
                    "/dev/rt_usb_imu": False,
                    "/dev/usb_can": True,
                    "/dev/input/js0": False,
                    "can0": True,
                },
            ):
                self.assertEqual(app._select_can_mode("serial"), "serial")
                self.assertEqual(app._select_can_mode("net"), "net")

    def test_ask_ids_rejects_invalid_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch("mujina_assist.app.ask_text", return_value="1 a 2"):
                self.assertEqual(app._ask_ids(), [])

    def test_ask_ids_accepts_commas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = MujinaAssistApp(Path(tmp))
            with patch("mujina_assist.app.ask_text", return_value="1,2,3"), patch(
                "mujina_assist.app.ask_yes_no",
                return_value=True,
            ):
                self.assertEqual(app._ask_ids(), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
