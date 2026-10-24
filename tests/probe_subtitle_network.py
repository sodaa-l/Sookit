"""可重复的平台字幕实测；所有下载位于临时目录，结束自动删除。

uv run python tests/probe_subtitle_network.py https://www.youtube.com/watch?v=Yb9H5vhWyHg
"""
import argparse
from pathlib import Path
import tempfile

from sookit.core.functions import Functions
from sookit.core.subtitle_utils import _read_cues, download_subtitles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--language', default='en')
    parser.add_argument('--source', choices=['platform', 'automatic'], default='platform')
    args = parser.parse_args()
    info = Functions.sniff_youtube(args.url)
    tracks = info['subtitle_tracks']
    print(f"formats={len(info['formats'])} subtitles={len(tracks)} comments={len(info['comment_tracks'])}")
    candidates = [t for t in tracks if t['source'] == args.source]
    if args.language == 'first':
        chosen = next((t for t in candidates if t['language'].endswith('-orig')),
                      next((t for t in candidates if t['language'] == 'en'),
                           candidates[0] if candidates else None))
    else:
        chosen = next((t for t in candidates if t['language'] == args.language), None)
    if chosen is None:
        available = [t['language'] for t in candidates]
        raise RuntimeError(f'没有匹配轨道: {args.source}:{args.language}; 可用语言: {available}')
    print(f"selected={chosen['key']} formats={chosen['formats']}")
    with tempfile.TemporaryDirectory(prefix='sookit-subtitle-platform-') as temporary:
        outputs = download_subtitles(args.url, temporary, [chosen], ['srt', 'txt', 'vtt'],
                                     workspace=temporary, log=print)
        if len(outputs) != 3:
            raise RuntimeError(f'产物数量错误: {len(outputs)}')
        for output in outputs:
            path = Path(output)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError('产物缺失或为空')
            print(f'PASS {path.suffix} bytes={path.stat().st_size}')
        by_suffix = {Path(output).suffix: Path(output) for output in outputs}
        srt_cues = _read_cues(by_suffix['.srt'])
        vtt_cues = _read_cues(by_suffix['.vtt'])
        if srt_cues != vtt_cues:
            raise RuntimeError('SRT 与 VTT 的时间轴或字幕内容不一致')
        expected_text = '\n\n'.join(payload for _, _, payload in srt_cues) + '\n'
        if by_suffix['.txt'].read_text(encoding='utf-8') != expected_text:
            raise RuntimeError('TXT 与字幕正文不一致')
        print(f'PASS content_consistency cues={len(srt_cues)}')


if __name__ == '__main__':
    main()
