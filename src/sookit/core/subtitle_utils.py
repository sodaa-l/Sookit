"""Subtitle discovery and queue-friendly downloads, with one fetch per track.

``platform`` means the extractor's ordinary subtitle bucket, not a promise of
human authorship. ``bucket`` retains the extractor origin when known AI captions
are found in that ordinary bucket (for example Bilibili).
"""

import hashlib
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

from sookit.core.ffmpeg_utils import get_ffmpeg_path, run_ffmpeg, run_ytdlp
from sookit.core.ytdlp_utils import build_ytdlp_cmd


_AI_NAME = re.compile(
    r'\bAI\b|\bauto(?:matic(?:ally)?|generated)?[ -](?:generated|captions?|subtitles?)'
    r'|自动(?:生成|字幕|翻译)|机器(?:生成|翻译)|AI\s*生成', re.IGNORECASE)
_AUTO_GENERATED_NAME = re.compile(
    r'\b(?:auto(?:matic)?[- ]generated|ai[- ]generated|original)\b'
    r'|自动(?:生成|字幕)|机器生成|AI\s*生成', re.IGNORECASE)
_AUTO_TRANSLATED_NAME = re.compile(
    r'\b(?:auto[- ]?translated|translated|translation|from\s+[\w-]+)\b'
    r'|自动翻译|机器翻译', re.IGNORECASE)
_ORIGINAL_NAME = re.compile(r'\(\s*original\s*\)', re.IGNORECASE)
_FROM_LANGUAGE_NAME = re.compile(r'\bfrom\s+(.+?)\s*$', re.IGNORECASE)
_EXT = re.compile(r'^[a-z0-9]+$')
_TIMING = re.compile(
    r'^(?P<start>(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+'
    r'(?P<end>(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})(?:\s+.*)?$')


def _usable_subtitle_entries(entries):
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)
            and (entry.get('url') or entry.get('data'))
            and _EXT.fullmatch(str(entry.get('ext', '')).lower())]


def _automatic_kind(language, entries, has_original=False):
    """Return generated/translated classification from explicit extractor metadata."""
    names = [str(entry.get('name') or '') for entry in entries]
    is_original = (language.casefold().endswith('-orig')
                   or any(_ORIGINAL_NAME.search(name) for name in names))
    if has_original:
        return 'generated' if is_original else 'translated'
    if is_original or any(_AUTO_GENERATED_NAME.search(name) for name in names):
        return 'generated'
    if any(_AUTO_TRANSLATED_NAME.search(name) for name in names):
        return 'translated'
    for entry in entries:
        url = entry.get('url')
        if isinstance(url, str):
            try:
                if parse_qs(urlsplit(url).query).get('tlang'):
                    return 'translated'
            except ValueError:
                continue
    return 'unknown'


def _normalized_language_tag(value):
    if not isinstance(value, str):
        return ''
    value = value.strip().replace('_', '-').casefold()
    return '' if value in ('und', 'unknown', 'none') else value


def _normalized_language_name(value):
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()


def _video_source_language(info, automatic_captions):
    for field in ('language', 'original_language'):
        language = _normalized_language_tag(info.get(field))
        if language:
            return language
    if isinstance(automatic_captions, dict):
        for language, entries in automatic_captions.items():
            if not isinstance(language, str):
                continue
            language_tag = _normalized_language_tag(language)
            usable = _usable_subtitle_entries(entries)
            names = [str(entry.get('name') or '') for entry in usable]
            if usable and language_tag.endswith('-orig'):
                return language_tag[:-5]
            if usable and any(_ORIGINAL_NAME.search(name) for name in names):
                return language_tag
    return ''


def _translation_matches_source(language, entries, source_language, source_names):
    if not source_language:
        return True
    candidates = [language]
    for entry in entries:
        url = entry.get('url')
        if not isinstance(url, str):
            continue
        try:
            candidates.extend(parse_qs(urlsplit(url).query).get('tlang', []))
        except ValueError:
            continue
    source_suffix = f'-{source_language}'
    for candidate in candidates:
        tag = _normalized_language_tag(candidate)
        if len(tag) > len(source_suffix) and tag.endswith(source_suffix):
            return True
    for entry in entries:
        match = _FROM_LANGUAGE_NAME.search(str(entry.get('name') or ''))
        if match and _normalized_language_name(match.group(1)) in source_names:
            return True
    return False


