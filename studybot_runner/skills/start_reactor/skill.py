# -*- coding: utf-8 -*-
"""
启动反应堆（Start Reactor）视觉自动化。

本模块把《Among Us》「核心反应堆 / 启动核反应堆」任务拆成两层：

* :class:`ReactorVision`  —— 只负责「看」：把一帧画面变成 9 个按钮的
  颜色/能量读数，用 GPU 做差分与统计，并给出「哪些按钮刚刚亮起」。
* :class:`ReactorController` —— 只负责「做」：把亮起事件按时间排成
  闪烁序列，在系统不再闪烁时切换到点击模式把序列按顺序点一遍，
  一旦看到蓝光报错就立刻停手、重置、重新记录。

设计要点
--------
* **固定几何 + 可选重定位**：九宫格按钮在 1280x800 下位置固定（见
  :data:`BUTTON_BOXES`），因此每帧只需要在 257x257 的紧 ROI 上做运算，
  绝不碰 1280x800 全图。``ReactorVision.locate_panel`` 可在面板偏移时
  重新找回九宫格原点。
* **GPU 优先**：后端按 ``cv2.cuda`` → ``cupy`` → ``numpy`` 三级回退。
  只有 ROI 会进显存，差分、阈值、逐按钮归约全部在 GPU 内完成，
  回传到主机的只有 9 个浮点数。
* **只记暗→亮起始点**：闪烁会持续 3~7 帧（实测），但只记录「暗→亮」的
  那一帧，再叠加「最小间隔去抖」+「帧间差分能量确认」两道闸，保证一次
  闪烁只产生一个事件。
* **判据用颜色而不是亮度**：亮起态是饱和的蓝（B-R = +130 ~ +158），
  常态是中性灰/白（B-R = 0 ± 3），面板关闭时露出的背景是 B-R = +27 ~ +57。
  蓝色分量与红色分量之差（B-R）是唯一能把三者分开的特征，
  而且对鼠标指针、JPEG 压缩噪声都不敏感；亮起判定用 80 作为阈值，
  上下两侧各有 23 / 50 的裕度。亮起/熄灭用 80 / 50 的迟滞，避免抖动。
* **帧间差分在 GPU 上完成**：差分能量只作为「这一帧确实变了」的确认信号
  （真实起始帧 132.6 ~ 157.1，其余帧 p95 仅 13.2，阈值取 30）。

坐标约定
--------
所有常量都以 **1280x800 基准画面** 为准。:data:`BUTTON_CENTERS` 给出
每个按钮中心的绝对坐标；:data:`BUTTON_CENTERS_REL` 给出相对于
窗口中心 (640, 400) 的偏移。真正点击时由 :class:`CoordinateMapper`
把这套坐标映射到游戏窗口客户区（或整块屏幕）的实际像素位置，
因此全屏与窗口化都能用。

命令行
------
离线回放（读取切好的帧序列，打印每一轮识别到的序列）::

    python runtime/tasks/start_reactor.py --replay "临时" --frames 732

真机运行（抓取游戏窗口并自动点击）::

    python runtime/tasks/start_reactor.py --live --window "Among Us"
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "BASE_WIDTH",
    "BASE_HEIGHT",
    "BASE_CENTER",
    "BUTTON_BOXES",
    "BUTTON_CENTERS",
    "BUTTON_CENTERS_REL",
    "GRID_ROI",
    "CORE_ROI",
    "ReactorObservation",
    "ReactorVision",
    "ReactorController",
    "CoordinateMapper",
    "MouseController",
    "WindowCapture",
    "detect_backend",
]


def _write_status_file(path: Optional[str], payload: dict) -> None:
    """Atomically publish the live skill state for the in-game HUD."""
    if not path:
        return
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    payload = {
        "schemaVersion": "1.0.0",
        "skillId": "start-reactor",
        "updatedAtUtc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
        **payload,
    }
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    os.replace(temporary, target)


# --------------------------------------------------------------------------
# 几何常量（全部基于 1280x800 基准画面）
# --------------------------------------------------------------------------

BASE_WIDTH = 1280
BASE_HEIGHT = 800
#: 基准画面的窗口中心
BASE_CENTER = (640.0, 400.0)

#: 九宫格按钮「内面」（可点击的方形亮面）在 1280x800 下的包围盒 (x0, y0, x1, y1)
#: 行列均为闭区间像素。顺序为行主序：1 2 3 / 4 5 6 / 7 8 9。
#: 按钮间距：列 91/92 px，行 93/92 px；亮面尺寸 54x57 / 57x58 / 56x56。
BUTTON_BOXES: Tuple[Tuple[int, int, int, int], ...] = (
    (744, 321, 797, 377),  # 1 左上
    (835, 321, 891, 377),  # 2 上中
    (928, 321, 983, 377),  # 3 右上
    (744, 414, 797, 471),  # 4 中左
    (835, 414, 891, 471),  # 5 正中
    (928, 414, 983, 471),  # 6 中右
    (744, 505, 797, 560),  # 7 左下
    (835, 505, 891, 560),  # 8 下中
    (928, 505, 983, 560),  # 9 右下
)

#: 每个按钮中心在 1280x800 下的绝对坐标
BUTTON_CENTERS: Tuple[Tuple[float, float], ...] = tuple(
    ((x0 + x1) / 2.0, (y0 + y1) / 2.0) for (x0, y0, x1, y1) in BUTTON_BOXES
)

#: 每个按钮中心相对于窗口中心 (640, 400) 的偏移
BUTTON_CENTERS_REL: Tuple[Tuple[float, float], ...] = tuple(
    (cx - BASE_CENTER[0], cy - BASE_CENTER[1]) for (cx, cy) in BUTTON_CENTERS
)

#: 九宫格紧 ROI：九个亮面的外接框再留 3 px 余量，257x257。
#: 绝大多数帧只需要在这个方块上做运算。
GRID_ROI: Tuple[int, int, int, int] = (735, 312, 992, 569)

#: 「核心 ROI」：以九宫格中心 (863, 440) 为心的 500x500 区域。
#: 适合做面板存在性检查或需要更大上下文时使用；检测仍然只用紧 ROI。
CORE_ROI: Tuple[int, int, int, int] = (613, 190, 1113, 690)

#: 亮起（蓝）判定：按钮区域平均 B 通道减 R 通道超过该值即视为亮起。
#:
#: 实测（1280x800 全分辨率、732 帧）分布：
#:   * 亮起态   B-R = +130 ~ +158
#:   * 常态     B-R =   0 ± 3
#:   * 面板关闭时露出的背景  B-R = +27 ~ +57
#: 因此阈值取 80：距背景上界 23、距亮起下界 50，两侧裕度都很大。
LIT_BR_THRESHOLD = 80.0

#: 迟滞释放阈值：B-R 落到该值以下才算「熄灭」。
#: 用迟滞可以避免按钮正中央的鼠标指针或 JPEG 噪声让亮起态在阈值附近抖动，
#: 从而重复产生「暗→亮」起始点。
LIT_BR_RELEASE = 50.0

#: 面板明暗分界：九个按钮的平均亮度高于该值视为「面板点亮」（序列播放阶段），
#: 低于该值视为「面板变暗」（等待玩家按键阶段）。
#: 实测：点亮 ≈ 182 ~ 192，变暗 ≈ 105 ~ 109。
BRIGHT_LUM_THRESHOLD = 150.0

#: 面板存在性判定：所有**未亮起**的按钮都必须是中性色
#: （B-R 绝对值小于该值），且亮度中位数不低于 PANEL_MIN_LUM。
#: 实测：面板开启时中性按钮 B-R ≈ 0 ± 6；面板关闭时背景 B-R = +27 ~ +57，
#: 亮度中位数 ≈ 28（帧 657 起）~ 78（帧 1 起）。
PANEL_MAX_NEUTRAL_BR = 10.0
PANEL_MIN_LUM = 60.0

#: 一次「全体闪蓝」判定：同时亮起的按钮数达到该值即认为是报错/终态闪光。
ERROR_FLASH_MIN_LIT = 2


# --------------------------------------------------------------------------
# 观测结果
# --------------------------------------------------------------------------


@dataclass
class ReactorObservation:
    """单帧观测结果。

    字段
    ----
    frame_index:
        帧序号（从 0 开始的自增计数）。
    br_mean:
        长度 9 的数组，每个按钮区域的平均 ``B - R``。亮起态 ≈ +143。
    lum_mean:
        长度 9 的数组，每个按钮区域的平均亮度（Rec.601 加权灰）。
    diff_energy:
        长度 9 的数组，每个按钮区域与上一帧的帧间差分平均能量。
    lit:
        长度 9 的布尔数组，当前是否亮起。
    rising:
        长度 9 的布尔数组，本帧是否发生「暗→亮」起始点。
    falling:
        长度 9 的布尔数组，本帧是否刚熄灭。
    panel_lum:
        九个按钮的平均亮度。
    panel_bright:
        面板是否处于「点亮」状态。
    panel_present:
        是否真的检测到反应堆面板。
    n_lit:
        当前亮起的按钮个数。
    error_flash:
        是否出现「全体闪蓝」式的报错闪光。
    backend:
        实际使用的计算后端（``cupy`` / ``cv2.cuda`` / ``numpy``）。
    """

    frame_index: int
    br_mean: np.ndarray
    lum_mean: np.ndarray
    diff_energy: np.ndarray
    lit: np.ndarray
    rising: np.ndarray
    falling: np.ndarray
    panel_lum: float
    panel_bright: bool
    panel_present: bool
    n_lit: int
    error_flash: bool
    backend: str

    def lit_buttons(self) -> List[int]:
        """返回当前亮起的按钮编号（1..9）。"""
        return [i + 1 for i in range(len(self.lit)) if bool(self.lit[i])]

    def rising_buttons(self) -> List[int]:
        """返回本帧刚亮起的按钮编号（1..9），即闪烁序列的新增项。"""
        return [i + 1 for i in range(len(self.rising)) if bool(self.rising[i])]


# --------------------------------------------------------------------------
# 后端选择
# --------------------------------------------------------------------------


def detect_backend(preferred: str = "auto") -> str:
    """挑选可用的计算后端。

    优先级：``cv2.cuda`` → ``cupy`` → ``numpy``。
    官方 ``opencv-python`` 的 Windows wheel 不含 CUDA，因此实际可用的是
    ``cupy``；但保留了 ``cv2.cuda`` 分支以便在自带 CUDA 的 OpenCV 上直接用。
    """
    if preferred in ("auto", "cv2.cuda"):
        try:
            import cv2  # noqa: F401

            if getattr(cv2, "cuda", None) is not None and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                return "cv2.cuda"
        except Exception:
            pass
        if preferred == "cv2.cuda":
            raise RuntimeError("请求的 cv2.cuda 后端不可用（OpenCV 未编译 CUDA 支持）")
    if preferred in ("auto", "cupy"):
        try:
            import cupy  # noqa: F401

            if cupy.cuda.runtime.getDeviceCount() > 0:
                return "cupy"
        except Exception:
            pass
        if preferred == "cupy":
            raise RuntimeError("请求的 cupy 后端不可用")
    return "numpy"


def _resize_bgr(
    image: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """Resize a BGR image with OpenCV when available, otherwise use PIL."""
    try:
        import cv2

        if hasattr(cv2, "resize"):
            return cv2.resize(
                image,
                (int(width), int(height)),
                interpolation=getattr(cv2, "INTER_AREA", 3),
            )
    except Exception:
        pass

    from PIL import Image

    rgb = np.ascontiguousarray(image[:, :, ::-1])
    resampling = getattr(Image, "Resampling", Image)
    filter_kind = getattr(
        resampling,
        "BOX" if width < image.shape[1] else "BILINEAR",
        getattr(resampling, "BILINEAR", 1),
    )
    resized = Image.fromarray(rgb, mode="RGB").resize(
        (int(width), int(height)),
        filter_kind,
    )
    return np.ascontiguousarray(np.asarray(resized)[:, :, ::-1])


# --------------------------------------------------------------------------
# 视觉层
# --------------------------------------------------------------------------


class ReactorVision:
    """从画面里读出九个反应堆按钮的状态。

    参数
    ----
    backend:
        ``auto`` / ``cv2.cuda`` / ``cupy`` / ``numpy``。
    work_size:
        ROI 缩放后的边长。实测 32 仍然可分，但 128 是推荐值：
        既把显存流量压到 1/4，又给鼠标指针留足抗噪余量。
    roi:
        用于检测的紧 ROI，默认 :data:`GRID_ROI`。
    lit_br_threshold:
        亮起判据（平均 B-R），默认 :data:`LIT_BR_THRESHOLD`。
    lit_br_release:
        熄灭判据（平均 B-R 迟滞下界），默认 :data:`LIT_BR_RELEASE`。
    bright_lum_threshold:
        面板明暗分界。
    """

    def __init__(
        self,
        backend: str = "auto",
        work_size: int = 128,
        roi: Sequence[int] = GRID_ROI,
        lit_br_threshold: float = LIT_BR_THRESHOLD,
        lit_br_release: float = LIT_BR_RELEASE,
        bright_lum_threshold: float = BRIGHT_LUM_THRESHOLD,
    ) -> None:
        self.backend = detect_backend(backend)
        self.work_size = int(work_size)
        self.roi = tuple(int(v) for v in roi)
        self.lit_br_threshold = float(lit_br_threshold)
        self.lit_br_release = float(lit_br_release)
        self.bright_lum_threshold = float(bright_lum_threshold)

        self._prev_gpu = None  # cupy: 上一帧 ROI（float32）
        self._prev_gpu_np = None  # numpy: 上一帧 ROI（float32）
        self._prev_lit = np.zeros(9, dtype=bool)
        self._frame_index = 0
        self._offset = (0, 0)  # 面板相对基准的偏移 (dx, dy)

        self._scaled_boxes = self._scale_boxes()
        self._index_map = self._build_index_map()

        # 一次 bincount 就算完 9 个按钮 × 3 个量（B-R / 亮度 / 差分能量）：
        # 把索引图复制 3 份并各自偏移 10，最后按 [br0..br8, lum0..lum8, diff0..diff8] 排列。
        # 每组 10 个槽位：0..8 是九个按钮，9 是「不属于任何按钮」的桶（只占位、不使用）。
        idx1d = self._index_map.reshape(-1)
        idx3 = (idx1d[None, :] + (np.arange(3, dtype=np.int64) * 10)[:, None]).ravel()
        counts = np.bincount(idx1d, minlength=10)[:9].astype(np.float32)
        counts3 = np.concatenate([counts, np.float32([1.0])]).repeat(3)

        if self.backend == "cupy":
            import cupy

            self._xp = cupy
            self._idx3_flat = cupy.asarray(idx3)
            self._counts3 = cupy.asarray(counts3)
        else:
            self._xp = np
            self._idx3_flat = idx3
            self._counts3 = counts3

    # -- 几何 -------------------------------------------------------------

    def _scale_boxes(self) -> List[Tuple[int, int, int, int]]:
        """把基准分辨率下的按钮包围盒换算到 ``work_size`` 坐标系。"""
        rx0, ry0, rx1, ry1 = self.roi
        rw = rx1 - rx0
        rh = ry1 - ry0
        sx = self.work_size / float(rw)
        sy = self.work_size / float(rh)
        out = []
        for bx0, by0, bx1, by1 in BUTTON_BOXES:
            nx0 = int(round((bx0 - rx0) * sx))
            nx1 = int(round((bx1 + 1 - rx0) * sx))
            ny0 = int(round((by0 - ry0) * sy))
            ny1 = int(round((by1 + 1 - ry0) * sy))
            nx0 = max(0, min(self.work_size - 1, nx0))
            ny0 = max(0, min(self.work_size - 1, ny0))
            nx1 = max(nx0 + 1, min(self.work_size, nx1))
            ny1 = max(ny0 + 1, min(self.work_size, ny1))
            out.append((nx0, ny0, nx1, ny1))
        return out

    def _build_index_map(self) -> np.ndarray:
        """构造 (work_size, work_size) 的索引图：像素归属哪个按钮，9 = 不属于任何按钮。"""
        idx = np.full((self.work_size, self.work_size), 9, dtype=np.int32)
        for i, (x0, y0, x1, y1) in enumerate(self._scaled_boxes):
            idx[y0:y1, x0:x1] = i
        return idx

    # -- 主流程 -----------------------------------------------------------

    def reset(self) -> None:
        """清空上一帧状态（换场景 / 重新开始任务时调用）。"""
        self._prev_gpu = None
        self._prev_gpu_np = None
        self._prev_lit = np.zeros(9, dtype=bool)
        self._frame_index = 0

    def process(self, frame_bgr: np.ndarray) -> ReactorObservation:
        """处理一帧 BGR 画面，返回观测结果。

        ``frame_bgr`` 必须是 **1280x800 基准分辨率** 的画面。
        """
        if frame_bgr.shape[0] != BASE_HEIGHT or frame_bgr.shape[1] != BASE_WIDTH:
            raise ValueError(f"画面必须是 {BASE_WIDTH}x{BASE_HEIGHT}，收到 {frame_bgr.shape[1]}x{frame_bgr.shape[0]}")

        roi = self._crop(frame_bgr)
        if self.backend == "cupy":
            br_mean, lum_mean, diff_energy = self._compute_cupy(roi)
        elif self.backend == "cv2.cuda":
            br_mean, lum_mean, diff_energy = self._compute_cv2_cuda(roi)
        else:
            br_mean, lum_mean, diff_energy = self._compute_numpy(roi)

        # 亮起判定带迟滞：越过 LIT_BR_THRESHOLD 才点亮，跌破 LIT_BR_RELEASE 才熄灭。
        # 这样按钮中央的鼠标指针或压缩噪声不会让亮起态在阈值附近反复抖动，
        # 「暗→亮起始点」也就不会被重复记录。
        above = br_mean > self.lit_br_threshold
        below = br_mean < self.lit_br_release
        lit = np.where(self._prev_lit & (~below), True, above)

        rising = lit & (~self._prev_lit)
        falling = (~lit) & self._prev_lit
        n_lit = int(lit.sum())
        panel_lum = float(lum_mean.mean())
        panel_bright = panel_lum > self.bright_lum_threshold
        # 面板存在性：未亮起的按钮必须全部是中性色，且整体亮度足够。
        # （亮起的按钮本来就是蓝色，不参与中性判定。）
        idle_br = br_mean[~lit]
        panel_present = bool(
            (idle_br.size == 0 or np.all(np.abs(idle_br) < PANEL_MAX_NEUTRAL_BR))
            and float(np.median(lum_mean)) >= PANEL_MIN_LUM
        )
        error_flash = n_lit >= ERROR_FLASH_MIN_LIT

        self._prev_lit = lit
        self._frame_index += 1

        return ReactorObservation(
            frame_index=self._frame_index - 1,
            br_mean=br_mean,
            lum_mean=lum_mean,
            diff_energy=diff_energy,
            lit=lit,
            rising=rising,
            falling=falling,
            panel_lum=panel_lum,
            panel_bright=panel_bright,
            panel_present=panel_present,
            n_lit=n_lit,
            error_flash=error_flash,
            backend=self.backend,
        )

    # -- ROI 裁剪 ---------------------------------------------------------

    def _crop(self, frame_bgr: np.ndarray) -> np.ndarray:
        """裁出紧 ROI 并等比缩放到 ``work_size``。

        裁剪只做一次内存视图切片；缩放在 CPU 上用 INTER_AREA 完成，
        因此送进显存的只有 ``work_size x work_size x 3``。
        """
        rx0, ry0, rx1, ry1 = self.roi
        rx0 += self._offset[0]
        rx1 += self._offset[0]
        ry0 += self._offset[1]
        ry1 += self._offset[1]
        rx0 = max(0, rx0)
        ry0 = max(0, ry0)
        rx1 = min(BASE_WIDTH, rx1)
        ry1 = min(BASE_HEIGHT, ry1)
        patch = frame_bgr[ry0:ry1, rx0:rx1]
        if patch.size == 0:
            raise RuntimeError("ROI 越界，画面尺寸与基准不符")
        if patch.shape[0] != self.work_size or patch.shape[1] != self.work_size:
            patch = _resize_bgr(patch, self.work_size, self.work_size)
        return np.ascontiguousarray(patch)

    # -- 三套计算后端 -----------------------------------------------------

    def _compute_numpy(self, roi: np.ndarray):
        """纯 numpy 回退路径（没有 GPU 时用）。"""
        f = roi.astype(np.float32)
        b = f[:, :, 0]
        g = f[:, :, 1]
        r = f[:, :, 2]
        br = (b - r).ravel()
        lum = (0.114 * b + 0.587 * g + 0.299 * r).ravel()
        if self._prev_gpu_np is None:
            diff = np.zeros(br.shape, dtype=np.float32)
        else:
            diff = np.abs(f - self._prev_gpu_np).max(axis=2).ravel()
        vals = np.stack((br, lum, diff)).ravel()
        sums = np.bincount(self._idx3_flat, weights=vals, minlength=30)
        self._prev_gpu_np = f
        out = (sums / self._counts3).astype(np.float32)
        # 每组 10 个槽位，9 号是「非按钮」桶，切片时跳过。
        return out[0:9], out[10:19], out[20:29]

    def _compute_cupy(self, roi: np.ndarray):
        """cupy 路径：ROI 上显存，差分与归约全在 GPU。"""
        cupy = self._xp
        gpu = cupy.asarray(roi.astype(np.float32))
        b = gpu[:, :, 0]
        g = gpu[:, :, 1]
        r = gpu[:, :, 2]
        br = (b - r).ravel()
        lum = (0.114 * b + 0.587 * g + 0.299 * r).ravel()
        if self._prev_gpu is None:
            diff = cupy.zeros(br.shape, dtype=cupy.float32)
        else:
            diff = cupy.abs(gpu - self._prev_gpu).max(axis=2).ravel()
        vals = cupy.stack((br, lum, diff)).ravel()
        sums = cupy.bincount(self._idx3_flat, weights=vals, minlength=30)
        self._prev_gpu = gpu
        # 只有 27 个数需要回主机
        payload = (sums / self._counts3).astype(cupy.float32).get()
        # 每组 10 个槽位，9 号是「非按钮」桶，切片时跳过。
        return (
            payload[0:9].astype(np.float32),
            payload[10:19].astype(np.float32),
            payload[20:29].astype(np.float32),
        )

    def _compute_cv2_cuda(self, roi: np.ndarray):
        """cv2.cuda 路径：仅当 OpenCV 编译了 CUDA 支持时可用。"""
        import cv2

        gpu = cv2.cuda.GpuMat()
        gpu.upload(roi)
        prev = getattr(self, "_gpu_prev", None)
        if prev is None:
            diff = cv2.cuda.GpuMat()
            diff.create(gpu.rows, gpu.cols, cv2.CV_8UC3)
            diff.setTo(cv2.cuda.GpuMat(1, 1, cv2.CV_8UC3, (0, 0, 0)))
        else:
            diff = cv2.cuda.absdiff(gpu, prev)

        # 帧间差分能量：转灰后阈值化，统计每块的变化像素占比
        gray = cv2.cuda.cvtColor(gpu, cv2.COLOR_BGR2GRAY)
        mask = cv2.cuda.threshold(gray, 12.0, 255.0, cv2.THRESH_BINARY)

        br_mean = np.zeros(9, dtype=np.float32)
        lum_mean = np.zeros(9, dtype=np.float32)
        diff_energy = np.zeros(9, dtype=np.float32)
        for i, (x0, y0, x1, y1) in enumerate(self._scaled_boxes):
            patch = gpu[y0:y1, x0:x1].get()
            pf = patch.astype(np.float32)
            br_mean[i] = float((pf[:, :, 0] - pf[:, :, 2]).mean())
            lum_mean[i] = float((0.114 * pf[:, :, 0] + 0.587 * pf[:, :, 1] + 0.299 * pf[:, :, 2]).mean())
            m = mask[y0:y1, x0:x1].get()
            diff_energy[i] = float((m > 0).mean())

        self._gpu_prev = gpu
        return br_mean, lum_mean, diff_energy

    # -- 面板重定位 -------------------------------------------------------

    def locate_panel(self, frame_bgr: np.ndarray, search_radius: int = 64) -> Optional[Tuple[int, int]]:
        """在画面里重新找回九宫格原点，返回相对基准的 ``(dx, dy)`` 偏移。

        实现思路：找出所有「中性灰亮面」色块（亮度 150..225、低饱和），
        找出其中尺寸相近、中心构成 3x3 网格的九块，取与基准位置最接近的一组。
        找不到时返回 ``None``。

        这是可选的健壮性功能：只有当角色在地图上走动、导致面板在屏幕上
        偏移时才会用到；站定做任务时不需要调用。
        """
        import cv2

        f = frame_bgr.astype(np.int16)
        b, g, r = f[:, :, 0], f[:, :, 1], f[:, :, 2]
        gray = 0.114 * b + 0.587 * g + 0.299 * r
        sat = np.maximum(np.maximum(b, g), r) - np.minimum(np.minimum(b, g), r)
        mask = ((gray > 150) & (gray < 225) & (sat < 26)).astype(np.uint8)
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n < 9:
            return None

        ref_w = BUTTON_BOXES[0][2] - BUTTON_BOXES[0][0] + 1
        ref_h = BUTTON_BOXES[0][3] - BUTTON_BOXES[0][1] + 1
        cand = []
        for i in range(1, n):
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            if abs(w - ref_w) > ref_w * 0.5 or abs(h - ref_h) > ref_h * 0.5:
                continue
            cand.append((cents[i][0], cents[i][1], w, h))
        if len(cand) < 9:
            return None

        # 期望的相对排布（以按钮 1 为基准）
        ref = np.array([[c[0] - BUTTON_CENTERS[0][0], c[1] - BUTTON_CENTERS[0][1]] for c in BUTTON_CENTERS])
        best = None
        for c in cand:
            pts = np.array([[p[0] - c[0], p[1] - c[1]] for p in cand])
            # 用最近邻匹配找最佳九宫格
            used = set()
            total = 0.0
            ok = True
            for target in ref:
                dists = [
                    ((pts[j][0] - target[0]) ** 2 + (pts[j][1] - target[1]) ** 2, j)
                    for j in range(len(pts))
                    if j not in used
                ]
                if not dists:
                    ok = False
                    break
                d, j = min(dists)
                if d > 40**2:
                    ok = False
                    break
                used.add(j)
                total += d
            if not ok:
                continue
            dx = int(round(c[0] - BUTTON_CENTERS[0][0]))
            dy = int(round(c[1] - BUTTON_CENTERS[0][1]))
            if abs(dx) > search_radius or abs(dy) > search_radius:
                continue
            score = total + (dx * dx + dy * dy)
            if best is None or score < best[0]:
                best = (score, dx, dy)
        if best is None:
            return None
        return best[1], best[2]

    def apply_offset(self, dx: int, dy: int) -> None:
        """设置 ROI 相对基准的偏移（由 :meth:`locate_panel` 得到）。"""
        self._offset = (int(dx), int(dy))
        self._prev_gpu = None
        self._prev_gpu_np = None


# --------------------------------------------------------------------------
# 坐标映射与鼠标
# --------------------------------------------------------------------------


class CoordinateMapper:
    """把 1280x800 基准坐标映射到真实屏幕像素。

    两种映射模式：

    ``window``
        以游戏窗口客户区为基准。窗口化与全屏都正确（推荐）。
    ``screen``
        以整块屏幕为基准，把 1280x800 的画面居中铺在屏幕上。
        没有窗口句柄时的兜底方案。
    """

    def __init__(self, mode: str = "window") -> None:
        if mode not in ("window", "screen"):
            raise ValueError("mode 只能是 'window' 或 'screen'")
        self.mode = mode
        self._origin = (0.0, 0.0)
        self._size = (float(BASE_WIDTH), float(BASE_HEIGHT))

    def set_viewport(self, origin_x: float, origin_y: float, width: float, height: float) -> None:
        """设置目标区域在屏幕上的位置与大小。"""
        self._origin = (float(origin_x), float(origin_y))
        self._size = (float(width), float(height))

    def map_point(self, base_x: float, base_y: float) -> Tuple[int, int]:
        """把基准画面坐标换算成屏幕像素坐标。"""
        ox, oy = self._origin
        w, h = self._size
        sx = ox + base_x * (w / BASE_WIDTH)
        sy = oy + base_y * (h / BASE_HEIGHT)
        return int(round(sx)), int(round(sy))

    def map_button(self, index: int) -> Tuple[int, int]:
        """把第 ``index`` 个按钮（1..9）的中心换算成屏幕像素坐标。"""
        cx, cy = BUTTON_CENTERS[index - 1]
        return self.map_point(cx, cy)

    def map_button_relative(self, index: int) -> Tuple[float, float]:
        """返回按钮中心相对目标区域中心的偏移（已按比例缩放）。"""
        rx, ry = BUTTON_CENTERS_REL[index - 1]
        w, h = self._size
        return rx * (w / BASE_WIDTH), ry * (h / BASE_HEIGHT)


class MouseController:
    """Windows 鼠标控制（纯 ctypes，无第三方依赖）。"""

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("MouseController 仅支持 Windows")
        self._user32 = ctypes.windll.user32
        self._screen_w = self._user32.GetSystemMetrics(0)
        self._screen_h = self._user32.GetSystemMetrics(1)

    def move(self, x: int, y: int) -> None:
        """把光标移动到屏幕坐标 (x, y)。"""
        self._user32.SetCursorPos(int(x), int(y))

    def click(self, x: int, y: int, delay: float = 0.04) -> None:
        """移动到 (x, y) 并左键点击一次。"""
        self.move(x, y)
        time.sleep(delay)
        self._user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(delay)
        self._user32.mouse_event(self.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# 窗口抓取
# --------------------------------------------------------------------------


class WindowCapture:
    """用 GDI 抓取指定窗口客户区（纯 ctypes，无第三方依赖）。"""

    def __init__(self, hwnd: int) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowCapture 仅支持 Windows")
        self.hwnd = int(hwnd)
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32

    @staticmethod
    def find_window(title_substr: str) -> int:
        """按标题子串查找窗口（返回第一个匹配句柄，找不到抛异常）。"""
        if os.name != "nt":
            raise RuntimeError("WindowCapture 仅支持 Windows")
        user32 = ctypes.windll.user32
        found = []
        from ctypes import wintypes

        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT),
        ]
        user32.GetWindowRect.restype = wintypes.BOOL

        @ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        def enum_windows_proc(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_substr.lower() in buf.value.lower():
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        width = max(0, rect.right - rect.left)
                        height = max(0, rect.bottom - rect.top)
                        if width > 0 and height > 0:
                            found.append((width * height, int(hwnd)))
            return 1

        user32.EnumWindows(enum_windows_proc, None)
        if not found:
            raise RuntimeError(f"没有找到标题包含 {title_substr!r} 的窗口")
        return max(found)[1]

    def client_rect(self) -> Tuple[int, int, int, int]:
        """返回客户区在屏幕上的 ``(x, y, width, height)``。"""
        from ctypes import wintypes

        self._user32.GetClientRect.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT),
        ]
        self._user32.GetClientRect.restype = wintypes.BOOL
        self._user32.ClientToScreen.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.POINT),
        ]
        self._user32.ClientToScreen.restype = wintypes.BOOL

        rect = wintypes.RECT()
        self._user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        self._user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top

    def grab(self) -> np.ndarray:
        """抓取一帧客户区画面，返回 BGR ndarray。"""
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        x, y, w, h = self.client_rect()
        if w <= 0 or h <= 0:
            raise RuntimeError("窗口客户区尺寸非法")

        hwnd_dc = user32.GetDC(self.hwnd)
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        old = gdi32.SelectObject(mem_dc, bitmap)
        try:
            gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)  # SRCCOPY

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_uint32),
                    ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # 负值 = 自上而下
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0  # BI_RGB

            buf = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(bmi), 0)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            return np.ascontiguousarray(arr[:, :, :3])  # BGRA -> BGR
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(self.hwnd, hwnd_dc)


# --------------------------------------------------------------------------
# 控制层
# --------------------------------------------------------------------------


@dataclass
class RoundResult:
    """一轮（一次序列播放）的结果。"""

    index: int
    sequence: List[int]
    start_frame: int
    end_frame: int
    error: bool = False

    def __str__(self) -> str:
        seq = ", ".join(str(i) for i in self.sequence)
        return f"第 {self.index} 轮，识别序列为 [{seq}]"


class ReactorController:
    """状态机：记录闪烁序列 → 切换到点击模式 → 遇到蓝光报错就重置。

    参数
    ----
    vision:
        视觉层实例。
    mapper:
        坐标映射器；为空则不产生真实点击（离线回放模式）。
    mouse:
        鼠标控制器；为空则只记录不点击。
    click_delay:
        两次点击之间的间隔（秒）。
    quiet_frames:
        多少帧没有新闪烁就认为序列播放结束（30fps 下 12 帧 ≈ 0.4 秒）。
    min_flash_gap:
        同一次闪烁的最小间隔（帧）。低于该间隔的再次亮起视为抖动，忽略。
    min_diff_energy:
        帧间差分能量门槛。**只有「暗→亮」那一帧确实发生了足够的画面变化**
        （``diff_energy`` ≥ 该值）才记录事件。

        实测（1280x800 / work_size=128 / 732 帧）：真实闪烁起始帧的
        diff_energy 在 132.6 ~ 157.1 之间，而其余帧的 p95 只有 13.2。
        取 30 有 4 倍以上裕度，既能挡住阈值附近的抖动，又不会漏掉真事件。
        设为 0 可关闭该门（仅靠颜色判据）。
    press_trigger:
        ``dim``（默认）—— 面板由「点亮」转为「变暗」时进入点击阶段。
        这正是演示视频里的真实行为：序列播放时面板亮着、按钮依次闪蓝，
        播放结束面板整体变暗并等待玩家按键。
        ``quiet`` —— 静默若干帧后进入点击阶段（用于面板不变暗的游戏）。
        ``either`` —— 两者任一满足即进入。
    error_policy:
        ``multi_lit``（默认）—— 同时有 2 个及以上按钮发蓝即判定为
        「报错 / 终态闪光」，立刻停止点击、重置状态、重新记录序列。
        ``click_only`` —— 只有在点击阶段出现发蓝才算报错。
        ``off`` —— 不把发蓝当作报错。
    """

    def __init__(
        self,
        vision: ReactorVision,
        mapper: Optional[CoordinateMapper] = None,
        mouse: Optional[MouseController] = None,
        click_delay: float = 0.12,
        quiet_frames: int = 24,
        min_flash_gap: int = 3,
        min_diff_energy: float = 30.0,
        press_trigger: str = "dim",
        error_policy: str = "multi_lit",
        auto_click: bool = True,
    ) -> None:
        if press_trigger not in ("dim", "quiet", "either"):
            raise ValueError("press_trigger 只能是 'dim' / 'quiet' / 'either'")
        if error_policy not in ("multi_lit", "click_only", "off"):
            raise ValueError("error_policy 只能是 'multi_lit' / 'click_only' / 'off'")
        self.vision = vision
        self.mapper = mapper
        self.mouse = mouse
        self.click_delay = float(click_delay)
        self.quiet_frames = int(quiet_frames)
        self.min_flash_gap = int(min_flash_gap)
        self.min_diff_energy = float(min_diff_energy)
        self.press_trigger = press_trigger
        self.error_policy = error_policy
        self.auto_click = bool(auto_click)

        self.sequence: List[int] = []
        self.rounds: List[RoundResult] = []
        self.state = "record"  # record / click / error
        self.error_count = 0
        self._last_event_frame = -(10**9)
        self._round_start = 0
        self._round_index = 0
        self._click_cursor = 0
        self._panel_was_bright = False
        self._finished = False

    # -- 主入口 -----------------------------------------------------------

    def update(self, obs: ReactorObservation) -> Optional[RoundResult]:
        """喂入一帧观测，推进状态机。

        返回值：某一轮刚刚收尾时返回该轮结果，否则返回 ``None``。
        """
        if self._finished:
            return None

        if not obs.panel_present:
            # 面板消失 —— 任务结束或还没开始
            if self.state == "record" and self.sequence:
                self._close_round(obs.frame_index, error=False)
            if obs.n_lit == 0 and obs.panel_lum < PANEL_MIN_LUM:
                self._finished = True
            return None

        # 报错 / 终态闪光：立刻停手、重置状态、重新开始记录序列
        is_error = self._is_error_flash(obs)
        if is_error:
            if self.sequence:
                # 本轮序列已经播放出来，但没来得及执行 —— 仍然如实记录
                self._close_round(obs.frame_index, error=True)
            self.state = "error"
            self.error_count += 1
            self.sequence.clear()
            self._click_cursor = 0
            self._last_event_frame = obs.frame_index
            self._panel_was_bright = obs.panel_bright
            return None

        if self.state == "error":
            # 报错后进入冷却，等面板回到常态再重新记录
            if obs.n_lit == 0:
                self.state = "record"
                self._round_start = obs.frame_index
                self._panel_was_bright = obs.panel_bright
            return None

        # 记录阶段：抓「暗→亮」起始点
        if self.state == "record":
            for btn in obs.rising_buttons():
                if obs.frame_index - self._last_event_frame < self.min_flash_gap:
                    continue
                # 帧间差分确认：这一帧必须真的发生了画面变化。
                if self.min_diff_energy > 0:
                    energy = float(obs.diff_energy[btn - 1])
                    if energy < self.min_diff_energy:
                        continue
                self.sequence.append(btn)
                self._last_event_frame = obs.frame_index
            if not self.sequence:
                self._round_start = obs.frame_index

        # 面板由亮变暗 = 系统不再闪烁 → 切换到点击模式
        if self.state == "record" and self.sequence:
            went_dim = self._panel_was_bright and not obs.panel_bright
            quiet = (obs.frame_index - self._last_event_frame) >= self.quiet_frames
            trigger = {
                "dim": went_dim,
                "quiet": quiet,
                "either": went_dim or quiet,
            }[self.press_trigger]
            if trigger:
                self.state = "click"
                self._click_cursor = 0

        # 点击阶段：按顺序把记录的按钮点一遍
        if self.state == "click":
            if self.auto_click and self.mapper is not None and self.mouse is not None:
                self._perform_clicks()
            else:
                # 回放模式（不做真实点击）：按帧推进指针
                self._click_cursor += 1
            if self._click_cursor >= len(self.sequence):
                result = self._close_round(obs.frame_index, error=False)
                self.state = "record"
                self.sequence.clear()
                self._click_cursor = 0
                self._round_start = obs.frame_index
                self._panel_was_bright = obs.panel_bright
                return result

        self._panel_was_bright = obs.panel_bright
        return None

    def _is_error_flash(self, obs: ReactorObservation) -> bool:
        """判断本帧是否属于「蓝光报错 / 终态闪光」。"""
        if self.error_policy == "off":
            return False
        if self.error_policy == "multi_lit":
            return obs.error_flash
        # click_only：只有已经进入点击阶段后还看到发蓝，才算报错
        return self.state == "click" and obs.n_lit > 0

    # -- 内部 -------------------------------------------------------------

    def _perform_clicks(self) -> None:
        """执行尚未完成的点击（每次 update 最多点一个，避免节奏过快）。"""
        if self._click_cursor >= len(self.sequence):
            return
        btn = self.sequence[self._click_cursor]
        x, y = self.mapper.map_button(btn)
        self.mouse.click(x, y)
        self._click_cursor += 1
        if self.click_delay:
            time.sleep(self.click_delay)

    def _close_round(self, frame_index: int, error: bool) -> RoundResult:
        self._round_index += 1
        result = RoundResult(
            index=self._round_index,
            sequence=list(self.sequence),
            start_frame=self._round_start,
            end_frame=frame_index,
            error=error,
        )
        self.rounds.append(result)
        return result

    def reset(self) -> None:
        """清空序列与状态机（报错后重新开始）。"""
        self.sequence.clear()
        self.state = "record"
        self._click_cursor = 0
        self._panel_was_bright = False


# --------------------------------------------------------------------------
# 离线回放
# --------------------------------------------------------------------------


def _load_frame(path: str) -> Optional[np.ndarray]:
    """读一张 BGR 帧（Windows 下走 imdecode，避免中文路径问题）。"""
    try:
        import cv2

        if hasattr(cv2, "imdecode"):
            raw = np.fromfile(path, dtype=np.uint8)
            if raw.size == 0:
                return None
            return cv2.imdecode(raw, getattr(cv2, "IMREAD_COLOR", 1))
    except Exception:
        pass

    try:
        from PIL import Image

        with Image.open(path) as source:
            rgb = np.asarray(source.convert("RGB"))
        return np.ascontiguousarray(rgb[:, :, ::-1])
    except OSError:
        return None


def replay(
    frame_dir: str,
    vision: ReactorVision,
    controller: Optional[ReactorController] = None,
    max_frames: Optional[int] = None,
    verbose: bool = True,
) -> Tuple[List[RoundResult], ReactorController]:
    """离线回放一整个帧目录，返回 (每一轮的结果, 控制器)。

    ``controller`` 为空时会自动创建一个不做真实点击的控制器。
    """
    if controller is None:
        controller = ReactorController(vision, auto_click=False)
    files = sorted(f for f in os.listdir(frame_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if max_frames:
        files = files[:max_frames]

    t0 = time.perf_counter()
    for name in files:
        img = _load_frame(os.path.join(frame_dir, name))
        if img is None:
            continue
        obs = vision.process(img)
        controller.update(obs)
    dt = time.perf_counter() - t0

    if verbose:
        print(f"后端: {vision.backend}    工作分辨率: {vision.work_size}x{vision.work_size}")
        print(f"回放 {len(files)} 帧耗时 {dt:.3f}s  ({dt / max(1, len(files)) * 1000:.3f} ms/帧)")
        for r in controller.rounds:
            print(f"  {r}   (帧 {r.start_frame}..{r.end_frame})")
        if controller.error_count:
            print(f"  检测到 {controller.error_count} 次蓝光报错闪光（已重置序列）")
    return controller.rounds, controller


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="启动反应堆视觉自动化")
    parser.add_argument("--replay", metavar="DIR", help="离线回放：帧目录")
    parser.add_argument("--frames", type=int, default=0, help="最多回放多少帧（0=全部）")
    parser.add_argument("--size", type=int, default=128, help="ROI 工作分辨率")
    parser.add_argument("--backend", default="auto", help="auto / cv2.cuda / cupy / numpy")
    parser.add_argument("--live", action="store_true", help="真机运行：抓窗口并自动点击")
    parser.add_argument("--window", default="Among Us", help="游戏窗口标题子串")
    parser.add_argument("--fps", type=float, default=30.0, help="真机采样帧率")
    parser.add_argument(
        "--status-json",
        default=os.environ.get("AU_SKILL_STATUS_PATH", ""),
        help="Write live skill state for the in-game HUD.",
    )
    args = parser.parse_args(argv)

    vision = ReactorVision(backend=args.backend, work_size=args.size)

    if args.replay:
        rounds, _ = replay(args.replay, vision, max_frames=args.frames or None)
        for r in rounds:
            print(r)
        return 0

    if args.live:
        status_path = args.status_json or None

        def publish_status(
            state: str,
            *,
            message: str = "",
            round_index: int = 0,
            sequence: Optional[Sequence[int]] = None,
            lit_buttons: Optional[Sequence[int]] = None,
            error: str = "",
        ) -> None:
            _write_status_file(
                status_path,
                {
                    "state": state,
                    "message": message,
                    "round": int(round_index),
                    "totalRounds": 5,
                    "sequence": list(sequence or ()),
                    "litButtons": list(lit_buttons or ()),
                    "backend": vision.backend,
                    "error": error,
                },
            )

        try:
            publish_status("starting", message="正在查找游戏窗口")
            hwnd = WindowCapture.find_window(args.window)
            cap = WindowCapture(hwnd)
            mapper = CoordinateMapper("window")
            ox, oy, w, h = cap.client_rect()
            mapper.set_viewport(ox, oy, w, h)
            mouse = MouseController()
            ctrl = ReactorController(vision, mapper, mouse)
            print(f"窗口客户区 {w}x{h} @ ({ox},{oy})  后端 {vision.backend}")
            publish_status(
                "running",
                message=f"窗口 {w}x{h}，等待闪烁",
            )
            period = 1.0 / args.fps
            last_hud_update = 0.0
            while True:
                t = time.perf_counter()
                frame = cap.grab()
                if frame.shape[0] != BASE_HEIGHT or frame.shape[1] != BASE_WIDTH:
                    frame = _fit_to_base(frame)
                obs = vision.process(frame)
                res = ctrl.update(obs)
                if res is not None:
                    print(res)
                    publish_status(
                        "round_complete",
                        message=str(res),
                        round_index=res.index,
                        sequence=res.sequence,
                        lit_buttons=obs.lit_buttons(),
                        error="blue_error" if res.error else "",
                    )
                elif obs.error_flash:
                    publish_status(
                        "error",
                        message="检测到蓝光报错，重新记录",
                        round_index=len(ctrl.rounds),
                        sequence=ctrl.sequence,
                        lit_buttons=obs.lit_buttons(),
                        error="blue_error",
                    )
                elif t - last_hud_update >= 0.1:
                    last_hud_update = t
                    publish_status(
                        ctrl.state,
                        message=("记录闪烁" if ctrl.state == "record" else "执行点击"),
                        round_index=len(ctrl.rounds),
                        sequence=ctrl.sequence,
                        lit_buttons=obs.lit_buttons(),
                    )
                if ctrl._finished:
                    publish_status(
                        "completed",
                        message="反应堆面板已关闭",
                        round_index=len(ctrl.rounds),
                        sequence=ctrl.rounds[-1].sequence if ctrl.rounds else (),
                    )
                    return 0
                dt = time.perf_counter() - t
                if dt < period:
                    time.sleep(period - dt)
        except Exception as exc:
            publish_status(
                "failed",
                message="技能运行失败",
                error=str(exc),
            )
            raise

    parser.print_help()
    return 1


def _fit_to_base(frame: np.ndarray) -> np.ndarray:
    """把任意尺寸画面缩放到 1280x800（仅在窗口不是 1280x800 时使用）。"""
    return _resize_bgr(frame, BASE_WIDTH, BASE_HEIGHT)


if __name__ == "__main__":
    sys.exit(main())
