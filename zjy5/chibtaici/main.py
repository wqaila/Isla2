#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
视频台词识别工具（增强版）
支持语音识别（openai-whisper/faster-whisper）、OCR（PaddleOCR）
输出结果：纯文本台词（无时间戳）

注意：**说话人分离尚未实现**。构造参数里的 speaker_diarization 只为保持
接口兼容而保留，传 True 只会打印一条警告，不会生效。
（原先的 docstring 写着"支持说话人分离、多人声识别"，与实际不符，已更正。）
"""

import os

# 禁用 PaddlePaddle PIR 模式以解决兼容性问题
os.environ['FLAGS_use_pir_mode'] = 'false'
os.environ['FLAGS_enable_pir_api'] = '0'
os.environ['FLAGS_cinn_new_group_scheduler'] = '0'
os.environ['FLAGS_enable_filelock'] = '0'

import argparse
import tempfile
import cv2
import numpy as np
from typing import List, Tuple, Dict, Any
import re

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

from moviepy.editor import VideoFileClip


class RoleLineExtractor:
    def __init__(self, video_path: str,
                 whisper_model: str = "base",
                 device: str = "auto",
                 ocr_region: Tuple[float, float, float, float] = (0, 0.7, 1, 0.95),
                 ocr_interval: int = 5,
                 use_faster_whisper: bool = False,
                 speaker_diarization: bool = False,
                 ocr_method: str = "paddle",
                 enable_ocr: bool = True,
                 enable_voice: bool = True,
                 interactive_ocr: bool = False):
        """
        :param video_path: 视频文件路径
        :param whisper_model: Whisper 模型大小（tiny/base/small/medium/large）
        :param device: 设备 "cpu"/"cuda"/"auto"
        :param ocr_region: OCR 区域相对坐标 (x1, y1, x2, y2)
        :param ocr_interval: OCR 帧间隔（减少 OCR 次数提升速度）
        :param use_faster_whisper: 是否使用 faster-whisper（更快更轻量）。
               传 True 表示**强制**用它；传 False（默认）则自动挑一个已安装的后端
               （优先尊重默认的 openai-whisper，它没装就用 faster-whisper）
        :param speaker_diarization: 是否启用说话人分离
        :param ocr_method: OCR 方法 "paddle"
        :param enable_ocr: 是否启用 OCR 识别
        :param enable_voice: 是否启用语音识别
        :param interactive_ocr: 是否启用交互式 OCR 区域设置（命令行输入）
        """
        self.video_path = video_path
        self.ocr_region = ocr_region
        self.ocr_interval = ocr_interval
        self.whisper_model_name = whisper_model
        self.use_faster_whisper = use_faster_whisper
        # 注意：说话人分离功能暂未实现，该参数仅为保持接口兼容而保留
        self.speaker_diarization = speaker_diarization
        if speaker_diarization:
            print("警告：说话人分离功能暂未实现，该参数不会生效")
        self.ocr_method = ocr_method
        self.enable_ocr = enable_ocr
        self.enable_voice = enable_voice

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

        # 使用轻量版 OCR 模型（ch_PP-OCRv4_mobile）
        # 注意：新版 PaddleOCR 通过环境变量控制 GPU
        self.paddle_ocr = PaddleOCR(
            lang="ch"
        )
        print(f"PaddleOCR 初始化完成（使用轻量版模型）")

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

    def _ocr_from_frame(self, frame: np.ndarray) -> str:
        """从单帧提取 OCR 文字"""
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
            return ""

        try:
            result = self.paddle_ocr.ocr(roi)
            if result and result[0]:
                texts = [line[1][0] for line in result[0] if line and len(line) > 1]
                combined_text = " ".join(texts).strip()
                # 转换为简体字
                if self.cc_converter:
                    combined_text = self.cc_converter.convert(combined_text)
                # 过滤无意义文本
                filtered_text = self._filter_text(combined_text)
                return filtered_text
        except Exception as e:
            print(f"OCR 识别失败：{e}")
        return ""

    def extract_ocr_lines(self) -> List[Dict[str, Any]]:
        """从视频画面提取 OCR 台词"""
        if not self.enable_ocr or not self.paddle_ocr:
            return []

        print("开始画面 OCR 台词识别...")
        cap = cv2.VideoCapture(self.video_path)
        fps = self.fps
        total_frames = self.total_frames

        ocr_results = []
        last_text = ""
        last_time = -1

        for frame_idx in range(0, total_frames, self.ocr_interval):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            text = self._ocr_from_frame(frame)
            if text:
                current_time = frame_idx / fps
                
                # 去重：如果与上次识别的文字相同且时间接近，跳过
                if text == last_text and (current_time - last_time) < 1.0:
                    continue
                
                ocr_results.append({
                    'text': text,
                    'start': current_time,
                    'end': current_time,
                    'source': 'ocr'
                })
                last_text = text
                last_time = current_time

        cap.release()
        print(f"OCR 识别完成，得到 {len(ocr_results)} 条原始台词")

        # 合并相近时间的文本
        merged = []
        for res in ocr_results:
            if not merged:
                merged.append(res)
            else:
                last = merged[-1]
                if res['start'] - last['end'] <= 0.5:
                    last['end'] = max(last['end'], res['end'])
                    if res['text'] != last['text']:
                        last['text'] = last['text'] + " " + res['text']
                else:
                    merged.append(res)
        
        print(f"OCR 台词合并后共 {len(merged)} 条")
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
                try:
                    model = WhisperModel(
                        self.whisper_model_name,
                        device=self.device,
                        compute_type="float16" if self.device == "cuda" else "float32"
                    )
                    segments, info = model.transcribe(
                        audio_path,
                        language="zh",
                        beam_size=5,
                        word_timestamps=False
                    )
                    
                    segments_list = []
                    for seg in segments:
                        segments_list.append({
                            "text": seg.text.strip(),
                            "start": seg.start,
                            "end": seg.end,
                            "source": "voice"
                        })
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

    def _transcribe_with_openai_whisper(self, audio_path: str) -> List[Dict[str, Any]]:
        """使用 openai-whisper 进行语音识别"""
        import whisper
        
        print(f"加载 openai-whisper 模型：{self.whisper_model_name} on {self.device}")
        model = whisper.load_model(self.whisper_model_name, device=self.device)
        
        result = model.transcribe(
            audio_path,
            language="zh",
            initial_prompt="以下是普通话的简体中文字幕。",
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
                "source": "voice"
            })
        return segments

    def merge_and_deduplicate(self, ocr_lines: List[Dict[str, Any]], 
                              voice_lines: List[Dict[str, Any]]) -> List[str]:
        """合并 OCR 和语音识别结果，去重"""
        all_lines = ocr_lines + voice_lines
        all_lines.sort(key=lambda x: x['start'])

        # 合并相近时间的文本
        merged = []
        for line in all_lines:
            if not merged:
                merged.append(line)
            else:
                last = merged[-1]
                if line['start'] - last['end'] <= 1.0:
                    if line['text'] != last['text']:
                        last['text'] = last['text'] + " " + line['text']
                    last['end'] = max(last['end'], line['end'])
                else:
                    merged.append(line)

        # 提取纯文本并去重
        texts = [line['text'] for line in merged if line['text']]
        
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

    def run(self, output_path: str = None):
        """执行台词提取"""
        ocr_lines = []
        voice_lines = []

        try:
            # OCR 识别
            if self.enable_ocr:
                ocr_lines = self.extract_ocr_lines()

            # 语音识别
            if self.enable_voice:
                voice_lines = self.extract_audio_transcription()

            # 合并输出
            unique_texts = self.merge_and_deduplicate(ocr_lines, voice_lines)

            # 输出结果
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(unique_texts))
                print(f"台词结果已保存到：{output_path}")
            else:
                for text in unique_texts:
                    print(text)

            return unique_texts
        finally:
            # 无论成功失败都释放视频句柄
            self.close()


def main():
    parser = argparse.ArgumentParser(description="视频台词识别工具（增强版，支持 OCR+ 语音识别）")
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--output", default="role_lines.txt", help="输出台词文件路径")
    parser.add_argument("--whisper_model", default="base", 
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper 模型大小（越大越准确，但越慢）")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], 
                        help="计算设备")
    parser.add_argument("--ocr_region", type=str, default="0,0.7,1,0.95", 
                        help="OCR 区域相对坐标 x1,y1,x2,y2")
    parser.add_argument("--ocr_interval", type=int, default=5, 
                        help="OCR 帧间隔（越大越快，但可能漏识别）")
    parser.add_argument("--use_faster_whisper", action="store_true",
                        help="强制使用 faster-whisper（默认会自动挑已安装的那个后端）")
    parser.add_argument("--disable_ocr", action="store_true",
                        help="禁用 OCR 识别（仅使用语音识别）")
    parser.add_argument("--disable_voice", action="store_true",
                        help="禁用语音识别（仅使用 OCR 识别）")
    parser.add_argument("--interactive_ocr", action="store_true",
                        help="启用交互式 OCR 区域设置（命令行输入模式）")
    args = parser.parse_args()

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

    extractor = RoleLineExtractor(
        video_path=args.video,
        whisper_model=args.whisper_model,
        device=args.device,
        ocr_region=ocr_region,
        ocr_interval=args.ocr_interval,
        use_faster_whisper=args.use_faster_whisper,
        enable_ocr=not args.disable_ocr,
        enable_voice=not args.disable_voice,
        interactive_ocr=args.interactive_ocr
    )
    extractor.run(output_path=args.output)


if __name__ == "__main__":
    main()