def build_subtitle_tracks(info) -> dict:
    """Return selectable captions and raw comment tracks from yt-dlp metadata."""
    result = {'subtitle_tracks': [], 'comment_tracks': []}
    automatic_captions = info.get('automatic_captions') or {}
    source_language = _video_source_language(info, automatic_captions)
    has_original = False
    original_names = set()
    if isinstance(automatic_captions, dict):
        for language, entries in automatic_captions.items():
            if not isinstance(language, str) or not language:
                continue
            usable = _usable_subtitle_entries(entries)
            names = [str(entry.get('name') or '') for entry in usable]
            is_original = (language.casefold().endswith('-orig')
                           or any(_ORIGINAL_NAME.search(name) for name in names))
            if usable and is_original:
                has_original = True
                for name in names:
                    if _ORIGINAL_NAME.search(name):
                        name = _ORIGINAL_NAME.sub('', name)
                        name = re.sub(r'\s+original\s*$', '', name, flags=re.IGNORECASE)
                        normalized_name = _normalized_language_name(name)
                        if normalized_name:
                            original_names.add(normalized_name)

    for bucket in ('subtitles', 'automatic_captions'):
        for language, entries in (info.get(bucket) or {}).items():
            if not isinstance(language, str) or not language:
                continue
            usable = _usable_subtitle_entries(entries)
            if not usable:
                continue
            formats = list(dict.fromkeys(str(entry['ext']).lower() for entry in usable))
            name = next((str(entry['name']) for entry in usable if entry.get('name')), language)
            comment = language.lower() in ('live_chat', 'danmaku')
            automatic = (bucket == 'automatic_captions' or language.lower().startswith('ai-')
                         or any(_AI_NAME.search(str(entry.get('name') or '')) for entry in usable))
            source = 'comment' if comment else 'automatic' if automatic else 'platform'
            track = {'key': f'{bucket}:{language}', 'language': language, 'name': name,
                     'source': source, 'formats': formats, 'bucket': bucket,
                     'title': str(info.get('title') or info.get('id') or 'subtitles')}
            if source == 'automatic':
                automatic_kind = _automatic_kind(
                    language, usable, has_original if bucket == 'automatic_captions' else False)
                if (bucket == 'automatic_captions' and automatic_kind == 'translated'
                        and not _translation_matches_source(
                            language, usable, source_language, original_names)):
                    continue
                track['automatic_kind'] = automatic_kind
            result['comment_tracks' if comment else 'subtitle_tracks'].append(track)
    return result


