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

# Windows 下抑制控制台子程序（yt-dlp/ffmpeg/aria2c）弹出黑色命令行窗口。
# CREATE_NO_WINDOW = 0x08000000，与 CREATE_NEW_PROCESS_GROUP 组合使用。
if sys.platform == "win32":
    _PROC_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
else:
    _PROC_FLAGS = 0


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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                creationflags=_PROC_FLAGS)
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

def run_ffmpeg(cmd_list, log_callback, process_ref=None, on_process_created=None):
    try:
        process = subprocess.Popen(
            cmd_list, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='replace',
            creationflags=_PROC_FLAGS
        )
        if process_ref is not None:
            process_ref.append(process)
        if on_process_created is not None:
            on_process_created(process)
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


def _build_ytdlp_error(returncode, error_lines):
    """根据 yt-dlp 的 ERROR 输出构造可读的失败信息。

    若检测到「Sign in to confirm」（YouTube 未登录/被反爬拦截），
    给出明确提示；否则返回通用信息 + 摘要一条 ERROR 行。
    """
    joined = " ".join(error_lines).lower()
    if "sign in to confirm" in joined:
        return (
            "YouTube 要求登录确认（未登录/被反爬拦截）：建议导入 Cookies 后重试。"
            f"yt-dlp 退出码: {returncode}"
        )
    detail = ""
    if error_lines:
        # 取第一条 ERROR 行作为摘要，避免过长
        for el in error_lines:
            if el.lower().startswith("error"):
                detail = f" 错误: {el[:200]}"
                break
    return f"yt-dlp 退出码: {returncode}，目标文件不存在或不完整{detail}"


def run_ytdlp(cmd_list, log_callback, process_ref=None, on_process_created=None,
              on_path=None):
    # 提前初始化，确保任何异常路径（如 Popen 失败）下 except 都能安全访问
    _last_dest = None
    _error_lines = []   # 收集 yt-dlp 的 ERROR 输出行，用于失败时给出具体原因
    try:
        # 创建临时文件用于 --print-to-file 输出最终路径
        import tempfile
        tmp_path = tempfile.mktemp(suffix='.ytdlp_path')
        # 强制 yt-dlp 输出 UTF-8：Windows 中文环境默认输出 GBK，若按 UTF-8 解码会导致
        # 中文路径（如 [download] Destination: ...安次嶺希和子...mp4）乱码，
        # 进而导致 _on_path 记录的错误路径删不掉真实的 .part/.part.aria2。
        # 与 _run_ytdlp_json 的 --encoding utf-8 保持一致。
        # 注意：--encoding 必须插在 yt-dlp 可执行路径(cmd_list[0])之后，不能放最前面，
        # 否则 Popen 会把 --encoding 当程序名执行而启动失败。
        cmd_list = cmd_list[:1] + ['--encoding', 'utf-8'] + cmd_list[1:] + \
                   ['--print-to-file', 'after_move:filepath', tmp_path]

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
            creationflags=_PROC_FLAGS,
            env=env
        )
        if process_ref is not None:
            process_ref.append(process)
        if on_process_created is not None:
            on_process_created(process)
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line and log_callback:
                log_callback(line.strip())
            if line:
                ls = line.strip()
                if ls.lower().startswith("error") or ": error" in ls.lower() or ls.lower().startswith("warning"):
                    # ERROR/WARNING 行收集（去重、去重绘控制字符）
                    clean = ls.replace("\r", "")
                    if clean and clean not in _error_lines:
                        _error_lines.append(clean)
            # 从输出中提取目标文件路径（备选方案）
            if line:
                for prefix in ('[download] Destination: ',
                               '[Merger] Merging formats into ',
                               '[ExtractAudio] Destination: '):
                    if prefix in line:
                        raw = line[line.index(prefix) + len(prefix):].strip().strip('"')
                        if raw:
                            _last_dest = raw
                            # 实时暴露目标文件路径（供任务记录对应的 .part，取消时精准删除本任务临时文件）
                            if on_path is not None:
                                on_path(raw)
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
                raise RuntimeError(_build_ytdlp_error(process.returncode, _error_lines))
        return output_path if output_path else True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        # 收集到的 ERROR 行里若包含登录/反爬特征，优先给出明确提示
        msg = str(e)
        if _error_lines:
            for el in _error_lines:
                if "sign in to confirm" in el.lower():
                    raise RuntimeError(
                        "YouTube 要求登录确认（未登录/被反爬拦截）：建议导入 Cookies 后重试"
                    ) from e
        raise RuntimeError(f"yt-dlp 执行失败: {msg}")


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
        result = subprocess.run(cmd, capture_output=True, timeout=30,
                                creationflags=_PROC_FLAGS)
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
                              capture_output=True, text=True, timeout=5,
                              creationflags=_PROC_FLAGS)
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
