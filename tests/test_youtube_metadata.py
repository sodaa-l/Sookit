"""HTTP 降级解析须保留预定直播的频道信息；不触网。"""
import json
import unittest
from unittest.mock import MagicMock, patch

from sookit.core.youtube_utils import fetch_youtube_metadata


class YouTubeMetadataTests(unittest.TestCase):
    def fetch(self, details, microformat, playability=None):
        player = {"videoDetails": details, "microformat": {"playerMicroformatRenderer": microformat},
                  "playabilityStatus": playability or {}}
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

    def test_past_start_without_end_keeps_waiting(self):
        for start in ('2000-01-01T00:00:00Z', 'invalid'):
            info = self.fetch({'title': '延迟开播'}, {'liveBroadcastDetails': {'startTimestamp': start}})
            self.assertEqual(info['live_status'], 'is_upcoming')
            self.assertFalse(info['is_live'])

    def test_explicit_upcoming_without_microformat_keeps_waiting(self):
        for details, playability in (({'isUpcoming': True}, {}),
                                     ({}, {'status': 'LIVE_STREAM_OFFLINE'})):
            info = self.fetch(dict(title='预约活动', **details), {}, playability)
            self.assertEqual(info['live_status'], 'is_upcoming')

    def test_live_ended_regular_and_incomplete_live_states(self):
        for details, live, status in (
                ({}, {'isLiveNow': True}, 'is_live'),
                ({'isLive': True}, {}, 'is_live'),
                ({}, {'startTimestamp': '2000-01-01T00:00:00Z',
                      'endTimestamp': '2000-01-01T01:00:00Z'}, 'was_live'),
                ({'isLiveContent': True}, {}, 'unknown'),
                ({'isLiveContent': False}, {}, 'not_live')):
            with self.subTest(status=status, details=details):
                info = self.fetch(dict(title='测试', **details), {'liveBroadcastDetails': live})
                self.assertEqual(info['live_status'], status)
                self.assertEqual(info['is_live'], status == 'is_live')
        ended = self.fetch({'title': '已结束'}, {'liveBroadcastDetails': {'endTimestamp': '2000-01-01T01:00:00Z'}},
                           {'status': 'LIVE_STREAM_OFFLINE'})
        self.assertEqual(ended['live_status'], 'was_live')

    def test_channel_falls_back_to_microformat(self):
        info = self.fetch({"title": "预定首播", "author": ""}, {"ownerChannelName": "备用频道"})
        self.assertEqual(info["channel"], "备用频道")

    def test_missing_channel_is_empty(self):
        info = self.fetch({"title": "预定首播"}, {})
        self.assertEqual(info["channel"], "")


if __name__ == "__main__":
    unittest.main()
