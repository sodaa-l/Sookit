"""
pages 包
应用程序页面
"""
from sookit.pages.base import PageBase
from sookit.pages.merge_page import MergePage
from sookit.pages.m3u8_page import M3U8Page
from sookit.pages.xspace_page import XSpacePage
from sookit.pages.youtube_page import YouTubePage
from sookit.pages.subtitle_page import SubtitlePage
from sookit.pages.replace_audio_page import ReplaceAudioPage
from sookit.pages.extract_audio_page import ExtractAudioPage
from sookit.pages.monitor_page import MonitorPage
from sookit.pages.settings_page import SettingsPage
from sookit.pages.queue_page import QueuePage

__all__ = [
    'PageBase',
    'MergePage',
    'M3U8Page',
    'XSpacePage',
    'YouTubePage',
    'SubtitlePage',
    'ReplaceAudioPage',
    'ExtractAudioPage',
    'MonitorPage',
    'QueuePage',
    'SettingsPage',
]