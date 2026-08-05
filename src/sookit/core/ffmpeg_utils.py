"""
core/ffmpeg_utils.py
FFmpeg / yt-dlp / aria2c 路径、检查与执行工具
"""

import os
import sys
import subprocess
import re
from shutil import which

from sookit.paths import get_tools_dir


# ---------- FFmpeg / ffprobe 路径 ----------

def get_ffmpeg_path():
    """获取内嵌 ffmpeg.exe 路径"""
    return str(get_tools_dir() / 'ffmpeg' / 'ffmpeg.exe')


def get_ffprobe_path():
    """获取内嵌 ffprobe.exe 路径"""
    return str(get_tools_dir() / 'ffmpeg' / 'ffprobe.exe')


def check_ffmpeg():
    """检查 ffmpeg 是否可用（优先内嵌，回退系统 PATH）"""
    if os.path.exists(get_ffmpeg_path()) and os.path.exists(get_ffprobe_path()):
        return True
    return which("ffmpeg") is not None and which("ffprobe") is not None


# ---------- 视频时长 / 格式化 ----------

def get_video_duration(video_path, log=None):
    try:
        ffprobe = get_ffprobe_path()
        if not os.path.exists(ffprobe):
            ffprobe = "ffprobe"
        cmd = [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        if log:
            log(f"获取视频时长失败: {e}")
    return None


def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_filesize(bytes_val):
    if not bytes_val:
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}TB"


# ---------- FFmpeg / yt-dlp 命令执行 ----------

def run_ffmpeg(cmd_list, log_callback, process_ref=None):
    try:
        process = subprocess.Popen(
            cmd_list, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
        )
        if process_ref is not None:
            process_ref.append(process)
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line and log_callback:
                log_callback(line.strip())
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd_list)
        return True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        raise RuntimeError(f"ffmpeg 执行失败: {e}")


def run_ytdlp(cmd_list, log_callback, process_ref=None, on_process_created=None):
    try:
        # 创建临时文件用于 --print-to-file 输出最终路径
        import tempfile
        tmp_path = tempfile.mktemp(suffix='.ytdlp_path')
        cmd_list = cmd_list + ['--print-to-file', 'after_move:filepath', tmp_path]

        # 继承当前环境并设置 SSL 证书路径，确保系统 yt-dlp 能正常建立 HTTPS 连接
        env = os.environ.copy()
        try:
            import certifi
            env['SSL_CERT_FILE'] = certifi.where()
        except ImportError:
            pass

        process = subprocess.Popen(
            cmd_list, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            env=env
        )
        if process_ref is not None:
            process_ref.append(process)
        if on_process_created is not None:
            on_process_created(process)
        _last_dest = None
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line and log_callback:
                log_callback(line.strip())
            # 从输出中提取目标文件路径（备选方案）
            if line:
                for prefix in ('[download] Destination: ',
                               '[Merger] Merging formats into ',
                               '[ExtractAudio] Destination: '):
                    if prefix in line:
                        raw = line[line.index(prefix) + len(prefix):].strip().strip('"')
                        if raw:
                            _last_dest = raw
        # 读取 --print-to-file 输出的最终路径（最可靠）
        # yt-dlp 输出的是 UTF-8，用 GBK 打开含日/韩字符的路径会报错
        output_path = None
        if os.path.exists(tmp_path):
            try:
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content and os.path.isfile(content):
                    output_path = content
            except Exception:
                pass
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        # 回退：使用日志解析的路径
        if not output_path and _last_dest and os.path.isfile(_last_dest):
            output_path = _last_dest
        if process.returncode != 0:
            # yt-dlp 有时在文件已下载成功后仍返回非零退出码（如 ffmpeg 合并问题）
            # 检查最终输出文件是否存在：存在则视为成功，不存在才报错
            if output_path and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                if log_callback:
                    log_callback(f"⚠ yt-dlp 退出码: {process.returncode}（但目标文件存在，视为成功）")
            else:
                raise RuntimeError(
                    f"yt-dlp 退出码: {process.returncode}，目标文件不存在或不完整")
        return output_path if output_path else True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        raise RuntimeError(f"yt-dlp 执行失败: {e}")


# ---------- aria2c ----------

def get_aria2c_path():
    """获取 aria2c 可执行文件路径"""
    return str(get_tools_dir() / 'aria2c' / 'aria2c.exe')


# ---------- 视频缩略图截取 ----------

def extract_video_frame(video_path):
    """
    从视频中快速截取一帧作为缩略图（取 30% 位置的 I 帧）。
    返回二进制图片数据 (JPEG)，失败返回 None。
    """
    try:
        # 获取视频时长
        duration = get_video_duration(video_path)
        if duration and duration > 0:
            seek_pos = duration * 0.3
        else:
            seek_pos = 5.0  # 默认取第 5 秒

        ffmpeg = get_ffmpeg_path()
        if not os.path.exists(ffmpeg):
            ffmpeg = "ffmpeg"

        cmd = [
            ffmpeg,
            '-ss', str(seek_pos),
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2',
            '-f', 'image2pipe',
            '-v', 'error',
            '-'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    return None


def check_aria2c():
    """检查 aria2c 是否可用"""
    aria2c_path = get_aria2c_path()
    if not os.path.exists(aria2c_path):
        return False, "aria2c.exe 不存在"
    
    try:
        result = subprocess.run([aria2c_path, '--version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('aria2 version'):
                    version = line.split(' ')[2]
                    return True, version
            return True, "未知版本"
        else:
            return False, "无法执行 aria2c"
    except Exception as e:
        return False, f"检查失败: {e}"
