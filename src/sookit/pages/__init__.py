"""
pages 包
应用程序页面
"""
from sookit.pages.base import PageBase
from sookit.pages.youtube_page import YouTubePage
from sookit.pages.subtitle_page import SubtitlePage
from sookit.pages.replace_audio_page import ReplaceAudioPage
from sookit.pages.extract_audio_page import ExtractAudioPage
from sookit.pages.monitor_page import MonitorPage
from sookit.pages.settings_page import SettingsPage
from sookit.pages.queue_page import QueuePage

__all__ = [
    'PageBase',
    'YouTubePage',
    'SubtitlePage',
    'ReplaceAudioPage',
    'ExtractAudioPage',
    'MonitorPage',
    'QueuePage',
    'SettingsPage',
]