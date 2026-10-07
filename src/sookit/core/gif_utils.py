"""
core/gif_utils.py
MP4 → GIF 全自动参数决策转码（移植自 auto_gif.py，决策逻辑逐条对齐）

【优化目标】不是凑到目标体积，而是在**体积上界以内最大化质量**：
  · 帧率：直接锁死，不做判断（源 ≤ fps_cap 保持源帧率；> fps_cap 锁在 fps_cap）
  · 色数：实测量化 MAE，选"刚好不出现可见色带"的最小候选；
          若宽度已顶到源上限、预算仍有富余，则继续升级色数
  · 分辨率：在 size ≤ 体积上界 的约束下取**最大可用宽度**（上限为源宽度）
  · 兜底：若连最小宽度都超出上界，仍交付最小宽度结果（日志标注实际体积与超出幅度）

【与 auto_gif.py 的差异（仅"工程外壳"，决策参数一律不变）】
  · ffmpeg/ffprobe 走 Sookit 内嵌路径（缺失时回退 PATH 裸名）
  · 编码子进程走 run_ffmpeg（暴露 on_process_created，供任务取消 taskkill 进程树）
  · 临时文件建在私有 mkdtemp 目录，结束即清理（不做 _rounds 归档、不落用户目录）
  · 无进度 → 阶段式 on_progress 上报（单调不减）

【取消】复用 Sookit 现有机制：取消时 taskkill 当前 ffmpeg 进程 → run_ffmpeg 抛异常
→ 本函数向上抛出 → TaskWorker 见 _cancelled 静默处理，无需额外的取消回调。
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile

from sookit.core.ffmpeg_utils import (
    get_ffmpeg_path, get_ffprobe_path, run_ffmpeg,
)

# ---- 决策常量（与 auto_gif.py 完全一致，勿随意改动）----
COLOR_CANDIDATES = (24, 64, 128, 256)
ANALYSIS_W = 480
SAMPLE_FRAMES = 8
TRIAL_WIDTH = 320
MAX_WIDTH_ROUNDS = 5
# 色数升级不设固定档数上限：候选有几档就允许升几档，由预算检查自然终止。
# （曾写死 2 档，导致 10MB 上界下线稿类内容只用到 47% 预算就停在 128 色 —— 与基准
#   auto_gif.py 的写法一致；基准脚本正是为此把它从 2 改成 len(COLOR_CANDIDATES)）
MAX_COLOR_UPGRADES = len(COLOR_CANDIDATES)
MIN_WIDTH = 200
BUDGET_STOP = 0.93          # 用到预算的 93% 即认为逼近到位，停止加宽
ANALYSIS_SECONDS = 10.0     # 分析帧最长取源的前 N 秒

# ---- 默认参数 ----
DEFAULT_FPS_CAP = 30
DEFAULT_QUANT_THRESHOLD = 1.3
DEFAULT_MIN_COLORS = 24


# ---- 编码质量档位 ----
class GifQuality:
    """编码质量档位。

    低质量   — 2MB 软上界，自动决策宽度与色数
    标准     — 4MB 软上界，自动决策宽度与色数
    高质量   — 10MB 软上界，自动决策宽度与色数（默认档）
    最佳质量 — 源分辨率 + 256 色，仅锁定帧率，不做体积约束
    自定义   — 配合 GifCustomParams：用户指定宽度/帧率上限/色数/dither，单次编码
    """
    LOW = "低质量"
    STANDARD = "标准"
    HIGH = "高质量"
    BEST = "最佳质量"
    CUSTOM = "自定义"


# ---- 自定义档 dither 可选值（ffmpeg paletteuse 支持的子集）----
DITHER_CHOICES = ("none", "bayer", "floyd_steinberg", "sierra2_4a")


class GifCustomParams:
    """自定义编码参数（quality = GifQuality.CUSTOM 时传入 convert_to_gif）。

    width   — 目标宽度 px（高度按源宽高比自动计算；超过源宽度时收敛到源宽度）
    fps_cap — 帧率上限：源 ≤ 上限保持源帧率，超过则锁在上限（与预设档同语义）
    colors  — 调色板色数，2 ~ 256
    dither  — paletteuse 抖动算法，取值见 DITHER_CHOICES
    """

    def __init__(self, width: int, fps_cap: float, colors: int,
                 dither: str = "none"):
        self.width = int(width)
        self.fps_cap = float(fps_cap)
        self.colors = int(colors)
        self.dither = dither

    def validate(self):
        """入参校验，非法时抛 RuntimeError（带可读原因）。"""
        if self.width <= 0:
            raise RuntimeError(f"自定义宽度必须为正整数，当前: {self.width}")
        if self.fps_cap <= 0:
            raise RuntimeError(f"自定义帧率上限必须为正数，当前: {self.fps_cap}")
        if not (2 <= self.colors <= 256):
            raise RuntimeError(
                f"自定义色数必须在 2~256 之间，当前: {self.colors}")
        if self.dither not in DITHER_CHOICES:
            raise RuntimeError(
                f"未知 dither 算法: {self.dither!r}（应为 "
                + " / ".join(DITHER_CHOICES) + "）")

    def __repr__(self):
        return (f"GifCustomParams(width={self.width}, fps_cap={self.fps_cap}, "
                f"colors={self.colors}, dither={self.dither!r})")


# 档位 → 体积软上界（MB）。最佳质量档不做体积约束，故不在此表内。
_QUALITY_BUDGET_MB = {
    GifQuality.LOW: 2.0,
    GifQuality.STANDARD: 4.0,
    GifQuality.HIGH: 10.0,
}

_PROC_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# ---- 阶段式进度区间 (起始%, 结束%) ----
_ST_PROBE = (0.0, 8.0)      # ffprobe + 读分析帧
_ST_COLORS = (8.0, 18.0)    # 色数决策
_ST_TRIAL = (18.0, 30.0)    # 试编码测 BPP
_ST_WIDTH = (30.0, 85.0)    # 宽度搜索
_ST_COLORUP = (85.0, 95.0)  # 色数升级
_ST_FINAL = (95.0, 100.0)   # 定稿 + 清理


# ---------------------------------------------------------------- 路径 / 小工具

def _ffmpeg():
    p = get_ffmpeg_path()
    return p if os.path.exists(p) else "ffmpeg"


def _ffprobe():
    p = get_ffprobe_path()
    return p if os.path.exists(p) else "ffprobe"


def _even(v):
    """对齐到偶数（GIF/缩放对奇偶敏感，与脚本一致）"""
    return max(2, int(round(v / 2.0)) * 2)


def _fmt_fps(f):
    s = f"{f:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _lock_fps(src_fps, cap):
    """源 ≤ cap → 保持源帧率；源 > cap → 锁在 cap。不做任何流畅度判断。"""
    return src_fps if src_fps <= cap else float(cap)


class _Progress:
    """阶段式进度：单调不减，避免提前 break 导致回退。"""

    def __init__(self, cb):
        self._cb = cb
        self._last = 0.0

    def set(self, pct):
        pct = max(0.0, min(100.0, float(pct)))
        if pct > self._last:
            self._last = pct
        if self._cb:
            self._cb(self._last)

    def span(self, rng, frac):
        """把 [0,1] 的 frac 映射到 rng 区间内"""
        lo, hi = rng
        frac = max(0.0, min(1.0, float(frac)))
        self.set(lo + (hi - lo) * frac)


# ---------------------------------------------------------------- ffprobe

def _probe(path):
    """读取源视频参数（宽/高/帧率/帧数/时长/大小）"""
    cmd = [_ffprobe(), "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate,nb_frames,codec_name",
           "-show_entries", "format=duration,size", "-of", "default=nw=1", path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       creationflags=_PROC_FLAGS)
    if r.returncode != 0:
        raise RuntimeError(
            "ffprobe 解析失败: " + ((r.stderr or r.stdout or "").strip()[:300]))

    d = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v.strip()

    if not d.get("width") or not d.get("height"):
        raise RuntimeError("ffprobe 未能解析出视频尺寸（可能不是有效视频）")

    num, _, den = (d.get("r_frame_rate") or "").partition("/")
    try:
        fps = float(num) / float(den) if den and float(den) else float(num or 30.0)
    except ValueError:
        fps = 30.0
    if fps <= 0:
        fps = 30.0

    try:
        dur = float(d.get("duration") or 0.0)
    except ValueError:
        dur = 0.0
    if dur <= 0:
        raise RuntimeError("ffprobe 未能解析出视频时长")

    nf = d.get("nb_frames", "N/A")
    try:
        frames = int(nf) if nf not in ("N/A", "") else round(fps * dur)
    except ValueError:
        frames = round(fps * dur)

    try:
        size = int(d.get("size") or 0)
    except ValueError:
        size = 0
    if size <= 0:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

    return {
        "w": int(d["width"]), "h": int(d["height"]), "fps": fps, "dur": dur,
        "frames": frames, "codec": d.get("codec_name", "?"), "size": size,
    }


# ---------------------------------------------------------------- 读分析帧

def _read_frames(path, fps, width, src_w, src_h, max_frames=None):
    """ffmpeg rawvideo 管道直读分析帧（不落盘）。

    这里必须用裸 subprocess.run（二进制 stdout 不能走 run_ffmpeg 的逐行文本读取），
    与 ffmpeg_utils.extract_video_frame() 同款处理。
    """
    import numpy as np

    h = _even(width * src_h / src_w)
    vf = f"fps={fps},scale={width}:{h}:flags=lanczos"
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", path, "-vf", vf]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    r = subprocess.run(cmd, capture_output=True, creationflags=_PROC_FLAGS)
    buf = r.stdout or b""
    fsz = width * h * 3
    n = len(buf) // fsz
    if n == 0:
        err = (r.stderr or b"")[-200:].decode("utf8", "ignore")
        raise RuntimeError(f"rawvideo 读取为空: {err}")
    arr = np.frombuffer(buf[:n * fsz], dtype=np.uint8).reshape(n, h, width, 3)
    return arr, width, h


# ---------------------------------------------------------------- 色数决策

def _decide_colors(frames, hi, min_colors):
    """采样若干帧做量化 MAE 实测，取"刚好不出现可见色带"的最小候选色数。"""
    import numpy as np
    from PIL import Image

    count = min(SAMPLE_FRAMES, len(frames))
    idx = sorted(set(np.linspace(0, len(frames) - 1, count).astype(int)))
    imgs = [Image.fromarray(frames[i]) for i in idx]

    table = []
    for c in COLOR_CANDIDATES:
        if c < min_colors:
            continue
        errs = []
        for im in imgs:
            q = im.quantize(colors=c,
                            method=Image.Quantize.MEDIANCUT).convert("RGB")
            errs.append(float(np.abs(np.asarray(im, np.float32) -
                                     np.asarray(q, np.float32)).mean()))
        table.append((c, float(np.mean(errs))))

    chosen = COLOR_CANDIDATES[-1]
    for c, e in table:
        if e <= hi:
            chosen = max(c, min_colors)
            break
    return chosen, table


# ---------------------------------------------------------------- 编码

def _encode_gif(src, out, width, height, fps, colors, pal_path,
                log=None, on_process_created=None, dither="none"):
    """palettegen + paletteuse 两段式编码，返回产物字节数。"""
    fstr = _fmt_fps(fps)
    base = f"fps={fstr},scale={width}:{height}:flags=lanczos"

    run_ffmpeg(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", src,
         "-vf", f"{base},palettegen=max_colors={colors}:stats_mode=diff", pal_path],
        log, None, on_process_created)

    run_ffmpeg(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", src,
         "-i", pal_path,
         "-lavfi", f"{base}[x];[x][1:v]paletteuse=dither={dither}:diff_mode=rectangle",
         "-loop", "0", out],
        log, None, on_process_created)

    return os.path.getsize(out)


def _gif_frames(path):
    """数 GIF 实际帧数（用于 BPP 实测）"""
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_PROC_FLAGS)
    try:
        return int((r.stdout or "").strip().splitlines()[0])
    except Exception:
        return 0


# ---------------------------------------------------------------- 定稿

def _finalize(chosen, output, fps_out, colors_final, log, prog):
    """把选定的候选 GIF 落到 output（同卷原子替换 / 跨卷复制），打完成日志并收尾进度。"""
    if os.path.exists(output):
        try:
            os.remove(output)
        except OSError:
            pass
    try:
        os.replace(chosen["path"], output)          # 同卷：原子替换
    except OSError:
        shutil.move(chosen["path"], output)         # 跨卷：复制后删除

    log(f"[GIF] 完成: {os.path.basename(output)}  "
        f"{chosen['sz'] / 1048576:.2f} MB · {chosen['w']}×{chosen['h']} · "
        f"{_fmt_fps(fps_out)}fps · {colors_final} 色")
    prog.set(_ST_FINAL[1])
    return output


# ---------------------------------------------------------------- 主入口

def convert_to_gif(video, output, quality=GifQuality.HIGH, *,
                   custom_params=None,
                   max_mb=None, fps_cap=DEFAULT_FPS_CAP,
                   quant_threshold=DEFAULT_QUANT_THRESHOLD,
                   min_colors=DEFAULT_MIN_COLORS,
                   log=None, on_process_created=None, on_progress=None):
    """把 MP4（含 X 动图下载得到的无声 MP4）转成 GIF，返回最终文件路径。

    Args:
        video: 输入 MP4 路径
        output: 输出 GIF 完整路径
        quality: 编码质量档位（GifQuality.LOW / STANDARD / HIGH / BEST，默认 HIGH；
            传入 custom_params 时忽略此参数）
            · 低质量：2MB 软上界，自动决策宽度与色数
            · 标准：4MB 软上界，自动决策宽度与色数
            · 高质量：10MB 软上界，自动决策宽度与色数（默认）
            · 最佳质量：源分辨率 + 256 色，仅锁定帧率，不做体积约束
        custom_params: GifCustomParams（自定义模式）。非 None 时走自定义路径：
            按用户指定的宽度/帧率上限/色数/dither 单次编码，不做预算搜索与
            MAE 色数决策，也不约束体积；quality 此时被忽略
        max_mb: 体积软上界（MB）。None（默认）= 由 quality 派生（低质量 2.0 / 标准 4.0 /
                高质量 10.0）；最佳质量档与自定义模式忽略此参数；显式传入可覆盖档位默认值
        fps_cap: 帧率上限（源 ≤ 上限保持源帧率，超过则锁在上限；预设档共用，
                自定义模式使用 custom_params.fps_cap）
        quant_threshold: 色数决策的量化 MAE 阈值（最佳质量档与自定义模式不使用）
        min_colors: 色数下限（最佳质量档与自定义模式不使用）
        log: 日志回调（Sookit 约定）
        on_process_created: 子进程创建回调（取消时由其 taskkill 进程树）
        on_progress: 进度回调，接收 0~100 的 float
    """
    def _log(msg):
        if log:
            log(msg)

    prog = _Progress(on_progress)

    # ---- 入参校验 ----
    if not video or not os.path.isfile(video):
        raise RuntimeError(f"输入文件不存在: {video}")
    if not output:
        raise RuntimeError("未指定输出路径")

    is_best = (quality == GifQuality.BEST)
    is_custom = custom_params is not None
    if is_custom:
        custom_params.validate()
        # 自定义模式忽略 quality 档位（UI 侧已保证互斥，这里再兜一层）
        quality = GifQuality.CUSTOM
        fps_cap = custom_params.fps_cap
    elif quality not in _QUALITY_BUDGET_MB and quality != GifQuality.BEST:
        raise RuntimeError(
            f"未知的编码质量档位: {quality!r}（应为 {GifQuality.LOW} / "
            f"{GifQuality.STANDARD} / {GifQuality.HIGH} / {GifQuality.BEST}）")

    if not is_best and not is_custom:
        if max_mb is None:
            max_mb = _QUALITY_BUDGET_MB[quality]
        if float(max_mb) <= 0:
            raise RuntimeError("体积上界必须大于 0")

    # 延迟 import：避免 numpy/Pillow 拖慢应用启动，缺失时给出明确报错。
    # 最佳质量档与自定义模式不读分析帧、不做色数决策，故不需要这两个库。
    if not is_best and not is_custom:
        try:
            import numpy  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"缺少依赖 numpy / Pillow，无法转 GIF: {e}")

    budget = (None if (is_best or is_custom)
              else int(float(max_mb) * 1024 * 1024))
    out_dir = os.path.dirname(os.path.abspath(output))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"无法创建输出目录 {out_dir}: {e}")

    tmpdir = tempfile.mkdtemp(prefix="sookit_gif_")
    try:
        # ---- ① 探测源 ----
        info = _probe(video)
        _log(f"[GIF] 源: {info['codec']} {info['w']}×{info['h']} · "
             f"{info['fps']:.2f}fps · {info['frames']} 帧 · {info['dur']:.2f}s · "
             f"{info['size'] / 1048576:.2f} MB")
        prog.set(_ST_PROBE[1])

        # ---- ① 帧率锁定（各档共用；自定义档取 custom_params.fps_cap）----
        fps_out = _lock_fps(info["fps"], fps_cap)
        rule = ("≤上限，保持源帧率" if info["fps"] <= fps_cap
                else f"超过上限，锁在 {fps_cap}fps")
        _log(f"[GIF] ① 帧率锁定: 源 {info['fps']:.2f}fps → "
             f"{_fmt_fps(fps_out)}fps（{rule}）")
        _log(f"[GIF] 质量档位: {quality}")

        if is_best:
            # ---- 最佳质量：源分辨率 + 256 色，一次编码，不做体积约束 ----
            colors_final = 256
            bw, bh = info["w"], info["h"]
            _log(f"[GIF] 最佳质量: 源分辨率 {bw}×{bh} · 256 色 · 不限制体积")
            prog.set(_ST_TRIAL[1])            # 跳过分析与试编码，直接进入编码
            best_out = os.path.join(tmpdir, f"best_{bw}x{bh}.gif")
            best_pal = os.path.join(tmpdir, "pal_best.png")
            best_sz = _encode_gif(video, best_out, bw, bh, fps_out, colors_final,
                                  best_pal, log, on_process_created)
            _log(f"[GIF] 编码完成: {bw}×{bh} · {colors_final} 色 · "
                 f"{best_sz / 1048576:.2f} MB")
            prog.set(_ST_COLORUP[1])
            return _finalize({"w": bw, "h": bh, "sz": best_sz, "path": best_out},
                             output, fps_out, colors_final, _log, prog)

        if is_custom:
            # ---- 自定义：按用户参数单次编码，不做预算搜索与色数决策 ----
            cw = min(custom_params.width, info["w"])
            if cw < custom_params.width:
                _log(f"[GIF] 自定义宽度 {custom_params.width} 超过源宽度，"
                     f"收敛到源宽度 {cw}")
            ch = _even(cw * info["h"] / info["w"])
            _log(f"[GIF] 自定义: {cw}×{ch} · {custom_params.colors} 色 · "
                 f"dither={custom_params.dither} · 不限制体积")
            prog.set(_ST_TRIAL[1])            # 跳过分析与试编码，直接进入编码
            cust_out = os.path.join(tmpdir, f"custom_{cw}x{ch}.gif")
            cust_pal = os.path.join(tmpdir, "pal_custom.png")
            cust_sz = _encode_gif(video, cust_out, cw, ch, fps_out,
                                  custom_params.colors, cust_pal,
                                  log, on_process_created,
                                  dither=custom_params.dither)
            _log(f"[GIF] 编码完成: {cw}×{ch} · {custom_params.colors} 色 · "
                 f"{cust_sz / 1048576:.2f} MB")
            prog.set(_ST_COLORUP[1])
            return _finalize({"w": cw, "h": ch, "sz": cust_sz, "path": cust_out},
                             output, fps_out, custom_params.colors, _log, prog)

        # ---- 分析帧（低/标准/高质量档：供色数决策使用）----
        analysis_fps = min(info["fps"], 60.0)
        cap = int(analysis_fps * min(info["dur"], ANALYSIS_SECONDS)) + 2
        frames, aw, ah = _read_frames(
            video, round(analysis_fps, 6), ANALYSIS_W,
            info["w"], info["h"], max_frames=cap)
        _log(f"[GIF] 分析帧: {len(frames)} 张 @{aw}×{ah}")

        # ---- ③ 色数决策 ----
        colors, ctable = _decide_colors(frames, quant_threshold, min_colors)
        _log(f"[GIF] ② 色数决策（量化 MAE ≤ {quant_threshold}，下限 {min_colors} 色）:")
        for c, e in ctable:
            _log(f"[GIF]      {c:>3}色  MAE {e:6.2f}  "
                 f"{'达标' if e <= quant_threshold else '超标'}")
        _log(f"[GIF] ② 起点 {colors} 色")
        prog.set(_ST_COLORS[1])

        # ---- ④ 试编码测 BPP ----
        tw = min(TRIAL_WIDTH, info["w"])
        th = _even(tw * info["h"] / info["w"])
        trial = os.path.join(tmpdir, "trial.gif")
        pal_trial = os.path.join(tmpdir, "pal_trial.png")
        tsz = _encode_gif(video, trial, tw, th, fps_out, colors, pal_trial,
                          log, on_process_created)
        tnf = _gif_frames(trial) or max(1, round(info["dur"] * fps_out))
        bpp = tsz / (tw * th * tnf)
        _log(f"[GIF] ③ 试编码: {tw}×{th} · {tnf}帧 · {tsz / 1048576:.2f}MB "
             f"→ BPP {bpp:.4f} B/像素")
        prog.set(_ST_TRIAL[1])

        # ---- ⑤ 宽度搜索：预算内最大化宽度 ----
        est_frames = max(1, round(info["dur"] * fps_out))
        aspect = info["h"] / info["w"]
        w_hi = info["w"]
        w = int(max(MIN_WIDTH,
                    min(w_hi, math.sqrt(budget / (bpp * est_frames * aspect)))) // 2 * 2)

        _log(f"[GIF] ④ 宽度搜索（预算 {budget / 1048576:.2f}MB 为上界，"
             f"目标取最大可用宽度）:")
        best = None        # 达标（≤ 预算）里最宽的一次
        fallback = None    # 全部超标时的兜底：超标结果里体积最小的一次
        for it in range(MAX_WIDTH_ROUNDS):
            h = _even(w * aspect)
            out = os.path.join(tmpdir, f"w{w}.gif")
            pal = os.path.join(tmpdir, f"pal_w{w}.png")
            sz = _encode_gif(video, out, w, h, fps_out, colors, pal,
                             log, on_process_created)
            util = sz / budget * 100
            ok = sz <= budget
            _log(f"[GIF]      第{it + 1}次 {w}×{h} → {sz / 1048576:.2f}MB  "
                 f"预算占用 {util:5.1f}%  {'可用' if ok else '超预算'}")
            prog.span(_ST_WIDTH, (it + 1) / MAX_WIDTH_ROUNDS)

            if ok and (best is None or w > best["w"]):
                best = {"w": w, "h": h, "sz": sz, "path": out}
            if not ok and (fallback is None or sz < fallback["sz"]):
                fallback = {"w": w, "h": h, "sz": sz, "path": out}

            if not ok:
                nw = int(max(MIN_WIDTH, w * math.sqrt(budget / sz)) // 2 * 2)
                if nw >= w:
                    nw = w - 2
            else:
                if w >= w_hi or sz >= budget * BUDGET_STOP:
                    break
                nw = int(min(w_hi, w * math.sqrt(budget / sz)) // 2 * 2)

            if nw < MIN_WIDTH:
                nw = MIN_WIDTH
            if nw == w:
                break
            w = nw

        prog.set(_ST_WIDTH[1])

        # 达标优先；一次都没达标时走"尽量出文件"兜底（定稿阶段交付 fallback）
        if best is None and fallback is None:
            raise RuntimeError("转码未产出任何结果")

        # ---- ⑥ 宽度顶到源上限且预算有富余 → 把余额花在色数上 ----
        colors_final = colors
        if best is not None and best["w"] >= w_hi and best["sz"] < budget * 0.90:
            upgrades = [c for c in COLOR_CANDIDATES
                        if c > colors][:MAX_COLOR_UPGRADES]
            for i, c in enumerate(upgrades):
                out = os.path.join(tmpdir, f"c{c}.gif")
                pal = os.path.join(tmpdir, f"pal_c{c}.png")
                sz = _encode_gif(video, out, best["w"], best["h"], fps_out, c, pal,
                                 log, on_process_created)
                ok = sz <= budget
                _log(f"[GIF]       色数升级 {colors_final}→{c}: {sz / 1048576:.2f}MB  "
                     f"预算占用 {sz / budget * 100:5.1f}%  "
                     f"{'采用' if ok else '超预算'}")
                prog.span(_ST_COLORUP, (i + 1) / max(1, len(upgrades)))
                if not ok:
                    break
                colors_final = c
                best = {"w": best["w"], "h": best["h"], "sz": sz, "path": out}

        prog.set(_ST_COLORUP[1])

        # ---- ⑦ 定稿：候选文件 → 目标路径，其余随 tmpdir 清理 ----
        # 注意 chosen 必须在色数升级之后取，升级会替换 best 对象
        chosen = best if best is not None else fallback
        if best is None:
            _log(f"[GIF] ！无法压到上界 {max_mb} MB 以内，按最小宽度交付 "
                 f"{chosen['sz'] / 1048576:.2f} MB"
                 f"（超出上界 {chosen['sz'] / budget * 100 - 100:.0f}%）")
        return _finalize(chosen, output, fps_out, colors_final, _log, prog)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
