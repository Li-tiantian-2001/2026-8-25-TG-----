import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tgbot.media_processor import MediaProcessError, MediaProcessor


class _Config:
    data = {"media": {"transcode_threads": 1, "max_height": 1080}}


def _meta(*, fmt="mov,mp4", codec="h264", pix_fmt="yuv420p", sar="1:1", rotation=0):
    return {
        "video": {
            "width": 1920,
            "height": 1080,
            "codec": codec,
            "pix_fmt": pix_fmt,
            "sar": sar,
            "rotation": rotation,
            "duration": 12.1,
        },
        "audio_codec": "aac",
        "format": fmt,
    }


class FakeProcessor(MediaProcessor):
    def __init__(self, probes):
        super().__init__(_Config())
        self.probes = iter(probes)
        self.commands = []

    def _probe(self, path):
        return next(self.probes)

    def _run(self, command):
        self.commands.append(command)
        Path(command[-1]).touch()


class MediaProcessorTests(unittest.TestCase):
    def test_compatible_mp4_is_not_processed(self):
        processor = FakeProcessor([_meta()])
        info = processor.prepare("video.mp4")
        self.assertEqual(info.action, "direct")
        self.assertEqual((info.width, info.height, info.duration), (1920, 1080, 13))
        self.assertEqual(processor.commands, [])

    def test_compatible_non_mp4_is_remuxed(self):
        with TemporaryDirectory() as temp:
            source = str(Path(temp) / "video.mkv")
            processor = FakeProcessor([_meta(fmt="matroska"), _meta()])
            info = processor.prepare(source)
            self.assertEqual(info.action, "remux")
            self.assertIn("copy", processor.commands[0])

    def test_rotation_requires_single_thread_transcode(self):
        with TemporaryDirectory() as temp:
            source = str(Path(temp) / "video.mp4")
            processor = FakeProcessor([_meta(rotation=90), _meta()])
            info = processor.prepare(source)
            self.assertEqual(info.action, "transcode")
            self.assertIn("libx264", processor.commands[0])
            threads_at = processor.commands[0].index("-threads")
            self.assertEqual(processor.commands[0][threads_at + 1], "1")

    def test_invalid_dimensions_are_rejected(self):
        meta = _meta()
        meta["video"]["width"] = 0
        processor = FakeProcessor([meta])
        with self.assertRaises(MediaProcessError):
            processor.prepare("broken.mp4")


if __name__ == "__main__":
    unittest.main()
