"""
pages 包
应用程序页面
"""
from sookit.pages.base import PageBase
from sookit.pages.merge_page import MergePage
from sookit.pages.img2vid_page import Img2VidPage
from sookit.pages.m3u8_page import M3U8Page
from sookit.pages.xspace_page import XSpacePage
from sookit.pages.youtube_page import YouTubePage
from sookit.pages.subtitle_page import SubtitlePage
from sookit.pages.cut_page import CutPage
from sookit.pages.replace_audio_page import ReplaceAudioPage
from sookit.pages.extract_audio_page import ExtractAudioPage
from sookit.pages.frame_page import FramePage
from sookit.pages.monitor_page import MonitorPage
from sookit.pages.settings_page import SettingsPage
from sookit.pages.queue_page import QueuePage

__all__ = [
    'PageBase',
    'MergePage',
    'Img2VidPage',
    'M3U8Page',
    'XSpacePage',
    'YouTubePage',
    'SubtitlePage',
    'CutPage',
    'ReplaceAudioPage',
    'ExtractAudioPage',
    'FramePage',
    'MonitorPage',
    'QueuePage',
    'SettingsPage',
]