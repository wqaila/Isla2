"""
情感识别引擎 (Emotion Engine)
对用户消息进行多维情感分析，生成情绪自适应的 AI 回复指导

功能：
1. 多维情感分析（喜悦/悲伤/愤怒/恐惧/惊讶/厌恶/中性）
2. 情绪强度评分
3. 情绪趋势追踪
4. 生成情绪自适应的 Prompt 指令（注入 AI System Prompt）
"""

import re
import math
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# ===== 情感维度定义 =====
EMOTION_DIMENSIONS = {
    "joy": {"label": "喜悦", "emoji": "😊", "valence": 1.0, "arousal": 0.7},
    "sadness": {"label": "悲伤", "emoji": "😢", "valence": -0.8, "arousal": -0.5},
    "anger": {"label": "愤怒", "emoji": "😡", "valence": -0.9, "arousal": 0.9},
    "fear": {"label": "恐惧", "emoji": "😨", "valence": -0.7, "arousal": 0.8},
    "surprise": {"label": "惊讶", "emoji": "😲", "valence": 0.2, "arousal": 0.9},
    "disgust": {"label": "厌恶", "emoji": "😖", "valence": -0.6, "arousal": 0.3},
    "neutral": {"label": "中性", "emoji": "😶", "valence": 0.0, "arousal": 0.0},
}

# ===== 情感关键词库 =====
EMOTION_KEYWORDS = {
    "joy": {
        "strong": [
            "太开心了", "高兴死了", "兴奋极了", "幸福的", "超快乐", "激动人心",
            "欣喜若狂", "乐坏了", "爽翻了", "绝了", "牛", "好耶",
            "哈哈哈", "嘿嘿嘿", "嘻嘻", "😄", "😆", "🤣", "😁", "🎉", "✨", "💖",
        ],
        "medium": [
            "开心", "高兴", "快乐", "开心呀", "喜欢", "爱了", "棒", "赞",
            "太好了", "不错", "哈哈", "😊", "👍", "❤️", "😍",
        ],
        "weak": [
            "还行", "还好", "不错吧", "可以", "嗯嗯", "好的呀", "好哦",
        ],
    },
    "sadness": {
        "strong": [
            "难过死了", "伤心欲绝", "心碎了", "崩溃了", "绝望", "痛苦",
            "生无可恋", "哭死", "好想哭", "😭", "💔", "😢",
        ],
        "medium": [
            "难过", "伤心", "失望", "失落", "郁闷", "不开心", "心累",
            "孤独", "寂寞", "想哭", "唉", "😞", "😔", "😿",
        ],
        "weak": [
            "有点难过", "不太开心", "心情不好", "烦", "唉",
        ],
    },
    "anger": {
        "strong": [
            "气死我了", "怒了", "暴躁", "想骂人", "气得", "火大",
            "忍不了", "受够了", "😡", "🤬", "😤",
        ],
        "medium": [
            "生气", "愤怒", "讨厌", "烦人", "无语了", "恶心",
            "别烦我", "走开", "滚", "😠",
        ],
        "weak": [
            "有点气", "不爽", "恼火", "啧",
        ],
    },
    "fear": {
        "strong": [
            "吓死我了", "好恐怖", "毛骨悚然", "恐惧", "不敢",
            "😱", "😨", "😰",
        ],
        "medium": [
            "害怕", "担心", "焦虑", "紧张", "不安", "怕",
            "忧虑", "忐忑", "慌",
        ],
        "weak": [
            "有点担心", "有点紧张", "有点怕",
        ],
    },
    "surprise": {
        "strong": [
            "天哪", "不会吧", "真的假的", "震惊", "不可思议", "离谱",
            "我的天", "😲", "😮", "🤯",
        ],
        "medium": [
            "惊讶", "吃惊", "意外", "没想到", "哇", "哦？", "咦",
            "真的吗", "😯",
        ],
        "weak": [
            "哦", "嗯？", "是吗",
        ],
    },
    "disgust": {
        "strong": [
            "恶心死了", "想吐", "令人作呕", "太恶心", "🤮", "🤢",
        ],
        "medium": [
            "讨厌", "反感", "嫌弃", "不喜欢", "受不了", "😒",
        ],
        "weak": [
            "不太喜欢", "有点反感",
        ],
    },
}

