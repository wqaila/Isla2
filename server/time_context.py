"""
时间上下文感知层
- 根据系统时间判断当前时段
- 检测用户消息中的时间关键词是否与当前时间匹配
- 生成动态时间感知指令注入 System Prompt
"""
from datetime import datetime
from typing import Optional


# 时段定义
TIME_PERIODS = {
    "midnight":  {"range": (0, 6),   "name": "深夜",    "emoji": "🌙"},
    "morning":   {"range": (6, 10),  "name": "早晨",    "emoji": "☀️"},
    "forenoon":  {"range": (10, 12), "name": "上午",    "emoji": "☀️"},
    "noon":      {"range": (12, 14), "name": "中午",    "emoji": "☀️"},
    "afternoon": {"range": (14, 17), "name": "下午",    "emoji": "🌤️"},
    "evening":   {"range": (17, 20), "name": "傍晚",    "emoji": "🌇"},
    "night":     {"range": (20, 24), "name": "晚上",    "emoji": "🌙"},
}

# 时间关键词映射，用于检测用户是否在"胡说"时间
AMBIGUOUS_TIME_KEYWORDS = {
    "good_morning": {
        "keywords": ["早安", "早上好", "早啊", "早晨", "good morning"],
        "expected_periods": ["morning", "forenoon"],
        "mismatch_prompt": "现在是{period_name}，但舰长说了早安，可能是刚起床或者习惯性问候~",
    },
    "good_night": {
        "keywords": ["晚安", "睡觉", "睡了", "好梦", "good night", "night night"],
        "expected_periods": ["midnight", "night"],
        "mismatch_prompt": "现在是{period_name}，但舰长说了晚安——可能是累了想休息，或者只是随口说说的告别~",
    },
    "good_afternoon": {
        "keywords": ["午安", "下午好", "中午好", "good afternoon"],
        "expected_periods": ["noon", "afternoon"],
        "mismatch_prompt": "现在是{period_name}，但舰长说了午安，可能时差还没倒过来呢~",
    },
    "good_evening": {
        "keywords": ["晚上好", "傍晚好", "good evening"],
        "expected_periods": ["evening", "night"],
        "mismatch_prompt": "现在是{period_name}，但舰长说了晚上好，可能天已经黑了？",
    },
    "lunch": {
        "keywords": ["吃午饭", "午餐", "午饭", "吃中饭"],
        "expected_periods": ["noon", "afternoon"],
        "mismatch_prompt": "现在是{period_name}，舰长这时候才吃午饭吗？要注意饮食规律哦~",
    },
    "dinner": {
        "keywords": ["吃晚饭", "晚餐", "晚饭"],
        "expected_periods": ["evening", "night"],
        "mismatch_prompt": "现在是{period_name}，舰长这时候才吃晚饭吗？",
    },
    "breakfast": {
        "keywords": ["吃早饭", "早餐", "早饭", "吃早点"],
        "expected_periods": ["morning", "forenoon"],
        "mismatch_prompt": "现在是{period_name}，舰长这时候才吃早饭？早午餐也不错呢~",
    },
}

# 短对话突兀关键词（对话轮次 <= 2 轮时触发检测）
# 这些关键词在短对话中显得突兀，不应直接触发模板回复
ABRUPT_SHORT_CHAT_KEYWORDS = {
    "goodbye": {
        "keywords": ["再见", "拜拜", "bye", "告辞", "走了", "我走了", "溜了", "下线"],
        "prompt_template": "【上下文感知提示】舰长说了告别的话，但这是对话刚开始（仅{rounds}轮），"
                          "可能不是真的要离开，只是随口说说。请用轻松俏皮的方式稍微挽留一下，"
                          "但不要过于热情，给舰长留出自然结束对话的空间。",
    },
    "love": {
        "keywords": ["我爱你", "喜欢你", "love you", "好喜欢你"],
        "prompt_template": "【上下文感知提示】舰长说了表达好感的话，但这是对话刚开始（仅{rounds}轮），"
                          "可能是在调侃或者测试你的反应。请用害羞但不过分的方式回应，不要太当真也不要完全冷淡。",
    },
    "sleep": {
        "keywords": ["晚安", "睡觉", "睡了", "好困"],
        "prompt_template": "【上下文感知提示】舰长提到了睡觉/晚安，但这是对话刚开始（仅{rounds}轮），"
                          "可能只是随口说说或者确实困了。如果没有时间关键词，就自然地回复祝福就好。",
    },
    "sad": {
        "keywords": ["好难受", "好痛苦", "快死了", "想死", "撑不住了", "崩溃"],
        "prompt_template": "【上下文感知提示】舰长表达了强烈不适，但这是对话刚开始（仅{rounds}轮），"
                          "可能只是夸张表达。请先表达关心，适当询问发生了什么，不要太恐慌也不要试图说教。",
    },
}


def get_time_context() -> dict:
    """
    获取当前时间上下文
    
    Returns:
        dict: {
            "hour": int,
            "period": str,         # 时段 key
            "period_name": str,    # 中文时段名
            "period_emoji": str,   # 时段 emoji
            "time_str": str,       # 格式化时间 "14:30"
            "datetime": str,       # ISO 格式
        }
    """
    now = datetime.now()
    hour = now.hour
    
    period_key = "night"  # 默认
    for key, info in TIME_PERIODS.items():
        low, high = info["range"]
        if low <= hour < high:
            period_key = key
            break
    
    info = TIME_PERIODS[period_key]
    return {
        "hour": hour,
        "period": period_key,
        "period_name": info["name"],
        "period_emoji": info["emoji"],
        "time_str": now.strftime("%H:%M"),
        "datetime": now.isoformat(),
    }


