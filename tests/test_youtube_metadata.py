"""HTTP 降级解析须保留预定直播的频道信息；不触网。"""
import json
import unittest
from unittest.mock import MagicMock, patch

from sookit.core.youtube_utils import fetch_youtube_metadata


class YouTubeMetadataTests(unittest.TestCase):
    def fetch(self, details, microformat):
        player = {"videoDetails": details, "microformat": {"playerMicroformatRenderer": microformat}}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            "var ytInitialPlayerResponse = " + json.dumps(player) + ";").encode()
        with patch("sookit.core.youtube_utils.urllib.request.urlopen", return_value=response):
            return fetch_youtube_metadata("abcdefghijk")

    def test_upcoming_channel_comes_from_video_author(self):
        info = self.fetch({"title": "预定首播", "author": "测试频道"}, {
            "ownerChannelName": "备用频道",
            "liveBroadcastDetails": {"startTimestamp": "2099-11-06T11:00:00Z"}})
        self.assertEqual(info["channel"], "测试频道")
        self.assertEqual(info["title"], "预定首播")
        self.assertEqual(info["live_status"], "is_upcoming")
        self.assertIsInstance(info["scheduled_start_time"], int)

    def test_channel_falls_back_to_microformat(self):
        info = self.fetch({"title": "预定首播", "author": ""}, {"ownerChannelName": "备用频道"})
        self.assertEqual(info["channel"], "备用频道")

    def test_missing_channel_is_empty(self):
        info = self.fetch({"title": "预定首播"}, {})
        self.assertEqual(info["channel"], "")


if __name__ == "__main__":
    unittest.main()