def _contained_file(raw, root):
    if not isinstance(raw, str) or not raw:
        raise RuntimeError('字幕清单缺少文件路径')
    path = Path(raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('字幕源文件必须位于独占 workspace 内且存在非空')
    return path


def _read_manifest(manifest, language, available, root):
    if not manifest.is_file():
        raise RuntimeError('yt-dlp 未返回字幕清单，所选字幕可能已不可用')
    try:
        lines = [line for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError('需要单个视频的字幕清单')
        data = json.loads(lines[0])
        if not isinstance(data, dict) or set(data) != {language}:
            raise ValueError('字幕语言不完整或不匹配')
        entry = data[language]
        ext = entry['ext'].lower()
        if ext not in available or not _EXT.fullmatch(ext):
            raise ValueError('字幕实际格式与嗅探结果不匹配')
        path = _contained_file(entry['filepath'], root)
        if path.suffix.lower() != f'.{ext}':
            raise ValueError('字幕路径扩展名与实际格式不匹配')
        return path, ext
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise RuntimeError(f'字幕清单无效: {error}') from error


def _fetch_track(url, track, root, remote, log, on_process_created, comment=False):
    language = track.get('language')
    bucket = track.get('bucket') or ('automatic_captions' if track.get('source') == 'automatic'
                                     else 'subtitles')
    available = track.get('formats')
    if (not isinstance(language, str) or not language or ',' in language
            or bucket not in ('subtitles', 'automatic_captions')
            or not isinstance(available, list) or not available
            or any(not isinstance(ext, str) or not _EXT.fullmatch(ext) for ext in available)):
        raise ValueError('无效的字幕轨道')
    if comment != (track.get('source') == 'comment'):
        raise ValueError('字幕与评论轨道不能混用')
    if track.get('source') not in ('platform', 'automatic', 'comment'):
        raise ValueError('无效的字幕来源')
    if track.get('source') == 'platform' and bucket != 'subtitles':
        raise ValueError('平台字幕来源与原始桶不匹配')
    preferences = ('json', 'xml') if comment else ('vtt', 'srt', 'ass', 'ssa', 'ttml', 'srv3', 'srv2', 'srv1', 'json3')
    chosen = [ext for ext in preferences if ext in available]
    if not chosen:
        chosen = available if not comment else []
    if not chosen:
        raise ValueError('评论轨道没有可用的原始 JSON/XML 格式')
    manifest = root / 'requested_subtitles.jsonl'
    # Explicitly disable the other bucket: yt-dlp gives ordinary subtitles priority
    # if both switches are enabled and both buckets contain the same language tag.
    switches = (('--write-auto-subs', '--no-write-subs') if bucket == 'automatic_captions'
                else ('--write-subs', '--no-write-auto-subs'))
    cmd = build_ytdlp_cmd(
        '--ignore-config', '--no-playlist', '--skip-download', '--no-simulate', '--newline',
        *switches, '--sub-langs', '^' + re.escape(language) + '$',
        '--sub-format', '/'.join(chosen), '-o', str(root / 'source.%(ext)s'))
    if remote:
        cmd.extend(['--remote-components', 'ejs:github'])
    cmd.append(url)
    run_ytdlp(cmd, log, on_process_created=on_process_created,
              diagnostics=True, subtitle_manifest=str(manifest))
    path, ext = _read_manifest(manifest, language, available, root)
    if comment and ext not in ('json', 'xml'):
        raise RuntimeError('评论必须保留原始 JSON/XML 格式')
    return path, ext


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'br':
            self.parts.append('\n')


def _plain_text(text):
    # WebVTT inline timestamp tags are not HTML tags (they begin with digits).
    text = re.sub(r'<(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}>', '', text)
    parser = _PlainText()
    parser.feed(text)
    parser.close()
    return ''.join(parser.parts).strip()


def _milliseconds(timestamp):
    parts = timestamp.replace(',', '.').split(':')
    if len(parts) == 2:
        parts.insert(0, '0')
    hours, minutes, seconds = parts
    seconds, millis = seconds.split('.')
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise RuntimeError('字幕时间轴无效')
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _read_cues(path):
    text = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n[ \t]*\n', text.strip()):
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0]
        if (first in ('WEBVTT', 'NOTE', 'STYLE', 'REGION')
                or first.startswith(('WEBVTT ', 'WEBVTT\t', 'NOTE ', 'NOTE\t'))):
            continue
        timing_index = next((i for i, line in enumerate(lines[:2]) if '-->' in line), None)
        if timing_index is None:
            raise RuntimeError('字幕包含无法识别的段落')
        match = _TIMING.fullmatch(lines[timing_index].strip())
        if not match:
            raise RuntimeError('字幕时间轴无效')
        start, end = (_milliseconds(match[name]) for name in ('start', 'end'))
        if end < start:
            raise RuntimeError('字幕结束时间早于开始时间')
        payload = _plain_text('\n'.join(lines[timing_index + 1:]))
        # Empty display cues carry no text; preserve all actual paragraphs, including repeats.
        if payload:
            cues.append((start, end, payload))
    if not cues:
        raise RuntimeError('字幕没有可导出的有效文本')
    return cues


def _timestamp(value, separator):
    seconds, millis = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}'


def _export(source, ext, formats, target_base, log, on_process_created):
    if ext not in ('srt', 'vtt'):
        if log:
            log(f'正在本地转换字幕源格式: {ext}')
        normalized = source.parent / 'normalized.vtt'
        ffmpeg = get_ffmpeg_path()
        if not os.path.isfile(ffmpeg):
            ffmpeg = 'ffmpeg'
        run_ffmpeg([ffmpeg, '-nostdin', '-y', '-i', str(source), str(normalized)],
                   log, on_process_created=on_process_created)
        source = _contained_file(str(normalized), source.parent)
    if log:
        log('正在解析字幕文本')
    cues = _read_cues(source)
    outputs = []
    for fmt in formats:
        if log:
            log(f'正在导出字幕: {fmt}')
        target = target_base.with_suffix('.' + fmt)
        if fmt == 'txt':
            text = '\n\n'.join(payload for _, _, payload in cues) + '\n'
        else:
            separator = ',' if fmt == 'srt' else '.'
            blocks = []
            for i, (start, end, payload) in enumerate(cues, 1):
                timing = f'{_timestamp(start, separator)} --> {_timestamp(end, separator)}'
                blocks.append((f'{i}\n' if fmt == 'srt' else '') + timing + '\n'
                              + escape(payload, quote=False))
            text = ('WEBVTT\n\n' if fmt == 'vtt' else '') + '\n\n'.join(blocks) + '\n'
        target.write_text(text, encoding='utf-8')
        outputs.append(target)
    return outputs


