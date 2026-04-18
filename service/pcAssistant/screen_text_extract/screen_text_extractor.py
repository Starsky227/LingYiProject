"""
屏幕文字提取服务

持续截取屏幕指定区域，通过图像 hash 比对检测变化，
变化足够大时使用 PaddleOCR 提取文字，按逻辑判断是否递交给 AI：

1. 新文字比旧文字更长且包含旧文字（逐字出现中）→ 不递交，等待
2. 新文字与旧文字相同（文字已完整展示）→ 递交一次
3. 新文字与旧文字完全不同 且 旧文字未递交 → 先递交旧文字，再继续监测
"""

import hashlib
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional, Tuple

# PaddlePaddle 3.x Windows OneDNN 兼容性修复
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

import numpy as np

logger = logging.getLogger(__name__)

# 延迟导入重量级依赖
_mss = None
_PaddleOCR = None


def _lazy_import_mss():
    global _mss
    if _mss is None:
        import mss as _m
        _mss = _m
    return _mss


def _lazy_import_paddleocr():
    global _PaddleOCR
    if _PaddleOCR is None:
        from paddleocr import PaddleOCR as _cls
        _PaddleOCR = _cls
    return _PaddleOCR


def _image_hash(img_array: np.ndarray) -> str:
    """计算图像的感知 hash（dHash 8x8 = 64-bit）"""
    from PIL import Image
    img = Image.fromarray(img_array).convert("L").resize((9, 8), Image.LANCZOS)
    pixels = np.array(img)
    diff = pixels[:, 1:] > pixels[:, :-1]
    return hashlib.md5(diff.tobytes()).hexdigest()


def _hamming_distance(h1: str, h2: str) -> int:
    """两个 hex hash 字符串的汉明距离"""
    b1 = int(h1, 16)
    b2 = int(h2, 16)
    return bin(b1 ^ b2).count("1")


