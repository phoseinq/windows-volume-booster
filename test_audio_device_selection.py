import unittest

from audio_device_selection import find_output_index


class DeviceSelectionTests(unittest.TestCase):
    def test_matches_windows_truncated_usb_output_name(self):
        devices = [
            {"name": "Speakers (Example USB SoundBa", "max_output_channels": 2},
            {"name": "Speakers (Steam Streaming Speakers)", "max_output_channels": 8},
        ]

        self.assertEqual(
            find_output_index(devices, "Speakers (Example USB SoundBar)"),
            0,
        )


    def test_prefers_usb_output_when_saved_name_is_missing(self):
        devices = [
            {"name": "Speakers (Steam Streaming Speakers)", "max_output_channels": 8},
            {"name": "Speakers (Example USB SoundBar)", "max_output_channels": 2},
        ]

        self.assertEqual(find_output_index(devices, "", prefer_usb=True), 1)

    def test_never_selects_cable_as_physical_output(self):
        devices = [
            {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_output_channels": 16},
            {"name": "Speakers (Example USB SoundBar)", "max_output_channels": 2},
        ]

        self.assertEqual(
            find_output_index(devices, "CABLE Input (VB-Audio Virtual Cable)", prefer_usb=True),
            1,
        )