def _basename(track):
    key = str(track.get('key') or f"{track.get('bucket')}:{track['language']}")
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()[:10]
    # Keep dots out of the stem: Path.with_suffix must only replace our final extension.
    label = re.sub(r'[^\w\-]+', '_', str(track.get('title') or 'subtitles'), flags=re.UNICODE)[:65].strip('_')
    language = re.sub(r'[^\w\-]+', '_', track['language'], flags=re.UNICODE)[:35]
    return f'{label or "subtitles"}_{track["source"]}_{language}_{digest}'


def _download(url, output_dir, tracks, formats, remote, workspace, log,
              on_process_created, on_progress, comment):
    if not isinstance(url, str) or not url.strip() or not tracks:
        raise ValueError('下载需要嗅探链接及至少一条轨道')
    if log:
        log('正在准备评论下载' if comment else '正在准备字幕下载')
    output = Path(output_dir).resolve()
    if workspace is None or workspace == '':
        # The queue uses an empty string when no workspace could be created.
        output.mkdir(parents=True, exist_ok=True)
        root = output
    else:
        # The queue owns the workspace lifecycle; never recreate one deleted by cancellation.
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('workspace 必须为任务独占目录')
    with tempfile.TemporaryDirectory(prefix='._subtitle_', dir=root) as temporary:
        staging = Path(temporary)
        exports = staging / 'exports'
        exports.mkdir()
        files = []
        seen = set()
        for index, track in enumerate(tracks):
            if not isinstance(track, dict):
                raise ValueError('无效的字幕轨道')
            identity = (track.get('bucket'), track.get('source'), track.get('language'))
            if identity in seen:
                raise ValueError('不能重复选择同一字幕轨道')
            seen.add(identity)
            if log:
                log(f'正在下载{"评论" if comment else "字幕"}轨道 '
                    f'({index + 1}/{len(tracks)}): {track.get("name") or track.get("language")}')
            source_root = staging / f'track_{index}'
            source_root.mkdir()
            source, ext = _fetch_track(url, track, source_root, remote, log,
                                       on_process_created, comment)
            target = exports / _basename(track)
            if comment:
                if log:
                    log(f'正在导出原始评论: {ext}')
                target = target.with_suffix('.' + ext)
                shutil.copyfile(source, target)
                files.append(target)
            else:
                files.extend(_export(source, ext, formats, target, log, on_process_created))
            if on_progress:
                on_progress((index + 1) * 100 / len(tracks))
        # Publish only the complete export set. Queue finalization receives this exact list;
        # original sources and the manifest remain inside the temporary subtree and vanish.
        if log:
            log('正在保存评论产物' if comment else '正在保存字幕产物')
        destinations = []
        for source in files:
            destination = root / source.name
            counter = 1
            while destination.exists():
                destination = root / f'{source.stem}_{counter}{source.suffix}'
                counter += 1
            os.replace(source, destination)
            destinations.append(str(destination))
        return destinations


def download_subtitles(url, output_dir, tracks, formats, remote=False, workspace=None,
                       log=None, on_process_created=None, on_progress=None) -> list[str]:
    """Fetch each selected track once, then export SRT/TXT/VTT locally.

    Pass the sniffed URL and complete track dictionaries through TaskType.YTDLP's
    kwargs channel. Returned paths are the only files queue finalization may move.
    """
    if isinstance(formats, str) or not formats:
        raise ValueError('请选择字幕导出格式')
    formats = list(dict.fromkeys(formats))
    if any(fmt not in ('srt', 'txt', 'vtt') for fmt in formats):
        raise ValueError('字幕导出仅支持 srt/txt/vtt')
    return _download(url, output_dir, tracks, formats, remote, workspace, log,
                     on_process_created, on_progress, False)


def download_comments(url, output_dir, track, remote=False, workspace=None,
                      log=None, on_process_created=None, on_progress=None) -> list[str]:
    """Download one raw live-chat/danmaku JSON/XML track without text conversion."""
    return _download(url, output_dir, [track], [], remote, workspace, log,
                     on_process_created, on_progress, True)