# ===== 情绪自适应 Prompt 模板 =====
EMOTION_ADAPTIVE_PROMPTS = {
    "joy": """
【用户当前情绪：喜悦/开心】
- 可以和用户一起开心，用更活泼俏皮的语气
- 适当多用「~」「✨」等欢快的符号
- 顺势夸赞用户，分享快乐情绪
""",
    "sadness": """
【用户当前情绪：悲伤/低落】
- 语气要温柔、体贴，表现出真诚的关心
- 适当安慰用户，给予情感支持
- 可以尝试转移话题到轻松愉快的内容
- 少用过于欢脱的符号，保持温暖但不聒噪
""",
    "anger": """
【用户当前情绪：愤怒/烦躁】
- 保持冷静和理解，不要火上浇油
- 先共情安抚："听起来确实让人生气呢..."
- 语气平和温柔，避免俏皮轻浮
- 可以尝试帮用户理性分析问题
""",
    "fear": """
【用户当前情绪：恐惧/焦虑】
- 语气要镇定、让人安心
- 给予安全感和鼓励："有爱莉在呢~"
- 不要说"别怕"这种否定情绪的话，改为"我陪着你"
- 适当转移注意力到积极的方向
""",
    "surprise": """
【用户当前情绪：惊讶/意外】
- 一起表达惊讶，增加互动感
- 可以追问细节，表达好奇心
- 保持活泼趣味
""",
    "disgust": """
【用户当前情绪：反感/厌恶】
- 表示理解和认同
- 不要展开讨论令用户反感的话题
- 自然转移到其他话题
""",
    "neutral": """
【用户当前情绪：中性/平静】
- 保持正常聊天节奏
- 可以适当引导有趣的话题
""",
}


@dataclass
class EmotionResult:
    """情感分析结果"""
    dominant: str                          # 主导情绪
    scores: dict                           # 各维度分数 {emotion: score}
    intensity: float                       # 情绪强度 0-1
    valence: float                         # 情感效价 -1(负) 到 +1(正)
    arousal: float                         # 唤醒度 -1(平静) 到 +1(激动)
    adaptive_prompt: str                   # 情绪自适应 Prompt
    emoji: str                             # 对应 emoji
    timestamp: str                         # 分析时间戳
    summary: str                           # 情绪摘要