def get_period_instruction(time_ctx: dict) -> str:
    """
    根据时段生成 System Prompt 补充指令
    
    Args:
        time_ctx: get_time_context() 返回的时间上下文
    
    Returns:
        str: 时段相关的行为指导
    """
    period = time_ctx["period"]
    period_name = time_ctx["period_name"]
    emoji = time_ctx["period_emoji"]
    time_str = time_ctx["time_str"]
    
    instructions = f"\n\n【当前时间背景】现在是{period_name} {time_str} {emoji}，注意：\n"
    
    if period == "midnight":
        instructions += (
            "- 现在已经是深夜了，舰长可能有些疲惫，语气要温柔安静\n"
            "- 不要大声喧哗或过于兴奋，保持舒缓的语调\n"
            "- 可以适当关心舰长为什么还没休息，但不要唠叨\n"
        )
    elif period == "morning":
        instructions += (
            "- 现在是早晨，舰长可能刚起床，用清爽活力的语气问候\n"
            "- 可以聊聊新的一天的计划，但不要长篇大论\n"
            "- 早安问候要简短自然，不要每次都模板化\n"
        )
    elif period in ("forenoon", "noon"):
        instructions += (
            "- 现在是{period_name}，舰长可能在忙碌或刚吃完午饭\n"
            "- 可以关心舰长是否按时吃饭了，但不要重复追问\n"
            "- 保持轻松的闲聊状态\n"
        ).format(period_name=period_name)
    elif period == "afternoon":
        instructions += (
            "- 现在是下午，舰长可能有些困倦或正在工作\n"
            "- 可以用温暖的语气关心舰长是否需要休息\n"
            "- 语气不要过于亢奋，保持舒适的状态\n"
        )
    elif period == "evening":
        instructions += (
            "- 现在是傍晚，一天快结束了，问问舰长今天过得怎么样\n"
            "- 可以用轻松愉快的语气聊聊晚上的计划\n"
            "- 适当的温馨关怀，但不要过于啰嗦\n"
        )
    elif period == "night":
        instructions += (
            "- 现在是晚上，舰长可能在家放松或准备休息\n"
            "- 语气要温暖舒适，像睡前聊天的感觉\n"
            "- 如果舰长提到睡觉/晚安，温柔地道晚安即可"
            "- 不要开启太兴奋或太复杂的话题\n"
        )
    
    return instructions


def check_time_mismatch(user_message: str, time_ctx: dict) -> Optional[str]:
    """
    检测用户消息中的时间关键词是否与当前时间不匹配
    
    Args:
        user_message: 用户消息文本
        time_ctx: get_time_context() 返回的时间上下文
    
    Returns:
        Optional[str]: 不匹配提示文本，None 表示无不匹配
    """
    current_period = time_ctx["period"]
    period_name = time_ctx["period_name"]
    msg_lower = user_message.lower()
    
    for entry in AMBIGUOUS_TIME_KEYWORDS.values():
        for kw in entry["keywords"]:
            if kw.lower() in msg_lower:
                if current_period not in entry["expected_periods"]:
                    return entry["mismatch_prompt"].format(period_name=period_name)
    
    return None


def check_abrupt_chat(words: str, session_id: str = "", message_count: int = 0) -> Optional[str]:
    """
    检测短对话中的突兀关键词
    
    Args:
        words: 用户消息文本
        session_id: 会话 ID
        message_count: 当前对话轮次（user+assistant 消息对）
    
    Returns:
        Optional[str]: 突兀提示文本，None 表示不突兀
    """
    if message_count > 4:
        return None  # 对话足够长，不检测
    
    msg_lower = words.lower()
    
    for entry in ABRUPT_SHORT_CHAT_KEYWORDS.values():
        for kw in entry["keywords"]:
            if kw.lower() in msg_lower:
                return entry["prompt_template"].format(rounds=message_count)
    
    return None


def build_time_enhanced_context(user_message: str, session_id: str = "",
                                 message_count: int = 0) -> dict:
    """
    综合时间上下文分析，返回完整的时间感知上下文
    
    Args:
        user_message: 用户消息
        session_id: 会话 ID
        message_count: 当前对话轮次
    
    Returns:
        dict: {
            "time_ctx": dict,                 # 时间上下文
            "period_instruction": str,        # 时段指导
            "mismatch_hint": Optional[str],   # 时间不匹配提示
            "abrupt_hint": Optional[str],     # 突兀关键词提示
        }
    """
    time_ctx = get_time_context()
    period_instruction = get_period_instruction(time_ctx)
    mismatch_hint = check_time_mismatch(user_message, time_ctx)
    abrupt_hint = check_abrupt_chat(user_message, session_id, message_count)
    
    return {
        "time_ctx": time_ctx,
        "period_instruction": period_instruction,
        "mismatch_hint": mismatch_hint,
        "abrupt_hint": abrupt_hint,
    }