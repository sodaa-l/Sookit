"""
core/ffmpeg_utils.py
FFmpeg / yt-dlp / aria2c 路径、检查与执行工具
"""

import os
import sys
import subprocess
import re
from collections import deque
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


# yt-dlp / ffmpeg 的高噪声行：逐请求（`[https @ 0x...] Opening '...' for reading`）、
# 逐帧统计（`frame= ... speed= ...`）等。开了 -v 或用 ffmpeg 下载 HLS 时每个分片刷一行，
# 只进失败诊断缓冲，不进 UI 日志。
_FFMPEG_TAG_RE = re.compile(r'^\[[^\]\s]+ @ [0-9a-fA-F]+\]')
_FFMPEG_STAT_PREFIXES = ('frame=', 'size=', 'video:', 'audio:', 'subtitle:',
                         'other streams:', 'global headers:', 'muxing overhead:')


def _is_ytdlp_noise(line: str) -> bool:
    """是否为「不该进日志」的噪声行（诊断缓冲仍会保留）。"""
    return (line.startswith('[debug]')
            or bool(_FFMPEG_TAG_RE.match(line))
            or line.startswith(_FFMPEG_STAT_PREFIXES))


def run_ytdlp(cmd_list, log_callback, process_ref=None, on_process_created=None,
              on_path=None, diagnostics=False, *, subtitle_manifest=None):
    """执行 yt-dlp 命令并返回产物路径列表。

    subtitle_manifest 非空时，把 requested_subtitles 的 JSON 写入该路径（after_video）。
    此模式不使用 after_move / 日志产物兜底，并严格要求零退出码；调用方负责
    校验清单里的语言、实际格式和 workspace 内文件。默认音视频行为不变。

    diagnostics=True 时给 yt-dlp 加 `-v`，把完整输出缓冲在内存里：
    逐请求/逐帧的噪声行（`[debug] ...`、ffmpeg 组件日志、`frame=` 统计）**不进日志**，
    只在任务失败时把尾部回放到日志。合并类失败（如 Matroska 封装器拒绝畸形包）
    只有 `-v` 才有 ffmpeg 的 stderr，不这么做就只能看到一句
    `ERROR: Postprocessing: Conversion failed!`（见 DEVELOPMENT.md 决策 34）。
    """
    # 提前初始化，确保任何异常路径（如 Popen 失败）下 except 都能安全访问
    _last_dest = None
    _error_lines = []   # 收集 yt-dlp 的 ERROR 输出行，用于失败时给出具体原因
    _diag = deque(maxlen=300) if diagnostics else None   # -v 输出尾部（失败时回放）
    process = None
    tmp_path = None
    callback_error = None

    def _notify(callback, *args):
        nonlocal callback_error
        try:
            callback(*args)
        except Exception as error:
            callback_error = error
            raise

    def _dump_diagnostics():
        """失败时回放 yt-dlp 详细输出尾部（尽力而为，自身不得抛异常）。"""
        if not _diag or log_callback is None:
            return
        try:
            log_callback("↓ yt-dlp 详细输出尾部（诊断）：")
            for ln in list(_diag)[-40:]:
                log_callback("  " + ln[:280])
        except Exception:
            pass

    try:
        # 创建临时文件用于 --print-to-file 输出最终路径
        import tempfile
        tmp_path = None if subtitle_manifest is not None else tempfile.mktemp(suffix='.ytdlp_path')
        # 强制 yt-dlp 输出 UTF-8：Windows 中文环境默认输出 GBK，若按 UTF-8 解码会导致
        # 中文路径（如 [download] Destination: ...安次嶺希和子...mp4）乱码，
        # 进而导致 _on_path 记录的错误路径删不掉真实的 .part/.part.aria2。
        # 与 _run_ytdlp_json 的 --encoding utf-8 保持一致。
        # 注意：--encoding 必须插在 yt-dlp 可执行路径(cmd_list[0])之后，不能放最前面，
        # 否则 Popen 会把 --encoding 当程序名执行而启动失败。
        print_template = ('after_video:%(requested_subtitles)j'
                          if subtitle_manifest is not None else 'after_move:filepath')
        cmd_list = cmd_list[:1] + ['--encoding', 'utf-8'] + cmd_list[1:] + \
                   ['--print-to-file', print_template,
                    str(subtitle_manifest) if subtitle_manifest is not None else tmp_path]
        # 诊断模式：-v 才能拿到 ffmpeg 的命令行与 stderr（合并失败的唯一线索）
        if diagnostics:
            cmd_list = cmd_list[:1] + ['-v'] + cmd_list[1:]

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
            _notify(on_process_created, process)
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                continue
            ls = line.strip()
            if _diag is not None:
                _diag.append(ls)
            # 噪声行（-v 调试行 / ffmpeg 逐请求与逐帧输出）只留在诊断缓冲里
            if log_callback and not (_diag is not None and _is_ytdlp_noise(ls)):
                _notify(log_callback, ls)
            if ls.lower().startswith("error") or ": error" in ls.lower() or ls.lower().startswith("warning"):
                # ERROR/WARNING 行收集（去重、去重绘控制字符）
                clean = ls.replace("\r", "")
                if clean and clean not in _error_lines:
                    _error_lines.append(clean)
                    if len(_error_lines) > 20:   # -v 下 WARNING 可能很多，只留尾部
                        _error_lines.pop(0)
            # 从输出中提取目标文件路径（备选方案）
            for prefix in ('[download] Destination: ',
                           '[Merger] Merging formats into ',
                           '[ExtractAudio] Destination: '):
                if prefix in ls:
                    raw = ls[ls.index(prefix) + len(prefix):].strip().strip('"')
                    if raw:
                        _last_dest = raw
                        # 实时暴露目标文件路径（供任务记录对应的 .part，取消时精准删除本任务临时文件）
                        if on_path is not None:
                            _notify(on_path, raw)
        # 读取 --print-to-file 输出的最终路径（最可靠）。
        # 一个 Task 可能产出多个最终文件（如 -f '137,140' 不合并时视频+音频），
        # yt-dlp 会把每个最终路径写为一行，因此按行拆分收集为列表。
        # yt-dlp 输出的是 UTF-8，用 GBK 打开含日/韩字符的路径会报错
        if subtitle_manifest is not None:
            # skip-download 不触发 after_move；字幕由调用方按结构化清单严格验收。
            if process.returncode != 0:
                raise RuntimeError(_build_ytdlp_error(process.returncode, _error_lines))
            return []
        output_paths = []
        if os.path.exists(tmp_path):
            try:
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    for raw_line in f:
                        p = raw_line.strip()
                        if p and os.path.isfile(p):
                            output_paths.append(p)
            except Exception:
                pass
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        # 回退：使用日志解析的最后路径
        if not output_paths and _last_dest and os.path.isfile(_last_dest):
            output_paths = [_last_dest]
        if process.returncode != 0:
            # yt-dlp 有时在文件已下载成功后仍返回非零退出码（如 ffmpeg 合并问题）
            # 检查最终输出文件是否存在：存在则视为成功，不存在才报错
            if output_paths and all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in output_paths):
                if log_callback:
                    _notify(log_callback, f"⚠ yt-dlp 退出码: {process.returncode}（但目标文件存在，视为成功）")
            else:
                raise RuntimeError(_build_ytdlp_error(process.returncode, _error_lines))
        return output_paths
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if e is callback_error:
            # 取消由 Worker 回调抛出；保留原异常，不能被诊断信息覆盖。
            raise
        # 收集到的 ERROR 行里若包含登录/反爬特征，优先给出明确提示
        msg = str(e)
        _dump_diagnostics()   # 失败时回放 -v 详情（含 ffmpeg 命令行与 stderr）
        if _error_lines:
            for el in _error_lines:
                if "sign in to confirm" in el.lower():
                    raise RuntimeError(
                        "YouTube 要求登录确认（未登录/被反爬拦截）：建议导入 Cookies 后重试"
                    ) from e
        raise RuntimeError(f"yt-dlp 执行失败: {msg}")
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    # 决策 9：异常/取消时只结束本任务的进程树。
                    if sys.platform == 'win32':
                        try:
                            subprocess.run(
                                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                        except (OSError, subprocess.SubprocessError):
                            pass
                    if process.poll() is None:
                        process.kill()
                wait = getattr(process, 'wait', None)
                if wait is not None:
                    try:
                        wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass  # 清理失败不得掩盖原始下载/取消异常。
            finally:
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