class EmotionEngine:
    """情感识别引擎"""
    
    def __init__(self):
        self.keyword_scores = self._build_keyword_scores()
    
    def _build_keyword_scores(self) -> dict:
        """构建关键词及其对应分数的查找表"""
        scores = {}
        for emotion, levels in EMOTION_KEYWORDS.items():
            for level_name, keywords in levels.items():
                if level_name == "strong":
                    weight = 0.9
                elif level_name == "medium":
                    weight = 0.6
                else:
                    weight = 0.3
                for kw in keywords:
                    scores[kw] = (emotion, weight)
        return scores
    
    def analyze(self, text: str) -> EmotionResult:
        """
        对输入文本进行多维情感分析
        
        Args:
            text: 用户输入文本
        
        Returns:
            EmotionResult: 包含所有情感维度分数的结果
        """
        if not text or not text.strip():
            return EmotionResult(
                dominant="neutral",
                scores={"neutral": 1.0, "joy": 0, "sadness": 0, "anger": 0,
                        "fear": 0, "surprise": 0, "disgust": 0},
                intensity=0.0,
                valence=0.0,
                arousal=0.0,
                adaptive_prompt=EMOTION_ADAPTIVE_PROMPTS["neutral"],
                emoji="😶",
                timestamp=datetime.now().isoformat(),
                summary="无有效输入文本",
            )
        
        # === 第 1 层：关键词匹配 ===
        raw_scores = defaultdict(float)
        for kw, (emotion, weight) in self.keyword_scores.items():
            count = text.count(kw)
            if count > 0:
                raw_scores[emotion] += weight * min(count, 3)  # 最多计数 3 次避免过拟合
        
        # === 第 2 层：句式特征分析 ===
        self._analyze_sentence_patterns(text, raw_scores)
        
        # === 第 3 层：标点符和表情号分析 ===
        self._analyze_punctuation(text, raw_scores)
        
        # === 第 4 层：否定词处理 ===
        negation_words = ["不", "没", "别", "无", "非"]
        if any(neg in text for neg in negation_words):
            # 否定会降低积极情绪的分数，略微提升消极
            if raw_scores.get("joy", 0) > 0:
                raw_scores["joy"] *= 0.4
                raw_scores["sadness"] += 0.2
        
        # === 归一化 ===
        scores = {}
        total = sum(raw_scores.values())
        if total > 0:
            for em in EMOTION_DIMENSIONS:
                scores[em] = round(raw_scores.get(em, 0) / total, 3)
        else:
            # 完全没有任何情感匹配 → 中性
            for em in EMOTION_DIMENSIONS:
                scores[em] = 0.0
            scores["neutral"] = 1.0
        
        # 确保 neutral 有一个基础分数（如果其他情感都很低）
        non_neutral_sum = sum(v for k, v in scores.items() if k != "neutral")
        if non_neutral_sum < 0.15:
            scores = {k: 0.0 for k in EMOTION_DIMENSIONS}
            scores["neutral"] = 1.0
        
        # === 确定主导情绪 ===
        dominant = max(scores, key=scores.get)
        
        # === 情绪强度 ===
        intensity = 1.0 - scores.get("neutral", 1.0)
        intensity = round(min(intensity, 1.0), 3)
        
        # === 情感效价和唤醒度（加权平均）===
        valence = 0.0
        arousal = 0.0
        for em, dim in EMOTION_DIMENSIONS.items():
            if em != "neutral":
                valence += scores[em] * dim["valence"]
                arousal += scores[em] * dim["arousal"]
        valence = round(max(-1.0, min(1.0, valence)), 3)
        arousal = round(max(-1.0, min(1.0, arousal)), 3)
        
        # === 生成自适应 Prompt ===
        adaptive_prompt = EMOTION_ADAPTIVE_PROMPTS.get(dominant, EMOTION_ADAPTIVE_PROMPTS["neutral"])
        
        # === 生成摘要 ===
        dim_info = EMOTION_DIMENSIONS.get(dominant, EMOTION_DIMENSIONS["neutral"])
        summary = f"主导情绪: {dim_info['label']}({dim_info['emoji']}), 强度: {intensity:.1%}"
        
        return EmotionResult(
            dominant=dominant,
            scores=scores,
            intensity=intensity,
            valence=valence,
            arousal=arousal,
            adaptive_prompt=adaptive_prompt,
            emoji=dim_info["emoji"],
            timestamp=datetime.now().isoformat(),
            summary=summary,
        )
    
    def _analyze_sentence_patterns(self, text: str, scores: dict):
        """分析句式特征"""
        # 感叹句 → 通常情绪较强
        if "！" in text or "!" in text:
            # 判断是否正面
            positive_indicators = ["好", "棒", "赞", "美", "绝", "帅", "牛", "厉害", "优秀"]
            negative_indicators = ["糟", "差", "坏", "烂", "惨", "烦", "累", "疼"]
            if any(w in text for w in positive_indicators):
                scores["joy"] += 0.4
                scores["surprise"] += 0.2
            elif any(w in text for w in negative_indicators):
                scores["sadness"] += 0.3
                scores["anger"] += 0.3
        
        # 反问句 → 可能带有愤怒或不满
        if re.search(r"(难道|凭什么|为什么|怎么这样|怎么回事)", text):
            if "?" in text or "？" in text:
                scores["anger"] += 0.4
                scores["surprise"] += 0.2
        
        # 省略号 → 犹豫/悲伤
        if "..." in text or "……" in text:
            scores["sadness"] += 0.2
            scores["fear"] += 0.1
        
        # 连续重复字 → 情绪较强
        repeats = re.findall(r"(.)\1{2,}", text)
        if repeats:
            scores["surprise"] += 0.2
    
    def _analyze_punctuation(self, text: str, scores: dict):
        """分析标点符号和表情符号"""
        # 连续感叹号 → 强烈情绪
        if re.search(r"！{2,}|!{2,}", text):
            scores["surprise"] += 0.3
        
        # 连续问号 → 困惑/惊讶
        if re.search(r"？{2,}|\?{2,}", text):
            scores["surprise"] += 0.2
            scores["fear"] += 0.1
        
        # emoji 表情分析（中文常用符号）
        happy_emojis = ["😊", "😄", "😆", "🤣", "😁", "🎉", "✨", "💖", "❤️", "👍", "😍", "🥰"]
        sad_emojis = ["😢", "😭", "💔", "😞", "😔", "😿", "🥺"]
        angry_emojis = ["😡", "🤬", "😤", "😠"]
        fear_emojis = ["😱", "😨", "😰"]
        disgust_emojis = ["🤮", "🤢"]
        surprise_emojis = ["😲", "😮", "🤯"]
        
        for emoji in happy_emojis:
            scores["joy"] += text.count(emoji) * 0.3
        for emoji in sad_emojis:
            scores["sadness"] += text.count(emoji) * 0.3
        for emoji in angry_emojis:
            scores["anger"] += text.count(emoji) * 0.3
        for emoji in fear_emojis:
            scores["fear"] += text.count(emoji) * 0.3
        for emoji in disgust_emojis:
            scores["disgust"] += text.count(emoji) * 0.3
        for emoji in surprise_emojis:
            scores["surprise"] += text.count(emoji) * 0.3
    
    def get_adaptive_instruction(self, result: EmotionResult) -> str:
        """
        根据情感分析结果，生成注入到 AI System Prompt 的情绪自适应指令
        
        Returns:
            str: 情绪自适应指令文本
        """
        instruction = result.adaptive_prompt
        
        # 如果是强烈的负面情绪，添加更强的指导
        if result.dominant in ("sadness", "anger", "fear") and result.intensity > 0.6:
            instruction += """
⚠️ 用户当前情绪较强烈，请注意：
- 不要开玩笑或使用轻浮的语气
- 给予充分的理解和共情
- 谨慎选择话题
"""
        
        return instruction