class ScreenTextExtractor:
    """屏幕文字提取服务

    Args:
        text_callback: 文字递交回调，签名 (text: str) -> None
        region: 截屏区域 (left, top, width, height)；None 表示全屏
        interval: 截屏检测间隔（秒）
        hash_threshold: dHash 汉明距离阈值，超过此值认为画面有变化
        stable_count: 连续多少次 OCR 结果相同才认为文字已稳定
        lang: PaddleOCR 语言，默认 "ch"
    """

    def __init__(
        self,
        text_callback: Callable[[str], None],
        region: Optional[Tuple[int, int, int, int]] = None,
        interval: float = 1.0,
        hash_threshold: int = 5,
        stable_count: int = 2,
        lang: str = "ch",
    ):
        self._text_callback = text_callback
        self._region = region
        self._interval = interval
        self._hash_threshold = hash_threshold
        self._stable_count = stable_count
        self._lang = lang

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ocr = None

        # 状态跟踪
        self._last_hash: Optional[str] = None
        self._last_ocr_text: str = ""
        self._last_submitted_text: str = ""
        self._stable_hits: int = 0  # 连续相同次数

    @property
    def is_running(self) -> bool:
        return self._running

    def set_region(self, left: int, top: int, width: int, height: int) -> None:
        """动态更新截屏区域"""
        self._region = (left, top, width, height)
        # 切换区域后重置状态
        self._reset_state()

    def _reset_state(self) -> None:
        self._last_hash = None
        self._last_ocr_text = ""
        self._last_submitted_text = ""
        self._stable_hits = 0

    def start(self) -> bool:
        if self._running:
            return True
        if self._region is None:
            logger.error("[ScreenOCR] 未设置截屏区域")
            return False

        # 初始化 PaddleOCR
        try:
            cls = _lazy_import_paddleocr()
            self._ocr = cls(use_textline_orientation=True, lang=self._lang, enable_mkldnn=False)
        except Exception as e:
            logger.error(f"[ScreenOCR] PaddleOCR 初始化失败: {e}")
            return False

        self._reset_state()
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="screen-ocr-capture",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[ScreenOCR] 已启动，区域={self._region}, 间隔={self._interval}s")
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._ocr = None
        logger.info("[ScreenOCR] 已停止")

    # ------------------------------------------------------------------ #
    #  截屏循环
    # ------------------------------------------------------------------ #

    def _capture_loop(self) -> None:
        mss_mod = _lazy_import_mss()
        # hash 比对很轻量，用短间隔快速检测画面变化
        _HASH_POLL_INTERVAL = 0.3

        with mss_mod.mss() as sct:
            while self._running:
                try:
                    did_ocr = self._tick(sct)
                except Exception as e:
                    logger.error(f"[ScreenOCR] 截屏循环异常: {e}")
                    did_ocr = False

                if did_ocr:
                    # OCR 刚完成，等待配置的间隔再截下一帧（避免 CPU 过载）
                    time.sleep(self._interval)
                else:
                    # 仅 hash 比对，快速轮询
                    time.sleep(_HASH_POLL_INTERVAL)

        # 循环结束：如果有未递交的文字，最后递交一次
        if self._last_ocr_text and self._last_ocr_text != self._last_submitted_text:
            self._submit(self._last_ocr_text)

    def _tick(self, sct) -> bool:
        """执行一次截屏检测。返回 True 表示本次触发了 OCR（CPU 密集），False 仅做了 hash 比对。"""
        region = self._region
        if region is None:
            return False

        left, top, width, height = region
        monitor = {"left": left, "top": top, "width": width, "height": height}
        screenshot = sct.grab(monitor)
        img = np.array(screenshot)[:, :, :3]  # BGRA → BGR

        # 计算 hash，与上一帧比较
        current_hash = _image_hash(img)

        if self._last_hash is not None:
            distance = _hamming_distance(current_hash, self._last_hash)
            if distance < self._hash_threshold:
                # 画面几乎没变：检查当前 OCR 文字是否已稳定
                if self._last_ocr_text and self._last_ocr_text != self._last_submitted_text:
                    self._stable_hits += 1
                    if self._stable_hits >= self._stable_count:
                        self._submit(self._last_ocr_text)
                self._last_hash = current_hash
                return False

        # 画面有变化 → OCR
        self._last_hash = current_hash
        new_text = self._run_ocr(img)

        if not new_text:
            return True  # OCR 已执行（即使结果为空）

        old_text = self._last_ocr_text

        if not old_text:
            # 首次提取
            self._last_ocr_text = new_text
            self._stable_hits = 0
            return True

        # 判断文字变化逻辑
        if new_text == old_text:
            # 情况 2：文字相同（已完整展示）
            self._stable_hits += 1
            if self._stable_hits >= self._stable_count and old_text != self._last_submitted_text:
                self._submit(old_text)
        elif self._is_text_growing(old_text, new_text):
            # 情况 1：新文字是旧文字的延续（逐字出现中）→ 不递交，继续等待
            self._last_ocr_text = new_text
            self._stable_hits = 0
        else:
            # 情况 3：文字完全不同
            if old_text != self._last_submitted_text:
                # 旧文字还没来得及递交 → 先递交旧文字
                self._submit(old_text)
            self._last_ocr_text = new_text
            self._stable_hits = 0

        return True

    # ------------------------------------------------------------------ #
    #  OCR
    # ------------------------------------------------------------------ #

    def _run_ocr(self, img: np.ndarray) -> str:
        """对图像运行 PaddleOCR，返回拼接后的文本。

        排序规则：先按 Y 坐标从上到下分行（Y 差值在行高阈值内视为同一行），
        同一行内按 X 坐标从左到右排序并拼接。
        """
        if self._ocr is None:
            return ""
        try:
            results = self._ocr.predict(img)
            if not results:
                return ""

            # 收集所有文本块及其位置 (top_y, left_x, height, text)
            blocks: list[tuple[float, float, float, str]] = []
            for r in results:
                polys = r["dt_polys"]
                texts = r["rec_texts"]
                for poly, text in zip(polys, texts):
                    if not text.strip():
                        continue
                    ys = [p[1] for p in poly]
                    xs = [p[0] for p in poly]
                    top_y = min(ys)
                    left_x = min(xs)
                    height = max(ys) - top_y
                    blocks.append((top_y, left_x, height, text.strip()))

            if not blocks:
                return ""

            # 按 top_y 排序后，将 Y 坐标接近的块归为同一行
            blocks.sort(key=lambda b: (b[0], b[1]))
            row_threshold = max(b[2] for b in blocks) * 0.5 if blocks else 20

            rows: list[list[tuple[float, float, float, str]]] = []
            current_row: list[tuple[float, float, float, str]] = [blocks[0]]
            current_row_y = blocks[0][0]

            for block in blocks[1:]:
                if abs(block[0] - current_row_y) <= row_threshold:
                    current_row.append(block)
                else:
                    rows.append(current_row)
                    current_row = [block]
                    current_row_y = block[0]
            rows.append(current_row)

            # 每行内按 left_x 排序，拼接文本
            lines = []
            for row in rows:
                row.sort(key=lambda b: b[1])
                line_text = " ".join(b[3] for b in row)
                lines.append(line_text)

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[ScreenOCR] OCR 失败: {e}")
            return ""

    # ------------------------------------------------------------------ #
    #  文字变化判断
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_text_growing(old_text: str, new_text: str) -> bool:
        """判断新文字是否是旧文字的延续（逐字出现场景）

        条件：新文字更长，且旧文字是新文字的前缀（或被包含）
        """
        old_clean = old_text.replace("\n", "").replace(" ", "")
        new_clean = new_text.replace("\n", "").replace(" ", "")

        if len(new_clean) <= len(old_clean):
            return False

        # 旧文字是新文字的前缀
        if new_clean.startswith(old_clean):
            return True

        # 旧文字被完整包含在新文字中（允许 OCR 微小差异）
        if old_clean in new_clean:
            return True

        return False

    # ------------------------------------------------------------------ #
    #  递交
    # ------------------------------------------------------------------ #

    # 测试日志路径
    _TEST_LOG = os.path.join(os.path.dirname(__file__), "screen_text_extract_test.txt")

    def _submit(self, text: str) -> None:
        """将文字记录到测试文件，并通过回调递交给 AI"""
        if not text.strip():
            return
        self._last_submitted_text = text
        self._stable_hits = 0
        logger.info(f"[ScreenOCR] 记录文字 ({len(text)} 字): {text[:80]}...")
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._TEST_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}]\n{text}\n{'─' * 40}\n")
        except Exception as e:
            logger.error(f"[ScreenOCR] 写入测试日志失败: {e}")
        if self._text_callback:
            try:
                self._text_callback(text)
            except Exception as e:
                logger.error(f"[ScreenOCR] 回调执行失败: {e}")
