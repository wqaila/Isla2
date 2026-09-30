#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
视频台词识别工具（增强版）
支持语音识别（openai-whisper/faster-whisper）、OCR（PaddleOCR）
输出结果：纯文本台词（去重，用于喂给微调流水线）、SRT 字幕（保留时间轴），
或 **jsonl**（逐句带溯源：BV 号 + 时间点 + 通道 + 置信度）。

采集质量相关的能力（见 text_clean.py）：
- 规范化（繁→简、全半角统一、去表情残留）
- 长度红线（4~100 字；超长按标点切句）
- 可选严格模式（要求标点收尾，默认关）
- 去重同时保留频次

说话人分离：`speaker_diarization`（pyannote 那类重型方案）仍未实现，传 True 只会警告。
替代方案是**轻量语气打标** —— 用 `--speaker-filter` 让本地 LLM 给每句打
「像不像角色本人」的分，低分剔除，见 speaker_filter.py。
"""

import os

# 禁用 PaddlePaddle PIR 模式以解决兼容性问题
os.environ['FLAGS_use_pir_mode'] = 'false'
os.environ['FLAGS_enable_pir_api'] = '0'
os.environ['FLAGS_cinn_new_group_scheduler'] = '0'
os.environ['FLAGS_enable_filelock'] = '0'

import argparse
import difflib
import math
import sys
import time
import tempfile
from typing import List, Tuple, Dict, Any, Optional
import re

# ⚠️ cv2 / numpy / moviepy 这些第三方依赖**不要写成裸 import**。
#    裸导入会让 `python main.py --help` 在装依赖之前就崩掉 —— 用户连参数说明
#    都看不到，只能先去猜着装依赖。这里统一放进 try 块，缺依赖时给出可执行的指引。
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    CV2_AVAILABLE = False

# 可选依赖检查
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    # 这里 import 只是为了探测 whisper 能否加载，模块本身在
    # _transcribe_with_openai_whisper() 里按需再导入一次。
    # （静态检查会报 "imported but unused"，属于预期，不要删）
    import whisper  # noqa: F401
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False

# 简繁体转换
try:
    from opencc import OpenCC
    OPENCC_AVAILABLE = True
except ImportError:
    OPENCC_AVAILABLE = False

try:
    from moviepy.editor import VideoFileClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    VideoFileClip = None
    MOVIEPY_AVAILABLE = False

# 同目录的纯逻辑模块（只依赖标准库）。用路径兜底是为了让
# 「cd chibtaici && python main.py」和「python chibtaici/main.py」都能跑。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_clean import (  # noqa: E402
    CHANNEL_MERGED,
    CHANNEL_OCR,
    CHANNEL_VOICE,
    build_record,
    check_line,
    dedup_with_counts,
    format_quality_report,
    normalize_text,
    split_long_text,
    text_frequencies,
    write_jsonl,
)
from speaker_filter import LlmSpeakerFilter  # noqa: E402


class RoleLineExtractor:
    def __init__(self, video_path: str,
                 whisper_model: str = "large-v3",
                 device: str = "auto",
                 ocr_region: Tuple[float, float, float, float] = (0, 0.7, 1, 0.95),
                 ocr_interval: int = 5,
                 use_faster_whisper: bool = False,
                 speaker_diarization: bool = False,
                 ocr_method: str = "paddle",
                 enable_ocr: bool = True,
                 enable_voice: bool = True,
                 interactive_ocr: bool = False,
                 video_id: str = None,
                 initial_prompt: str = None,
                 vad_filter: bool = True,
                 ocr_skip_static: bool = True,
                 clean: bool = True,
                 min_len: int = 4,
                 max_len: int = 100,
                 require_complete: bool = False,
                 skip_head: float = 0.0,
                 skip_tail: float = 0.0,
                 speaker_filter: "LlmSpeakerFilter" = None):
        """
        :param video_path: 视频文件路径
        :param whisper_model: Whisper 模型大小。默认 large-v3 —— base 的中文
               错误率偏高（人名、专有名词尤其），GPU 能跑就别用它。
        :param device: 设备 "cpu"/"cuda"/"auto"
        :param ocr_region: OCR 区域相对坐标 (x1, y1, x2, y2)
        :param ocr_interval: OCR 帧间隔（减少 OCR 次数提升速度）
        :param use_faster_whisper: 是否使用 faster-whisper（更快更轻量）。
               传 True 表示**强制**用它；传 False（默认）则自动挑一个已安装的后端
               （优先尊重默认的 openai-whisper，它没装就用 faster-whisper）
        :param speaker_diarization: 是否启用说话人分离（**未实现**，见模块 docstring）
        :param ocr_method: OCR 方法 "paddle"
        :param enable_ocr: 是否启用 OCR 识别
        :param enable_voice: 是否启用语音识别
        :param interactive_ocr: 是否启用交互式 OCR 区域设置（命令行输入）
        :param video_id: 溯源用的视频 ID（BV 号）；不传则留空
        :param initial_prompt: 注入 whisper 的提示词，放专有名词能明显提升人名准确率
        :param vad_filter: 是否用 VAD 切句（faster-whisper 支持；减少幻觉与整段沉默）
        :param ocr_skip_static: 字幕区没变化时跳过 OCR（区域哈希比对）
        :param clean: 是否做文本规范化与质量过滤（默认开；关掉就是原始的「只合并」行为）
        :param min_len / max_len: 台词长度红线（字数）
        :param require_complete: 是否要求标点收尾（默认关，ASR 输出常缺标点）
        :param skip_head / skip_tail: 掐掉开头/结尾多少秒（OP/ED、广告）
        :param speaker_filter: LlmSpeakerFilter 实例；传了就对台词做语气打标
        """
        self.video_path = video_path
        self.ocr_region = ocr_region
        # 帧间隔必须 >= 1：0 会让 range() 直接报错。原来静默接受任意值，
        # 跑起来才炸，这里改成显式纠正 + 提示。
        if ocr_interval is None or int(ocr_interval) < 1:
            print(f"警告：ocr_interval={ocr_interval} 非法，已改用 1（逐帧，最慢但最全）")
            ocr_interval = 1
        self.ocr_interval = int(ocr_interval)
        self.whisper_model_name = whisper_model
        self.use_faster_whisper = use_faster_whisper
        # 注意：说话人分离功能暂未实现，该参数仅为保持接口兼容而保留
        self.speaker_diarization = speaker_diarization
        if speaker_diarization:
            print("警告：说话人分离功能暂未实现（可改用 --speaker-filter 做语气打标）")
        self.ocr_method = ocr_method
        self.enable_ocr = enable_ocr
        self.enable_voice = enable_voice

        # —— 采集质量相关 ——
        self.video_id = video_id
        self.initial_prompt = initial_prompt
        self.vad_filter = vad_filter
        self.ocr_skip_static = ocr_skip_static
        self.clean = clean
        self.min_len = int(min_len)
        self.max_len = int(max_len)
        self.require_complete = require_complete
        self.skip_head = max(0.0, float(skip_head or 0.0))
        self.skip_tail = max(0.0, float(skip_tail or 0.0))
        self.speaker_filter = speaker_filter

        # 进度显示状态：tty 下用 \r 原地刷新并节流；重定向到文件时按 10% 分桶打印
        self._last_progress_at = 0.0
        self._last_progress_bucket = -1
        # OCR 调用失败计数。不静默吞掉：失败多了要在结束时汇总报出来
        self._ocr_failures = 0
        self._ocr_frames = 0
        # 跳帧统计（字幕静止时省下的 OCR 次数）
        self._ocr_skipped = 0
        self._clean_report: Dict[str, int] = {}

        # 设备设置
        if device == "auto":
            if TORCH_AVAILABLE and torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
        else:
            self.device = device

        print(f"使用设备：{self.device}")

        # 打开视频获取基本信息
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise IOError(f"无法打开视频文件：{video_path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        # fps 可能为 0 或非法值，回退到默认值，避免除零
        if not self.fps or self.fps <= 0:
            print("警告：无法获取有效帧率，使用默认值 25 FPS")
            self.fps = 25.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.fps
        print(f"视频信息：{self.total_frames} 帧，FPS={self.fps:.2f}, 时长={self.duration:.2f}秒")

        # 掐头去尾窗口的合理性检查：别把整个视频掐没了
        if self.skip_head + self.skip_tail >= self.duration:
            print(f"警告：掐头 {self.skip_head:.0f}s + 去尾 {self.skip_tail:.0f}s "
                  f"已不小于视频时长 {self.duration:.0f}s，这两个窗口已忽略")
            self.skip_head = 0.0
            self.skip_tail = 0.0
        elif self.skip_head or self.skip_tail:
            print(f"掐头去尾生效：前 {self.skip_head:.0f}s 与后 {self.skip_tail:.0f}s "
                  f"内的台词不采用（OP/ED、广告的常见位置）")

        # 大模型 + CPU 是灾难组合，提前讲清楚，别让用户等三小时
        if self.device == "cpu" and str(self.whisper_model_name).startswith(("large", "distil-large")):
            print(f"提示：在 CPU 上跑 {self.whisper_model_name} 会非常慢，"
                  f"建议改用 --whisper_model medium")

        # 初始化 OCR 引擎
        self.paddle_ocr = None
        self.cc_converter = None
        
        # 如果启用交互式 OCR 设置，先进行命令行输入
        if enable_ocr and interactive_ocr:
            self._interactive_ocr_setup()
        
        if enable_ocr and ocr_method == "paddle":
            if not PADDLE_AVAILABLE:
                raise ImportError("paddleocr 未安装，请安装：pip install paddleocr")
            self._init_ocr()

        # 初始化语音识别模型
        self.whisper_model = None
        if enable_voice:
            # 后端选择：显式指定的优先，否则**自动挑一个已安装的**。
            #
            # 为什么需要自动挑：requirements.txt 装的是 faster-whisper，而
            # use_faster_whisper 的默认值是 False —— 于是「照文档装完直接跑」
            # 必然报「openai-whisper 未安装」。让默认行为跟着实际装了什么走，
            # 才能做到开箱可用。
            if use_faster_whisper and FASTER_WHISPER_AVAILABLE:
                self.use_faster_whisper = True
            elif not use_faster_whisper and WHISPER_AVAILABLE:
                self.use_faster_whisper = False
            elif FASTER_WHISPER_AVAILABLE:
                self.use_faster_whisper = True
                print("提示：未安装 openai-whisper，自动改用 faster-whisper")
            elif WHISPER_AVAILABLE:
                self.use_faster_whisper = False
                print("提示：未安装 faster-whisper，自动改用 openai-whisper")
            else:
                raise ImportError(
                    "语音识别后端未安装，二者装其一即可：\n"
                    "  pip install faster-whisper    （推荐：更快更轻，requirements 里装的就是它）\n"
                    "  pip install openai-whisper"
                )

    def close(self):
        """释放视频句柄"""
        cap = getattr(self, 'cap', None)
        if cap is not None:
            try:
                if cap.isOpened():
                    cap.release()
            except Exception:
                pass
            self.cap = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        self.close()

    def _report_progress(self, stage: str, ratio: float, extra: str = "") -> None:
        """打印一行原地刷新的进度。

        长任务（OCR 逐帧、ASR 逐段）全程无反馈时，用户会以为卡死。这里统一处理：
        - 交互终端：用 `\\r` 覆盖同一行，按时间节流（0.15s）避免刷屏
        - 重定向到文件/管道：改成每跨过 10% 打一行，避免日志被百分比刷爆
        """
        ratio = max(0.0, min(1.0, float(ratio)))
        done = ratio >= 1.0
        line = f"  [{stage}] {ratio * 100:5.1f}%"
        if extra:
            line += f"  {extra}"

        if sys.stdout.isatty():
            now = time.time()
            if not done and now - self._last_progress_at < 0.15:
                return
            self._last_progress_at = now
            print(f"\r{line}", end="\n" if done else "", flush=True)
        else:
            bucket = int(ratio * 10)
            if done or bucket > self._last_progress_bucket:
                self._last_progress_bucket = bucket
                print(line, flush=True)

    def _init_ocr(self):
        """初始化 OCR 引擎"""
        # 检查 GPU 是否可用
        use_gpu = False
        try:
            import paddle
            try:
                if paddle.device.cuda.device_count() > 0:
                    use_gpu = True
                    print(f"Paddle GPU 可用，设备：{paddle.device.cuda.get_device_name()}")
            except Exception:
                # 探测 GPU 失败属正常（没装 CUDA 版 paddle），继续用 CPU
                pass
            if not use_gpu:
                print("Paddle GPU 不可用，将使用 CPU")
        except Exception as e:
            print(f"GPU 检测失败：{e}，将使用 CPU")

        # 记录版本：2.x 与 3.x 的返回结构完全不同，排查问题时先看这一行
        try:
            import paddleocr as _paddleocr_mod
            print(f"PaddleOCR 版本：{getattr(_paddleocr_mod, '__version__', '未知')}")
        except Exception:
            pass

        # 使用轻量版 OCR 模型（ch_PP-OCRv4_mobile）
        # 注意：新版 PaddleOCR 通过环境变量控制 GPU
        self.paddle_ocr = PaddleOCR(
            lang="ch"
        )
        print("PaddleOCR 初始化完成（使用轻量版模型）")

        # 初始化简繁体转换器
        if OPENCC_AVAILABLE:
            self.cc_converter = OpenCC('t2s')
            print("简繁体转换器已启用（繁体→简体）")

    def _interactive_ocr_setup(self):
        """交互式设置 OCR 区域（命令行输入模式）"""
        print("\n=== OCR 区域设置 ===")
        print("OCR 区域由四个相对坐标组成：x1, y1, x2, y2")
        print("  x1, y1: 左上角坐标（0-1 之间的小数）")
        print("  x2, y2: 右下角坐标（0-1 之间的小数）")
        print()
        print("示例：字幕通常在视频底部，默认区域为 0,0.7,1,0.95")
        print("  表示：从左到右全宽，从上往下 70% 到 95% 的区域")
        print()

        region = list(self.ocr_region)
        
        while True:
            print("-" * 50)
            print(f"当前 OCR 区域：({region[0]:.2f}, {region[1]:.2f}, {region[2]:.2f}, {region[3]:.2f})")
            print()
            print("请输入新的 OCR 区域（格式：x1,y1,x2,y2），或输入选项：")
            print("  d - 使用默认区域 (0,0.7,1,0.95)")
            print("  r - 重置为当前值")
            print("  q - 确认并继续")
            print()
            
            try:
                user_input = input("请输入：").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消，使用默认区域")
                self.ocr_region = (0, 0.7, 1, 0.95)
                break
            
            if user_input == 'q':
                self.ocr_region = tuple(region)
                print(f"✓ OCR 区域已设置为：{self.ocr_region}")
                break
            elif user_input == 'd':
                region = [0, 0.7, 1, 0.95]
                print("已使用默认区域")
            elif user_input == 'r':
                region = list(self.ocr_region)
                print("已重置为初始值")
            elif ',' in user_input:
                try:
                    values = list(map(float, user_input.split(',')))
                    if len(values) == 4:
                        if all(0 <= v <= 1 for v in values):
                            region = values
                            print(f"已设置 OCR 区域：{region}")
                        else:
                            print("错误：坐标值必须在 0-1 之间")
                    else:
                        print("错误：需要 4 个坐标值（x1,y1,x2,y2）")
                except ValueError:
                    print("错误：请输入有效的数字，用逗号分隔")
            else:
                print("无效输入，请重新输入")
            
            print()
        
        print(f"最终 OCR 区域：{self.ocr_region}")
        print("-" * 50)

    def _filter_text(self, text: str) -> str:
        """过滤无意义的 OCR 识别结果"""
        if not text:
            return ""
        
        # 统计中文字符数量
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text)
        
        # 如果中文字符占比低于 30%，认为是无效识别
        if chinese_chars / total_chars < 0.3 if total_chars > 0 else True:
            return ""
        
        # 过滤掉纯英文/数字的行
        if not re.search(r'[\u4e00-\u9fff]', text):
            return ""
        
        # 过滤掉字符重复度过高的文本（如 "n a o t o e e e e e e e i e"）
        # 计算唯一字符比例
        unique_chars = len(set(text.replace(' ', '')))
        if unique_chars < 3:
            return ""
        
        # 过滤掉英文字母占比过高的文本
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        if english_chars / total_chars > 0.5 if total_chars > 0 else False:
            return ""
        
        return text

    @staticmethod
    def _extract_texts_and_scores_from_ocr_result(result) -> Tuple[List[str], List[Optional[float]]]:
        """把不同版本 PaddleOCR 的返回结果统一成「文本列表 + 置信度列表」。

        这是必要的兼容层 —— 两个大版本的返回结构**完全不同**：

        - **2.x**：``[[ [box, (text, score)], ... ]]``
          外层是「每张图一个列表」，内层是「每行一个 [框, (文本, 置信度)]」
        - **3.x**：``[OCRResult, ...]``
          OCRResult 是 dict 风格的（PaddleX 定义），文本在 ``["rec_texts"]`` 里，
          置信度在 ``["rec_scores"]`` 里；且 ``ocr()`` 已被标记 deprecated，
          推荐 ``predict()``

        没有这层兼容，在 3.x 环境里按 2.x 解析会抛异常 → 被上层吞掉 →
        **OCR 静默返回空**（表现为「跑完了但一句台词都没有」）。

        置信度是 jsonl 溯源字段之一，拿不到就写 ``None``，不要编一个数字出来。
        """
        texts: List[str] = []
        scores: List[Optional[float]] = []
        if not result:
            return texts, scores

        # ---- 3.x：dict 风格，取 rec_texts / rec_scores ----
        for item in result:
            if isinstance(item, (list, tuple)):
                continue          # 这是 2.x 的结构，交给下面处理
            try:
                rec_texts = item["rec_texts"]
            except Exception:
                continue
            rec_scores = None
            try:
                rec_scores = item["rec_scores"]
            except Exception:
                pass

            if isinstance(rec_texts, (list, tuple)):
                for index, text in enumerate(rec_texts):
                    if not text:
                        continue
                    texts.append(str(text))
                    score = None
                    if isinstance(rec_scores, (list, tuple)) and index < len(rec_scores):
                        try:
                            score = float(rec_scores[index])
                        except (TypeError, ValueError):
                            score = None
                    scores.append(score)
        if texts:
            return texts, scores

        # ---- 2.x：[[ [box, (text, score)], ... ]] ----
        first = result[0]
        if isinstance(first, (list, tuple)):
            for line in first:
                if not isinstance(line, (list, tuple)) or len(line) < 2:
                    continue
                info = line[1]
                if isinstance(info, (list, tuple)) and info:
                    texts.append(str(info[0]))
                    score = None
                    if len(info) > 1:
                        try:
                            score = float(info[1])
                        except (TypeError, ValueError):
                            score = None
                    scores.append(score)

        return texts, scores

    @staticmethod
    def _extract_texts_from_ocr_result(result) -> List[str]:
        """（兼容保留）只要文本，不要置信度。"""
        texts, _ = RoleLineExtractor._extract_texts_and_scores_from_ocr_result(result)
        return texts

    def _roi_hash(self, frame: "np.ndarray") -> bytes:
        """OCR 区域的灰度缩略图指纹。

        字幕静止时，这块像素是**逐帧不变**的（视频里这种情况占大头），
        指纹一样就没必要再调一次 OCR。用缩略图而不是原图，是为了让压缩噪点、
        轻微抖动不影响判断 —— 我们要判断的是「这一屏和上一屏是不是同一行字」。
        """
        h, w = frame.shape[:2]
        x1 = max(0, int(self.ocr_region[0] * w))
        y1 = max(0, int(self.ocr_region[1] * h))
        x2 = min(w, int(self.ocr_region[2] * w))
        y2 = min(h, int(self.ocr_region[3] * h))

        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return b""
        try:
            small = cv2.resize(roi, (32, 8), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            return gray.tobytes()
        except Exception:
            return b""

    def _ocr_from_frame(self, frame: "np.ndarray") -> Tuple[str, Optional[float]]:
        """从单帧提取 OCR 文字。

        :return: ``(文本, 置信度)`` —— 没识别到就是 ``("", None)``。
                 置信度取该帧所有文本行的平均分，取不到写 None。
        """
        h, w = frame.shape[:2]
        x1 = int(self.ocr_region[0] * w)
        y1 = int(self.ocr_region[1] * h)
        x2 = int(self.ocr_region[2] * w)
        y2 = int(self.ocr_region[3] * h)
        
        # 确保坐标有效
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return "", None

        self._ocr_frames += 1
        try:
            # 2.x 只有 ocr()；3.x 推荐 predict()（ocr() 仍可用但已 deprecated）
            run = getattr(self.paddle_ocr, "predict", None) or self.paddle_ocr.ocr
            result = run(roi)
        except Exception as e:
            self._ocr_failures += 1
            # 只前 3 次逐条打印，避免上千帧把日志刷爆；结束时统一汇总
            if self._ocr_failures <= 3:
                print(f"OCR 调用失败（第 {self._ocr_failures} 次）：{type(e).__name__}: {e}")
            elif self._ocr_failures == 4:
                print("  （后续同类失败不再逐条打印，结束时汇总）")
            return "", None

        texts, scores = self._extract_texts_and_scores_from_ocr_result(result)
        if not texts:
            return "", None

        combined_text = " ".join(texts).strip()
        # 转换为简体字
        if self.cc_converter:
            combined_text = self.cc_converter.convert(combined_text)
        # 过滤无意义文本
        filtered = self._filter_text(combined_text)
        if not filtered:
            return "", None

        valid_scores = [s for s in scores if isinstance(s, (int, float))]
        confidence = (sum(valid_scores) / len(valid_scores)) if valid_scores else None
        return filtered, confidence

    def extract_ocr_lines(self) -> List[Dict[str, Any]]:
        """从视频画面提取 OCR 台词。

        与旧实现有两处实质不同：

        1. **跳帧**：OCR 区域的缩略图指纹没变时，直接沿用上一帧的文字，跳过 OCR 调用。
           视频里字幕静止的时间占大头，这一条能省掉相当一部分 GPU 时间。
        2. **相同文本才合并**：旧实现把 0.5s 内的相邻条目一律串起来，会把两条不同台词
           黏成一条连行（历史上出现过三个片段被并成一条）。现在只延长「同一条字幕」的
           持续时间，不同文本各自成条。
        """
        if not self.enable_ocr or not self.paddle_ocr:
            return []

        print("开始画面 OCR 台词识别..."
              + ("（跳帧已开：字幕区没变化就不调 OCR）" if self.ocr_skip_static else ""))
        cap = cv2.VideoCapture(self.video_path)
        fps = self.fps
        total_frames = self.total_frames

        entries: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        last_hash: Optional[bytes] = None
        last_text = ""
        last_confidence: Optional[float] = None

        frame_indices = range(0, total_frames, self.ocr_interval)
        total_steps = max(1, len(frame_indices))

        for step, frame_idx in enumerate(frame_indices):
            extra = f"已识别 {len(entries)} 条"
            if self._ocr_skipped:
                extra += f"，跳帧 {self._ocr_skipped}"
            self._report_progress("OCR", (step + 1) / total_steps, extra)

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            current_time = frame_idx / fps
            roi_hash = self._roi_hash(frame) if self.ocr_skip_static else None

            if self.ocr_skip_static and roi_hash is not None and roi_hash == last_hash:
                # 这一屏和上一屏是同一行字：不调 OCR，直接沿用
                self._ocr_skipped += 1
                text, confidence = last_text, last_confidence
            else:
                text, confidence = self._ocr_from_frame(frame)
                last_hash = roi_hash
                last_text, last_confidence = text, confidence

            if text:
                if current is not None and current['text'] == text:
                    current['end'] = current_time          # 同一条字幕还在 → 延长
                else:
                    if current is not None:
                        entries.append(current)
                    current = {
                        'text': text,
                        'start': current_time,
                        'end': current_time,
                        'source': 'ocr',
                        'channel': CHANNEL_OCR,
                        'confidence': confidence,
                    }
            elif current is not None:
                entries.append(current)                    # 字幕消失 → 收尾
                current = None

        if current is not None:
            entries.append(current)

        # 保证 video 句柄一定被释放：后面不再需要逐帧读了
        cap.release()

        self._report_progress("OCR", 1.0, f"已识别 {len(entries)} 条")
        print(f"OCR 识别完成，得到 {len(entries)} 条原始台词")

        if self.ocr_skip_static and self._ocr_skipped:
            print(f"跳帧生效：{self._ocr_skipped}/{total_steps} 帧"
                  f"（{self._ocr_skipped / total_steps * 100:.0f}%）"
                  f"因字幕未变化而跳过 OCR 调用")

        # 不静默失败：把调用失败汇总出来。全部失败通常意味着
        # PaddleOCR 版本与解析方式不匹配 —— 历史上真出过，而且完全无声。
        if self._ocr_failures:
            print(f"⚠️ OCR 调用失败 {self._ocr_failures}/{self._ocr_frames} 帧 —— "
                  f"若全部失败，多半是 PaddleOCR 版本不兼容（见 README「已知限制」）")
            if self._ocr_failures >= self._ocr_frames and not entries:
                print("   提示：一行台词都没识别到。先看上面的失败原因，"
                      "不要误以为视频里没有字幕。")

        # 相同文本才合并：跨间隔重新出现的同一条字幕（例如被中间一次误识别切断）
        merged: List[Dict[str, Any]] = []
        for entry in entries:
            if (merged and merged[-1]['text'] == entry['text']
                    and entry['start'] - merged[-1]['end'] <= 0.5):
                merged[-1]['end'] = max(merged[-1]['end'], entry['end'])
            else:
                merged.append(entry)
        if len(merged) != len(entries):
            print(f"相邻重复字幕合并：{len(entries)} -> {len(merged)} 条")

        return merged

    def extract_audio_transcription(self) -> List[Dict[str, Any]]:
        """从音频提取语音识别台词"""
        if not self.enable_voice:
            return []

        print("正在提取音频并进行语音识别...")
        # 使用唯一临时文件，避免并发运行互相覆盖，也避免异常时残留
        fd, audio_path = tempfile.mkstemp(suffix=".wav", prefix="chibtaici_")
        os.close(fd)
        
        try:
            try:
                video_clip = VideoFileClip(self.video_path)
                try:
                    video_clip.audio.write_audiofile(audio_path, logger=None)
                finally:
                    video_clip.close()
            except Exception as e:
                print(f"音频提取失败：{e}")
                return []

            # 加载语音识别模型
            if self.use_faster_whisper:
                print(f"加载 faster-whisper 模型：{self.whisper_model_name} on {self.device}")
                if self.vad_filter:
                    print("  已开启 VAD 切句（silero，抑制静音段幻觉）")
                if self.initial_prompt:
                    print(f"  已注入提示词：{self.initial_prompt}")
                try:
                    model = WhisperModel(
                        self.whisper_model_name,
                        device=self.device,
                        compute_type="float16" if self.device == "cuda" else "float32"
                    )

                    kwargs = {
                        "language": "zh",
                        "beam_size": 5,
                        "word_timestamps": False,
                    }
                    if self.initial_prompt:
                        kwargs["initial_prompt"] = self.initial_prompt
                    if self.vad_filter:
                        kwargs["vad_filter"] = True
                        kwargs["vad_parameters"] = {"min_silence_duration_ms": 500}

                    try:
                        segments, info = model.transcribe(audio_path, **kwargs)
                    except TypeError as e:
                        # 老版本 faster-whisper 没有这些参数，退回基础用法而不是整条失败
                        print(f"  ⚠️ 当前 faster-whisper 版本不支持上述参数（{e}），退回基础用法")
                        segments, info = model.transcribe(audio_path, language="zh", beam_size=5)

                    # faster-whisper 的 segments 是生成器，可以边出边报进度。
                    # info.duration 是音频总时长，拿它当分母。
                    total_duration = float(getattr(info, "duration", 0) or 0)

                    segments_list = []
                    for seg in segments:
                        segments_list.append({
                            "text": seg.text.strip(),
                            "start": seg.start,
                            "end": seg.end,
                            "source": "voice",
                            "channel": CHANNEL_VOICE,
                            "confidence": self._logprob_to_confidence(
                                getattr(seg, "avg_logprob", None)
                            ),
                        })
                        if total_duration > 0:
                            self._report_progress(
                                "语音识别",
                                min(seg.end, total_duration) / total_duration,
                                f"已转 {len(segments_list)} 段"
                            )
                    if total_duration > 0:
                        self._report_progress("语音识别", 1.0, f"已转 {len(segments_list)} 段")
                except Exception as e:
                    print(f"faster-whisper 识别失败：{e}，尝试使用 openai-whisper")
                    segments_list = self._transcribe_with_openai_whisper(audio_path)
            else:
                segments_list = self._transcribe_with_openai_whisper(audio_path)

            print(f"语音识别完成，共得到 {len(segments_list)} 个句子片段")
            return segments_list
        finally:
            # 无论成功失败都清理临时音频
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError as e:
                    print(f"临时音频清理失败：{e}")

    @staticmethod
    def _logprob_to_confidence(avg_logprob: Any) -> Optional[float]:
        """把 whisper 的 avg_logprob（对数概率）换算成 0~1 的置信度。

        换算方式是 ``exp(avg_logprob)`` —— 也就是该段所有 token 概率的几何平均。
        拿不到就返回 None：jsonl 里宁可写 null，也不要编一个看起来很像的数字。
        """
        if not isinstance(avg_logprob, (int, float)):
            return None
        try:
            return max(0.0, min(1.0, math.exp(float(avg_logprob))))
        except (OverflowError, ValueError):
            return None

    def _transcribe_with_openai_whisper(self, audio_path: str) -> List[Dict[str, Any]]:
        """使用 openai-whisper 进行语音识别"""
        import whisper
        
        print(f"加载 openai-whisper 模型：{self.whisper_model_name} on {self.device}")
        model = whisper.load_model(self.whisper_model_name, device=self.device)

        # openai-whisper 的 transcribe() 是一次性返回的，拿不到逐段进度。
        # 至少把音频时长说清楚，让用户知道要等多久（而不是以为卡死）。
        duration = 0.0
        try:
            import wave
            with wave.open(audio_path, "rb") as wf:
                duration = wf.getnframes() / float(wf.getframerate() or 1)
        except Exception:
            pass
        if duration:
            print(f"正在识别（音频时长约 {duration:.0f} 秒）。"
                  f"openai-whisper 不支持实时进度，请耐心等待...")
        else:
            print("正在识别。openai-whisper 不支持实时进度，请耐心等待...")

        # initial_prompt 放专有名词（人名、术语）能明显改善识别准确率
        prompt = self.initial_prompt or "以下是普通话的简体中文字幕。"
        print(f"  提示词：{prompt}")
        if self.vad_filter:
            print("  提示：openai-whisper 不支持 VAD 参数，该选项只在 faster-whisper 上生效")

        result = model.transcribe(
            audio_path,
            language="zh",
            initial_prompt=prompt,
            temperature=0.2,
            best_of=5,
            beam_size=10,
            patience=2
        )
        
        segments = []
        for seg in result["segments"]:
            segments.append({
                "text": seg["text"].strip(),
                "start": seg["start"],
                "end": seg["end"],
                "source": "voice",
                "channel": CHANNEL_VOICE,
                "confidence": self._logprob_to_confidence(seg.get("avg_logprob")),
            })
        return segments

    # OCR 与 ASR 识别同一句话时，结果往往只差几个字（OCR 丢标点、ASR 丢字）。
    # 相似度高于这个值就认为是同一条台词，取更长的那份而不是拼接。
    SIMILARITY_THRESHOLD = 0.85

    @staticmethod
    def _is_same_line(a: str, b: str) -> bool:
        """判断两段文本是不是「同一句台词」（而不是相邻的两句）。"""
        if not a or not b:
            return False
        if a == b:
            return True
        # 互为包含（一方多识别/少识别了几个字）
        if min(len(a), len(b)) >= 3 and (a in b or b in a):
            return True
        try:
            return difflib.SequenceMatcher(None, a, b).ratio() >= RoleLineExtractor.SIMILARITY_THRESHOLD
        except Exception:
            return False

    @staticmethod
    def _best_confidence(a, b):
        """两个置信度里取更可信的那个（都不可用则 None）。"""
        values = [v for v in (a, b) if isinstance(v, (int, float))]
        return max(values) if values else None

    def merge_timeline(self, ocr_lines: List[Dict[str, Any]], 
                       voice_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并 OCR 与语音识别结果，按时间排序，并把**同一条台词**的两路结果并起来。

        规则（相对旧实现是实质变化）：

        - 文本**相同 / 高度相似（≥0.85）/ 互为包含** → 判为同一条，合并时间区间，
          文本取更长的那份（信息量更足），通道记为 ``voice+ocr``
        - 文本**不同** → 各自成条，**不再拼接**

        旧实现把 1.0s 内的相邻条目一律拼接，会产生两类脏数据：
        「两条不同台词黏成一条连行」，以及 OCR/ASR 各识别一遍同一句时拼成
        「同一句话 同一句话」。连行会教模型「一句话可以说很长」，
        属于采集质量红线里明确要避免的。

        :return: 带时间轴的条目（text/start/end/channel/confidence/source）
        """
        all_lines = sorted(ocr_lines + voice_lines, key=lambda x: x['start'])

        merged: List[Dict[str, Any]] = []
        for line in all_lines:
            if not merged:
                merged.append(dict(line))
                continue

            last = merged[-1]
            if self._is_same_line(last['text'], line['text']) and line['start'] - last['end'] <= 1.0:
                if len(line['text']) > len(last['text']):
                    last['text'] = line['text']
                last['end'] = max(last['end'], line['end'])
                last['confidence'] = self._best_confidence(
                    last.get('confidence'), line.get('confidence')
                )
                # 两侧都识别到了才标 merged；同一通道内部合并保持原通道
                if last.get('channel') != line.get('channel'):
                    last['channel'] = CHANNEL_MERGED
            else:
                merged.append(dict(line))

        return merged

    def _dedup_texts(self, entries: List[Dict[str, Any]]) -> List[str]:
        """从带时间轴的条目里提取去重后的纯文本（供喂给微调流水线）。"""
        texts = [e['text'] for e in entries if e.get('text')]

        # 智能去重（保留顺序，合并相似文本）
        seen = set()
        unique_texts = []
        for t in texts:
            # 清理文本（去除多余空格和标点）
            cleaned = re.sub(r'\s+', ' ', t).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                unique_texts.append(cleaned)

        return unique_texts

    def merge_and_deduplicate(self, ocr_lines: List[Dict[str, Any]], 
                              voice_lines: List[Dict[str, Any]]) -> List[str]:
        """（兼容保留）合并后返回去重纯文本。"""
        return self._dedup_texts(self.merge_timeline(ocr_lines, voice_lines))

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """把秒数格式化成 SRT 的 HH:MM:SS,mmm"""
        ms_total = int(round(max(0.0, float(seconds)) * 1000))
        hours, rem = divmod(ms_total, 3600 * 1000)
        minutes, rem = divmod(rem, 60 * 1000)
        secs, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def to_srt(self, entries: List[Dict[str, Any]],
               min_duration: float = 1.2) -> str:
        """把带时间轴的条目转成 SRT 字幕文本。

        OCR 条目的时间戳是"命中某一帧"，start == end。这里补一个最小持续时间，
        并把结束时间夹在"下一条的开始"之前，避免字幕互相重叠。

        两条字幕**开始时间相同**时（OCR 与 ASR 各给了一版，或同一帧命中两次），
        后一条会被顺延到前一条结束之后 —— 字幕必须严格递增，否则播放器会闪烁。
        """
        cleaned = []
        for e in entries:
            text = re.sub(r'\s+', ' ', str(e.get('text', ''))).strip()
            if not text:
                continue
            start = max(0.0, float(e.get('start', 0.0) or 0.0))
            end = float(e.get('end', start) or start)
            cleaned.append({'text': text, 'start': start, 'end': end})

        blocks = []
        last_end = 0.0
        for i, e in enumerate(cleaned):
            start = max(e['start'], last_end) if i else e['start']
            end = e['end']
            if end <= start:
                end = start + min_duration
            if i + 1 < len(cleaned):
                nxt = cleaned[i + 1]['start']
                if nxt > start:
                    end = min(end, nxt)
            if end - start < 0.2:
                end = start + 0.2
            last_end = end
            blocks.append(
                f"{i + 1}\n"
                f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}\n"
                f"{e['text']}\n"
            )
        return "\n".join(blocks)

    @staticmethod
    def _srt_path(output_path: str = None) -> str:
        """由 txt 输出路径推导 srt 路径（同目录同名，只换扩展名）。"""
        if not output_path:
            return "role_lines.srt"
        base, _ = os.path.splitext(output_path)
        return base + ".srt"

    @staticmethod
    def _jsonl_path(output_path: str = None) -> str:
        """由 txt 输出路径推导 jsonl 路径（同目录同名，只换扩展名）。"""
        if not output_path:
            return "role_lines.jsonl"
        base, _ = os.path.splitext(output_path)
        return base + ".jsonl"

    def _in_time_window(self, entry: Dict[str, Any]) -> bool:
        """是否落在「掐头去尾」保留下来的时间窗内。"""
        start = float(entry.get('start') or 0.0)
        end = float(entry.get('end') or start)
        if self.skip_head and start < self.skip_head:
            return False
        if self.skip_tail and end > (self.duration - self.skip_tail):
            return False
        return True

    def clean_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """规范化 + 质量过滤（长度红线、长句切分、可选严格完整句、掐头去尾）。

        过滤只发生在**输出前**：OCR / ASR 两条路的原始结果不受影响。
        丢弃统计会记进 ``self._clean_report``，由调用方打印成体检报告 ——
        「丢了多少、为什么丢」必须让人看得见。
        """
        if not self.clean:
            return [dict(e) for e in entries]

        report: Dict[str, int] = {}
        self._clean_report = report

        def bump(reason: str) -> None:
            report[reason] = report.get(reason, 0) + 1

        kept: List[Dict[str, Any]] = []
        for entry in entries:
            text = normalize_text(entry.get('text', ''), cc_converter=self.cc_converter)
            if not text:
                bump("empty")
                continue
            if not self._in_time_window(entry):
                bump("out_of_window")
                continue

            for piece in split_long_text(text, limit=self.max_len):
                reason = check_line(piece, min_len=self.min_len, max_len=self.max_len,
                                    require_complete=self.require_complete)
                if reason:
                    bump(reason)
                    continue
                record = dict(entry)
                record['text'] = piece
                kept.append(record)
                bump("kept")

        return kept

    def to_jsonl_records(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把条目转成 jsonl 记录（句级溯源：BV 号 + 时间点 + 通道 + 置信度）。"""
        records = []
        for entry in entries:
            records.append(build_record(
                text=entry.get('text', ''),
                video=self.video_id,
                start=entry.get('start', 0.0),
                end=entry.get('end', 0.0),
                channel=entry.get('channel') or entry.get('source') or CHANNEL_OCR,
                confidence=entry.get('confidence'),
                source=entry.get('source'),
                speaker_score=entry.get('speaker_score'),
            ))
        return records

    def _report_repeats(self, texts: List[str], top: int = 5) -> None:
        """打印重复台词 Top N。

        重复不是噪声 —— 高频台词往往是角色的口头禅，对训练是加权信号，
        所以这里只**报告**，不删除（去重后的 txt 依然保留一份）。
        """
        repeated = [(t, c) for t, c in dedup_with_counts(texts) if c > 1]
        if not repeated:
            return
        repeated.sort(key=lambda item: item[1], reverse=True)
        print(f"重复台词 {len(repeated)} 条（保留频次信息，未删除）：")
        for text, count in repeated[:top]:
            shown = text if len(text) <= 24 else text[:24] + "…"
            print(f"  ×{count}  {shown}")

    def run(self, output_path: str = None, fmt: str = "txt") -> List[Dict[str, Any]]:
        """执行台词提取。

        :param output_path: 输出路径（txt 用；srt / jsonl 由它推导同名文件）
        :param fmt: "txt" / "srt" / "jsonl" / "both"(txt+srt) / "all"(三者都要)
        :return: 带时间轴的条目列表（text/start/end/channel/confidence）
        """
        ocr_lines = []
        voice_lines = []

        try:
            # OCR 识别
            if self.enable_ocr:
                ocr_lines = self.extract_ocr_lines()

            # 语音识别
            if self.enable_voice:
                voice_lines = self.extract_audio_transcription()

            # 合并（只并「同一条台词」，不同文本不拼接）
            entries = self.merge_timeline(ocr_lines, voice_lines)
            raw_count = len(entries)

            # 规范化 + 质量过滤
            if self.clean:
                entries = self.clean_entries(entries)
                print(format_quality_report(self._clean_report))
                if raw_count != len(entries):
                    print(f"（合并后 {raw_count} 条 → 清理后保留 {len(entries)} 条）")

            # 说话人过滤（轻量语气打标）
            if self.speaker_filter is not None:
                if entries:
                    entries, _ = self.speaker_filter.filter_entries(entries)
                else:
                    print("没有可打标的台词，跳过语气打标")

            # 纯文本输出：去重后的台词，用于喂给微调流水线
            if fmt in ("txt", "both", "all"):
                texts = [e['text'] for e in entries if e.get('text')]
                unique_texts = [text for text, _ in dedup_with_counts(texts)]
                self._report_repeats(texts)
                if output_path:
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write("\n".join(unique_texts))
                    print(f"台词结果已保存到：{output_path}（{len(unique_texts)} 句）")
                else:
                    for text in unique_texts:
                        print(text)

            # SRT 输出：保留时间轴的字幕
            if fmt in ("srt", "both", "all"):
                srt_path = self._srt_path(output_path)
                with open(srt_path, 'w', encoding='utf-8') as f:
                    f.write(self.to_srt(entries))
                print(f"SRT 字幕已保存到：{srt_path}")

            # jsonl 输出：句级溯源，下游做过滤/加权/回溯的入口
            if fmt in ("jsonl", "all"):
                records = self.to_jsonl_records(entries)
                jsonl_path = self._jsonl_path(output_path)
                written = write_jsonl(jsonl_path, records)
                print(f"jsonl 已保存到：{jsonl_path}（{written} 条，含 BV/时间/通道/置信度）")
                channels: Dict[str, int] = {}
                for record in records:
                    channels[record['channel']] = channels.get(record['channel'], 0) + 1
                if channels:
                    detail = "、".join(f"{k} {v}" for k, v in sorted(channels.items()))
                    print(f"  通道分布：{detail}")
                freq = text_frequencies(records)
                if freq:
                    print(f"  去重后不同台词：{len(freq)} 句")

            return entries
        finally:
            # 无论成功失败都释放视频句柄
            self.close()


def guess_video_id(video_path: str) -> str:
    """从文件名里推测视频 ID（BV 号），用于 jsonl 溯源；猜不到就用文件名主干。"""
    base = os.path.basename(video_path or "")
    match = re.search(r'BV[0-9A-Za-z]{10}', base)
    if match:
        return match.group(0)
    stem = os.path.splitext(base)[0]
    return stem or None


def main():
    parser = argparse.ArgumentParser(description="视频台词识别工具（增强版，支持 OCR+ 语音识别）")
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--output", default="role_lines.txt", help="输出台词文件路径")
    parser.add_argument("--format", default="txt",
                        choices=["txt", "srt", "jsonl", "both", "all"],
                        help="输出格式：txt=去重纯文本（默认，喂给微调流水线）、"
                             "srt=带时间轴字幕、jsonl=句级结构化（BV+时间+通道+置信度）、"
                             "both=txt+srt、all=三者都要（.srt/.jsonl 由 --output 推导）")
    parser.add_argument("--whisper_model", default="large-v3",
                        choices=["tiny", "base", "small", "medium",
                                 "large", "large-v2", "large-v3", "distil-large-v3"],
                        help="Whisper 模型（默认 large-v3：base 的中文错误率偏高，"
                             "人名尤其容易错；CPU 上建议退回 medium）")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], 
                        help="计算设备")
    parser.add_argument("--ocr_region", type=str, default="0,0.7,1,0.95", 
                        help="OCR 区域相对坐标 x1,y1,x2,y2")
    parser.add_argument("--ocr_interval", type=int, default=5, 
                        help="OCR 帧间隔（越大越快，但可能漏识别）")
    parser.add_argument("--no-ocr-skip", action="store_true",
                        help="关闭 OCR 跳帧（默认开启：字幕区没变化就跳过 OCR 调用）")
    parser.add_argument("--use_faster_whisper", action="store_true",
                        help="强制使用 faster-whisper（默认会自动挑已安装的那个后端）")
    parser.add_argument("--no-vad", action="store_true",
                        help="关闭 VAD 切句（默认开启，仅 faster-whisper 支持）")
    parser.add_argument("--hotwords", default="爱莉希雅、芽衣、朔愿、律者、往世乐土",
                        help="专有名词提示词，喂给 whisper 的 initial_prompt，"
                             "明显提升人名准确率。传空字符串可关闭")
    parser.add_argument("--disable_ocr", action="store_true",
                        help="禁用 OCR 识别（仅使用语音识别）")
    parser.add_argument("--disable_voice", action="store_true",
                        help="禁用语音识别（仅使用 OCR 识别）")
    parser.add_argument("--interactive_ocr", action="store_true",
                        help="启用交互式 OCR 区域设置（命令行输入模式）")

    # —— 采集质量 ——
    quality = parser.add_argument_group("采集质量（见 README「采集质量红线」）")
    quality.add_argument("--video-id", default=None,
                         help="溯源用视频 ID（默认从文件名里的 BV 号推断）")
    quality.add_argument("--no-clean", action="store_true",
                         help="关闭文本规范化与质量过滤（保留原始的合并结果）")
    quality.add_argument("--min-len", type=int, default=4, help="最短台词字数（默认 4）")
    quality.add_argument("--max-len", type=int, default=100,
                         help="最长台词字数，超出按标点切开（默认 100）")
    quality.add_argument("--require-complete", action="store_true",
                         help="严格要求标点收尾，丢弃截断句（默认关：ASR 输出常缺标点，"
                              "开了会一次性损失不少语料）")
    quality.add_argument("--skip-head", type=float, default=0.0,
                         help="掐掉开头多少秒（OP/ED 常在这里，如 90）")
    quality.add_argument("--skip-tail", type=float, default=0.0,
                         help="掐掉结尾多少秒（ED/广告常在这里，如 90）")

    # —— 说话人（轻量语气打标）——
    speaker = parser.add_argument_group("说话人过滤（本地 LLM 语气打标）")
    speaker.add_argument("--speaker-filter", action="store_true",
                         help="启用语气打标：让本地 LLM 判断每句像不像角色本人，低分剔除")
    speaker.add_argument("--speaker-role", default="爱莉希雅", help="角色名（默认 爱莉希雅）")
    speaker.add_argument("--speaker-model", default="qwen3:8b",
                         help="Ollama 模型名（默认 qwen3:8b）")
    speaker.add_argument("--speaker-threshold", type=int, default=3,
                         help="低于这个分剔除（1-5，默认 3）")
    speaker.add_argument("--speaker-batch", type=int, default=20,
                         help="一次请求打标多少句（默认 20）")
    speaker.add_argument("--ollama-url", default="http://127.0.0.1:11434",
                         help="本地 Ollama 地址")

    args = parser.parse_args()

    # 依赖预检：缺什么直接说清楚怎么装，而不是让用户撞上一串 ModuleNotFoundError 堆栈。
    # （`--help` 在上面就已经返回了，不受这里影响。）
    _missing = []
    if not CV2_AVAILABLE:
        _missing.append("opencv-python（视频帧读取）")
    if not MOVIEPY_AVAILABLE:
        _missing.append("moviepy（音轨提取）")
    if _missing:
        print("缺少核心依赖，无法继续：")
        for _m in _missing:
            print(f"  - {_m}")
        print()
        print("请先安装依赖：")
        print("  pip install -r requirements.txt")
        print()
        print("注意 requirements.txt 里有几个版本是刻意钉住的（见文件内注释），")
        print("不要单独升级其中一个，否则容易出现 PaddleOCR 兼容性问题。")
        return 1

    # 解析 OCR 区域坐标（去除空格）
    try:
        coords = [float(x.strip()) for x in args.ocr_region.split(',')]
        if len(coords) != 4:
            print("OCR 区域格式错误，使用默认值")
            ocr_region = (0, 0.7, 1, 0.95)
        else:
            # 自动检测坐标格式：如果有任何值大于 1，则认为是绝对坐标，需要转换
            if any(c > 1 for c in coords):
                # 需要先获取视频分辨率来转换绝对坐标为相对坐标
                print("检测到绝对坐标格式，正在转换为相对坐标...")
                temp_cap = cv2.VideoCapture(args.video)
                if temp_cap.isOpened():
                    width = int(temp_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(temp_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    temp_cap.release()
                    ocr_region = (
                        coords[0] / width,
                        coords[1] / height,
                        coords[2] / width,
                        coords[3] / height
                    )
                    print(f"视频分辨率：{width}x{height}")
                    print(f"绝对坐标：({coords[0]}, {coords[1]}, {coords[2]}, {coords[3]})")
                    print(f"相对坐标：{ocr_region}")
                else:
                    print("无法获取视频分辨率，使用绝对坐标作为相对坐标")
                    ocr_region = tuple(coords)
            else:
                ocr_region = tuple(coords)
    except ValueError:
        print("OCR 区域格式错误，使用默认值")
        ocr_region = (0, 0.7, 1, 0.95)
    
    print(f"最终 OCR 区域设置：{ocr_region}")

    speaker_filter = None
    if args.speaker_filter:
        speaker_filter = LlmSpeakerFilter(
            role=args.speaker_role,
            model=args.speaker_model,
            base_url=args.ollama_url,
            threshold=args.speaker_threshold,
            batch_size=args.speaker_batch,
        )
        if args.speaker_threshold < 1 or args.speaker_threshold > 5:
            print(f"警告：--speaker-threshold={args.speaker_threshold} 超出 1-5，已按 3 处理")
            speaker_filter.threshold = 3

    print("\n采集设置：")
    print(f"  输出格式   : {args.format}")
    print(f"  语音模型   : {args.whisper_model}"
          f"（{args.device}，VAD {'关' if args.no_vad else '开'}）")
    if args.hotwords:
        print(f"  专有名词   : {args.hotwords}")
    print(f"  OCR 跳帧   : {'关' if args.no_ocr_skip else '开'}")
    print(f"  文本清洗   : {'关' if args.no_clean else '开'}"
          f"（{args.min_len}~{args.max_len} 字"
          f"{'，要求标点收尾' if args.require_complete else ''}）")
    if args.skip_head or args.skip_tail:
        print(f"  掐头去尾   : 前 {args.skip_head:.0f}s / 后 {args.skip_tail:.0f}s")
    if speaker_filter:
        print(f"  语气打标   : {args.speaker_role} @ {args.speaker_model}"
              f"（低于 {speaker_filter.threshold} 分剔除）")

    extractor = RoleLineExtractor(
        video_path=args.video,
        whisper_model=args.whisper_model,
        device=args.device,
        ocr_region=ocr_region,
        ocr_interval=args.ocr_interval,
        use_faster_whisper=args.use_faster_whisper,
        enable_ocr=not args.disable_ocr,
        enable_voice=not args.disable_voice,
        interactive_ocr=args.interactive_ocr,
        video_id=args.video_id or guess_video_id(args.video),
        initial_prompt=args.hotwords or None,
        vad_filter=not args.no_vad,
        ocr_skip_static=not args.no_ocr_skip,
        clean=not args.no_clean,
        min_len=args.min_len,
        max_len=args.max_len,
        require_complete=args.require_complete,
        skip_head=args.skip_head,
        skip_tail=args.skip_tail,
        speaker_filter=speaker_filter,
    )
    extractor.run(output_path=args.output, fmt=args.format)


if __name__ == "__main__":
    main()