# ===== 情绪趋势追踪器 =====

@dataclass
class MoodTracker:
    """追踪用户情绪变化趋势"""
    history: list = field(default_factory=list)  # [{timestamp, dominant, intensity, valence, ...}]
    max_history: int = 200
    
    def record(self, result: EmotionResult):
        """记录一次情绪分析结果"""
        self.history.append({
            "timestamp": result.timestamp,
            "dominant": result.dominant,
            "intensity": result.intensity,
            "valence": result.valence,
            "arousal": result.arousal,
            "summary": result.summary,
        })
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def get_trend(self, window: int = 10) -> dict:
        """
        获取最近 window 条消息的情绪趋势
        
        Returns:
            dict: {
                "recent_valence": float,      # 近期平均效价
                "trend": str,                 # "improving"/"declining"/"stable"
                "dominant_moods": list,       # 近期主导情绪分布
                "mood_swings": int,           # 情绪波动次数
            }
        """
        recent = self.history[-window:] if len(self.history) >= window else self.history
        if not recent:
            return {"recent_valence": 0, "trend": "stable", "dominant_moods": [], "mood_swings": 0}
        
        # 平均效价
        avg_valence = sum(r["valence"] for r in recent) / len(recent)
        
        # 趋势判断（比较前后半段）
        half = len(recent) // 2
        if half >= 2:
            first_half_avg = sum(r["valence"] for r in recent[:half]) / half
            second_half_avg = sum(r["valence"] for r in recent[half:]) / (len(recent) - half)
            diff = second_half_avg - first_half_avg
            if diff > 0.15:
                trend = "improving"
            elif diff < -0.15:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "stable"
        
        # 主导情绪分布
        mood_counts = defaultdict(int)
        for r in recent:
            mood_counts[r["dominant"]] += 1
        dominant_moods = sorted(mood_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # 情绪波动次数（相邻两轮情绪不同则算一次波动）
        mood_swings = 0
        prev_dominant = None
        for r in recent:
            if prev_dominant and r["dominant"] != prev_dominant:
                mood_swings += 1
            prev_dominant = r["dominant"]
        
        return {
            "recent_valence": round(avg_valence, 3),
            "trend": trend,
            "dominant_moods": [{"mood": m, "count": c} for m, c in dominant_moods],
            "mood_swings": mood_swings,
            "sample_size": len(recent),
        }
    
    def get_trend_instruction(self, window: int = 10) -> str:
        """
        根据情绪趋势生成 AI 行为指导
        
        Returns:
            str: 趋势分析指导文本
        """
        trend_data = self.get_trend(window)
        
        if trend_data["sample_size"] < 3:
            return ""
        
        instructions = []
        
        # 持续负面情绪 → 特别关注
        if trend_data["recent_valence"] < -0.3:
            if trend_data["trend"] == "declining":
                instructions.append(
                    "⚠️ 舰长最近情绪持续低落，请在回复中多一些温暖的鼓励和关怀，"
                    "试着用温柔的语气让对方感受到陪伴。"
                )
            else:
                instructions.append(
                    "舰长最近情绪不太好，请保持温柔体贴的语气，"
                    "适当给出一些暖心的话语。"
                )
        
        # 情绪波动大 → 稳定化处理
        if trend_data["mood_swings"] >= 3 and trend_data["sample_size"] >= 5:
            instructions.append(
                "舰长情绪有些起伏，请保持稳定温和的语气，"
                "不要突然转换话题或使用过于强烈的语气。"
            )
        
        # 持续积极 → 可以更活泼
        if trend_data["recent_valence"] > 0.3 and trend_data["trend"] in ("improving", "stable"):
            instructions.append(
                "舰长最近心情不错呢~可以更活泼俏皮一些，"
                "多用欢快的语气和表达方式。"
            )
        
        return "\n".join(instructions)


# ===== 全局实例 =====
_emotion_engine: Optional[EmotionEngine] = None
_mood_trackers: dict[str, MoodTracker] = {}
_max_trackers = 50  # 防止无限制增长


# ==================== AI 回复情绪分析 & 一致性检查 (v3 新增) ====================

# 一致性阈值：大于此值认为情绪偏差显著
CONSISTENCY_THRESHOLD = 0.45

# 回复情绪自适应修正指令
CONSISTENCY_ADVISOR_PROMPTS = {
    "overly_happy": "【情绪修正提示】你的回复比舰长的情绪欢快太多——舰长可能比较低落，"
                    "你的过度兴奋会显得不够体谅。下次请先共情舰长的感受，再适当引导。",
    "overly_sad": "【情绪修正提示】你的回复比舰长的情绪低落太多——舰长可能心情不错，"
                  "你的沮丧会扫兴。下次请配合舰长的正能量，保持开心。",
    "overly_angry": "【情绪修正提示】你的回复显得比舰长愤怒很多——舰长可能只是平常说话，"
                    "你的愤怒会让人摸不着头脑。下次请冷静回应。",
    "overly_calm": "【情绪修正提示】你的回复显得比舰长冷静很多——舰长可能情绪高涨，"
                   "你的冷静会显得冷淡。下次请更热情地回应舰长。",
}


def analyze_generated_response(response_text: str) -> dict:
    """
    对 AI 生成的回复进行事后情绪分析（复用 EmotionEngine 的完整分析能力）
    
    Args:
        response_text: AI 生成的回复文本
    
    Returns:
        dict: {
            "dominant": str,        # 主导情绪
            "valence": float,       # 效价 -1.0~1.0
            "arousal": float,       # 唤醒度 -1.0~1.0
            "intensity": float,     # 情绪强度 0.0~1.0
            "scores": dict,         # 各情绪分数
        }
    """
    engine = get_emotion_engine()
    result = engine.analyze(response_text)
    return {
        "dominant": result.dominant,
        "valence": result.valence,
        "arousal": result.arousal,
        "intensity": result.intensity,
        "scores": result.scores,
    }


def check_response_consistency(user_emotion: dict, response_emotion: dict) -> dict:
    """
    比较用户情绪与 AI 回复情绪的一致性
    
    Args:
        user_emotion: 用户消息的情绪分析结果
        response_emotion: AI 回复的情绪分析结果
    
    Returns:
        dict: {
            "consistent": bool,          # 是否一致
            "deviation": float,          # 偏差值 0.0~1.0
            "mismatch_type": str,        # 不匹配类型
            "advice": Optional[str],     # 修正建议（如果不一致）
            "user_valence": float,
            "response_valence": float,
            "user_arousal": float,
            "response_arousal": float,
        }
    """
    u_valence = user_emotion.get("valence", 0.0)
    r_valence = response_emotion.get("valence", 0.0)
    u_arousal = user_emotion.get("arousal", 0.0)
    r_arousal = response_emotion.get("arousal", 0.0)
    
    # 计算情绪空间中的欧氏距离偏差
    # 效价和唤醒度构成 2D 情绪空间
    deviation = ((u_valence - r_valence) ** 2 + (u_arousal - r_arousal) ** 2) ** 0.5
    deviation = round(deviation / 2.0, 3)  # 归一化到 0~1（最大距离 ~2）
    
    consistent = deviation < CONSISTENCY_THRESHOLD
    
    # 判断不匹配类型
    mismatch_type = "aligned"
    advice = None
    
    if not consistent:
        # 效价偏差检测
        valence_gap = r_valence - u_valence
        if valence_gap > 0.35:
            mismatch_type = "overly_happy"
            advice = CONSISTENCY_ADVISOR_PROMPTS["overly_happy"]
        elif valence_gap < -0.35:
            mismatch_type = "overly_sad"
            advice = CONSISTENCY_ADVISOR_PROMPTS["overly_sad"]
        elif r_arousal > u_arousal + 0.4:
            mismatch_type = "overly_energetic"
            advice = "【情绪修正提示】你的回复比舰长情绪高涨很多——节奏可以放慢一点，先和舰长同步。"
        elif r_arousal < u_arousal - 0.4:
            mismatch_type = "overly_calm"
            advice = CONSISTENCY_ADVISOR_PROMPTS["overly_calm"]
        elif u_valence < -0.3 and r_valence > 0.1:
            mismatch_type = "overly_happy"
            advice = CONSISTENCY_ADVISOR_PROMPTS["overly_happy"]
        elif u_valence > 0.1 and r_valence < -0.3:
            mismatch_type = "overly_sad"
            advice = CONSISTENCY_ADVISOR_PROMPTS["overly_sad"]
        else:
            mismatch_type = "divergent"
            advice = "【情绪修正提示】你的回复和舰长的情绪状态不太合拍——下次请更仔细地感受舰长的情绪，做出更贴合氛围的回应。"
    
    return {
        "consistent": consistent,
        "deviation": deviation,
        "mismatch_type": mismatch_type,
        "advice": advice,
        "user_valence": u_valence,
        "response_valence": r_valence,
        "user_arousal": u_arousal,
        "response_arousal": r_arousal,
    }


def get_emotion_engine() -> EmotionEngine:
    """获取全局情感引擎实例（单例）"""
    global _emotion_engine
    if _emotion_engine is None:
        _emotion_engine = EmotionEngine()
    return _emotion_engine


def get_mood_tracker(session_id: str) -> MoodTracker:
    """获取指定会话的情绪追踪器（带 LRU 驱逐，防止内存泄漏）"""
    global _mood_trackers
    if session_id not in _mood_trackers:
        _mood_trackers[session_id] = MoodTracker()
    # 超过上限时驱逐最旧的 20%
    if len(_mood_trackers) > _max_trackers:
        evict_count = max(1, _max_trackers // 5)
        oldest = sorted(
            _mood_trackers.keys(),
            key=lambda sid: _mood_trackers[sid].history[-1]["timestamp"] if _mood_trackers[sid].history else "1970-01-01"
        )[:evict_count]
        for sid in oldest:
            del _mood_trackers[sid]
    return _mood_trackers[session_id]


def cleanup_mood_trackers():
    """清理所有情绪追踪器（优雅关闭时调用）"""
    global _mood_trackers
    _mood_trackers.clear()
