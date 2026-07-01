"""
用户学习引擎 - 智能体逐步学习用户，构建记忆神经网络

核心理念：
- 每次对话都是一次学习机会
- 用户画像随时间不断演化
- 知识节点之间形成加权关联网络
- AI 回复越来越个性化

架构：
    用户消息 → 多维度分析 → 知识图谱更新 → 个性化 Prompt 注入
                  │                │                    │
                  ▼                ▼                    ▼
            ┌──────────┐   ┌──────────────┐   ┌──────────────┐
            │ 性格分析   │   │ 记忆神经网络  │   │ 个性化回复    │
            │ 偏好提取   │   │ (加权图结构)  │   │ (Prompt增强)  │
            │ 情感识别   │   │              │   │              │
            │ 习惯学习   │   │ 节点=知识    │   │ 自然融入      │
            │ 话题分类   │   │ 边=关联强度  │   │ 用户偏好      │
            └──────────┘   └──────────────┘   └──────────────┘
"""
import json
import time
import hashlib
import re
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from config import BASE_DIR


# ===== 数据结构 =====

class UserProfile:
    """用户画像 - 存储所有学习到的用户信息"""
    
    def __init__(self):
        # 基本信息
        self.name = ""              # 用户名字
        self.gender = ""            # 性别
        self.age_range = ""         # 年龄段
        self.location = ""          # 地点
        self.occupation = ""        # 职业
        
        # 性格特征 (0-1 分值)
        self.personality = {
            "extraversion": 0.5,    # 外向性
            "agreeableness": 0.5,   # 宜人性
            "conscientiousness": 0.5, # 尽责性
            "neuroticism": 0.5,     # 神经质
            "openness": 0.5,        # 开放性
        }
        
        # 沟通风格
        self.communication_style = {
            "formality": 0.5,       # 正式程度 (0=随意, 1=正式)
            "verbosity": 0.5,       # 详细程度 (0=简洁, 1=详细)
            "humor": 0.5,           # 幽默程度
            "emoji_usage": 0.5,     # emoji 使用频率
            "question_rate": 0.5,   # 提问频率
        }
        
        # 偏好
        self.preferences = {
            "topics": {},           # 话题偏好 {topic: score}
            "response_length": "medium",  # 偏好回复长度
            "language_mix": "",     # 中英混用习惯
            "formality_pref": 0.5,  # 偏好的正式程度
        }
        
        # 活跃模式
        self.activity_patterns = {
            "active_hours": {},     # 活跃时间段 {hour: count}
            "active_days": {},      # 活跃星期 {day: count}
            "avg_session_length": 0, # 平均会话长度(分钟)
            "total_conversations": 0,
            "total_messages": 0,
        }
        
        # 情感状态追踪
        self.emotional_state = {
            "dominant_emotion": "neutral",  # 主导情绪
            "recent_sentiment": 0.5,        # 近期情感倾向 (0=消极, 1=积极)
            "mood_history": [],             # 情绪变化历史
        }
        
        # 关联知识图谱 (记忆神经网络)
        self.knowledge_graph = {
            "nodes": {},  # {node_id: {"content": str, "category": str, "weight": float, "created": str, "last_accessed": str, "access_count": int}}
            "edges": {},  # {edge_id: {"from": str, "to": str, "strength": float, "type": str}}
        }
        
        # 学习元数据
        self.learning_meta = {
            "first_seen": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
            "learning_sessions": 0,
            "confidence_score": 0.0,  # 整体置信度
        }
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "name": self.name,
            "gender": self.gender,
            "age_range": self.age_range,
            "location": self.location,
            "occupation": self.occupation,
            "personality": self.personality,
            "communication_style": self.communication_style,
            "preferences": self.preferences,
            "activity_patterns": self.activity_patterns,
            "emotional_state": self.emotional_state,
            "knowledge_graph": self.knowledge_graph,
            "learning_meta": self.learning_meta,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        """从字典反序列化"""
        profile = cls()
        for key, value in data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        return profile


# ===== 学习引擎 =====

class UserLearningEngine:
    """
    用户学习引擎
    
    核心能力：
    1. 从每条消息中提取多维度信息
    2. 增量更新用户画像
    3. 构建和维护知识图谱
    4. 生成个性化上下文
    """
    
    # 知识类别
    CATEGORIES = {
        "personal": "个人信息",
        "preference": "偏好喜好",
        "habit": "行为习惯",
        "emotion": "情感状态",
        "interest": "兴趣爱好",
        "work": "工作学习",
        "social": "社交关系",
        "opinion": "观点想法",
        "goal": "目标愿望",
        "routine": "日常规律",
    }
    
    
    def _init_patterns(self):
        """初始化学习模式"""
        
        # 个人信息提取模式
        self.personal_patterns = {
            "name": [
                r"我(?:叫|名字是|是)\s*([^\s，。！?？]{1,10})",
                r"你可以叫我\s*([^\s，。！?？]{1,10})",
                r"我的名字[是叫]\s*([^\s，。！?？]{1,10})",
            ],
            "age": [
                r"我(?:今年)?(\d{1,2})岁",
                r"我是(\d{2})年(?:出生|生)",
                r"我(?:是|属于)?\s*(\d{2})后",
            ],
            "gender": [
                r"我是(?:男生?|男|女孩?|女|男人|女人)",
                r"我(?:是|为)(?:男性|女性)",
            ],
            "location": [
                r"我(?:住在|在|来自|家在)\s*([^\s，。！?？]{2,10})",
            ],
            "occupation": [
                r"我是(?:一名?|个)?\s*(学生|程序员|工程师|医生|老师|设计师|教师|律师|会计|护士|警察|军人|作家|记者|厨师|司机|工人|农民|老板|经理|主管|总监|总裁|CEO|CTO|PM|产品经理|运营|销售|市场|HR|前端|后端|全栈|测试|运维|UI|UE|游戏|算法|数据|AI|人工智能|机器学习|深度学习|NLP|CV)",
                r"我(?:做|从事|干)\s*([^\s，。！?？]{2,10}(?:工作|行业|职业))",
                r"我的(?:工作|职业|岗位)[是做]\s*([^\s，。！?？]{2,10})",
            ],
        }
        
        # 情感分析模式
        self.emotion_patterns = {
            "positive": [
                r"(?:开心|高兴|快乐|兴奋|爽|棒|好|赞|爱|喜欢|感谢|谢谢|太好了|哈哈哈|嘿嘿|嘻嘻|✨|😊|😄|❤️|💖|👍)",
            ],
            "negative": [
                r"(?:难过|伤心|失望|烦|累|讨厌|无聊|焦虑|担心|害怕|生气|愤怒|痛|苦|糟糕|烦死了|唉|😢|😭|😡|😞|💔)",
            ],
            "excited": [
                r"(?:太棒了|太好了|真的吗|不会吧|哇|天哪|厉害|牛|强|绝了|OMG|！{2,}|🔥|🎉|💯)",
            ],
        }
        
        # 话题分类关键词
        self.topic_keywords = {
            "技术": ["编程", "代码", "开发", "程序", "软件", "bug", "API", "数据库", "前端", "后端", "Python", "Java", "JavaScript", "React", "Vue", "AI", "模型", "算法"],
            "游戏": ["游戏", "打游戏", "LOL", "王者", "原神", "崩坏", "Steam", "手游", "端游", "网游", "英雄联盟", "吃鸡"],
            "动漫": ["动漫", "番剧", "漫画", "二次元", "cos", "声优", "番", "新番", "追番", "B站", "鬼灭", "咒术", "进击"],
            "音乐": ["音乐", "歌", "听歌", "唱歌", "演唱会", "专辑", "歌手", "乐队", "钢琴", "吉他", "摇滚", "说唱"],
            "美食": ["吃", "美食", "好吃", "餐厅", "做饭", "烹饪", "火锅", "烧烤", "奶茶", "咖啡", "甜点", "面包"],
            "运动": ["运动", "健身", "跑步", "游泳", "篮球", "足球", "羽毛球", "瑜伽", "减肥", "锻炼", "体育"],
            "学习": ["学习", "考试", "作业", "论文", "大学", "学校", "课程", "知识", "看书", "阅读", "考研", "英语"],
            "工作": ["工作", "上班", "加班", "开会", "项目", "需求", "deadline", "绩效", "升职", "跳槽", "面试"],
            "情感": ["喜欢", "爱", "暗恋", "表白", "分手", "恋爱", "对象", "男朋友", "女朋友", "单身", "脱单"],
            "生活": ["天气", "睡觉", "起床", "洗澡", "出门", "回家", "交通", "地铁", "公交", "打车", "快递"],
        }
        
        # 沟通风格分析指标
        self.style_indicators = {
            "formal": ["您", "请问", "感谢", "麻烦", "方便", "打扰", "您好", "谢谢您"],
            "casual": ["哈哈", "嗯嗯", "哦哦", "好的呀", "嘞", "嘛", "呢", "吧", "啦"],
            "emoji_heavy": [r"[^\w\s]{2,}"],  # 多个非文字字符
            "question": [r"[？?]$", r"吗[？?]?$", r"呢[？?]?$", r"怎么", r"为什么", r"什么"],
        }
    
    # ===== 核心学习方法（内部辅助） =====
    
    def _extract_personal_info(self, text: str) -> dict:
        """提取个人信息"""
        info = {}
        for field, patterns in self.personal_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    info[field] = match.group(1) if match.lastindex else match.group(0)
                    break
        return info
    
    def _update_personal_info(self, info: dict):
        """更新个人信息（新信息覆盖旧信息）"""
        for field, value in info.items():
            if value and hasattr(self.profile, field):
                old = getattr(self.profile, field)
                if old != value:
                    setattr(self.profile, field, value)
                    # 添加到知识图谱
                    self._add_node(
                        f"personal_{field}",
                        f"用户的{field}是: {value}",
                        "personal",
                        weight=0.9,
                    )
    
    def _analyze_emotion(self, text: str) -> dict:
        """分析情感"""
        scores = {"positive": 0, "negative": 0, "excited": 0, "neutral": 1}
        
        for emotion, patterns in self.emotion_patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, text)
                scores[emotion] += len(matches) * 0.3
        
        # 归一化
        total = sum(scores.values())
        if total > 0:
            for k in scores:
                scores[k] = round(scores[k] / total, 3)
        
        # 确定主导情绪
        dominant = max(scores, key=scores.get)
        scores["dominant"] = dominant
        
        return scores
    
    def _update_emotional_state(self, emotion: dict, now: datetime):
        """更新情感状态"""
        state = self.profile.emotional_state
        
        # 更新主导情绪
        state["dominant_emotion"] = emotion.get("dominant", "neutral")
        
        # 更新近期情感倾向
        pos = emotion.get("positive", 0) + emotion.get("excited", 0) * 0.5
        neg = emotion.get("negative", 0)
        sentiment = 0.5 + (pos - neg) * 0.5
        # 指数移动平均
        alpha = 0.3
        state["recent_sentiment"] = round(
            alpha * sentiment + (1 - alpha) * state.get("recent_sentiment", 0.5), 3
        )
        
        # 记录情绪历史（保留最近 100 条）
        state.setdefault("mood_history", []).append({
            "emotion": emotion.get("dominant", "neutral"),
            "sentiment": sentiment,
            "time": now.isoformat(),
        })
        if len(state["mood_history"]) > 100:
            state["mood_history"] = state["mood_history"][-100:]
    
    def _analyze_communication_style(self, text: str) -> dict:
        """分析沟通风格"""
        style = {}
        
        # 正式程度
        formal_count = sum(1 for w in self.style_indicators["formal"] if w in text)
        casual_count = sum(1 for w in self.style_indicators["casual"] if w in text)
        total_indicators = formal_count + casual_count
        style["formality"] = formal_count / max(total_indicators, 1)
        
        # 详细程度
        style["verbosity"] = min(len(text) / 100, 1.0)
        
        # Emoji 使用
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE
        )
        emoji_count = len(emoji_pattern.findall(text))
        style["emoji_usage"] = min(emoji_count / 5, 1.0)
        
        # 提问频率
        question_count = len(re.findall(r"[？?]", text))
        style["question_rate"] = min(question_count / 3, 1.0)
        
        return style
    
    def _update_communication_style(self, style: dict):
        """更新沟通风格（指数移动平均）"""
        alpha = 0.2  # 学习率
        current = self.profile.communication_style
        for key, value in style.items():
            if key in current:
                current[key] = round(alpha * value + (1 - alpha) * current[key], 3)
    
    def _extract_topics(self, text: str) -> list:
        """提取话题"""
        found_topics = []
        for topic, keywords in self.topic_keywords.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                found_topics.append((topic, score))
        
        # 按匹配度排序
        found_topics.sort(key=lambda x: x[1], reverse=True)
        return [t[0] for t in found_topics[:3]]
    
    def _update_topic_preferences(self, topics: list):
        """更新话题偏好"""
        prefs = self.profile.preferences.setdefault("topics", {})
        for topic in topics:
            prefs[topic] = prefs.get(topic, 0) + 1
    
    def _extract_knowledge(self, text: str) -> list:
        """提取知识节点"""
        items = []
        
        # 偏好表达
        like_pattern = re.findall(r"(?:我喜欢|我爱|我偏好|我更喜欢|我最爱)\s*(.{1,20})", text)
        for match in like_pattern:
            items.append({
                "content": f"喜欢: {match.strip()}",
                "category": "preference",
                "weight": 0.8,
            })
        
        dislike_pattern = re.findall(r"(?:我不喜欢|我讨厌|我不爱|我受不了)\s*(.{1,20})", text)
        for match in dislike_pattern:
            items.append({
                "content": f"不喜欢: {match.strip()}",
                "category": "preference",
                "weight": 0.8,
            })
        
        # 目标和愿望
        goal_pattern = re.findall(r"(?:我想|我希望|我打算|我计划|我的目标是)\s*(.{1,30})", text)
        for match in goal_pattern:
            items.append({
                "content": f"目标/愿望: {match.strip()}",
                "category": "goal",
                "weight": 0.7,
            })
        
        # 观点和想法
        opinion_pattern = re.findall(r"(?:我觉得|我认为|在我看来|依我看|我的看法是)\s*(.{1,30})", text)
        for match in opinion_pattern:
            items.append({
                "content": f"观点: {match.strip()}",
                "category": "opinion",
                "weight": 0.6,
            })
        
        # 日常规律
        routine_pattern = re.findall(r"(?:我每天|我经常|我通常|我一般|我习惯)\s*(.{1,20})", text)
        for match in routine_pattern:
            items.append({
                "content": f"习惯: {match.strip()}",
                "category": "routine",
                "weight": 0.7,
            })
        
        # 社交关系
        social_pattern = re.findall(r"(?:我的(?:朋友|同学|同事|家人|爸爸|妈妈|哥|姐|弟|妹|男票|女票|老公|老婆|对象))\s*(.{0,15})", text)
        for match in social_pattern:
            items.append({
                "content": f"社交: {match.strip()}",
                "category": "social",
                "weight": 0.6,
            })
        
        return items
    
    def _update_knowledge_graph(self, items: list, session_id: str = ""):
        """更新知识图谱"""
        graph = self.profile.knowledge_graph
        nodes = graph.setdefault("nodes", {})
        edges = graph.setdefault("edges", {})
        
        new_node_ids = []
        
        for item in items:
            # 生成节点 ID
            node_id = hashlib.md5(f"{item['category']}_{item['content']}".encode()).hexdigest()[:12]
            
            if node_id in nodes:
                # 更新已有节点
                nodes[node_id]["weight"] = min(nodes[node_id]["weight"] + 0.1, 1.0)
                nodes[node_id]["access_count"] = nodes[node_id].get("access_count", 0) + 1
                nodes[node_id]["last_accessed"] = datetime.now().isoformat()
            else:
                # 创建新节点
                nodes[node_id] = {
                    "content": item["content"],
                    "category": item["category"],
                    "weight": item.get("weight", 0.5),
                    "created": datetime.now().isoformat(),
                    "last_accessed": datetime.now().isoformat(),
                    "access_count": 1,
                    "session_id": session_id,
                }
            
            new_node_ids.append(node_id)
        
        # 建立节点之间的关联（同时出现在同一条消息中的知识节点相互关联）
        for i, id_a in enumerate(new_node_ids):
            for id_b in new_node_ids[i+1:]:
                edge_key = f"{min(id_a, id_b)}_{max(id_a, id_b)}"
                if edge_key in edges:
                    # 加强已有连接
                    edges[edge_key]["strength"] = min(edges[edge_key]["strength"] + 0.1, 1.0)
                    edges[edge_key]["co_occurrences"] = edges[edge_key].get("co_occurrences", 0) + 1
                else:
                    # 创建新连接
                    edges[edge_key] = {
                        "from": id_a,
                        "to": id_b,
                        "strength": 0.3,
                        "type": "co_occurrence",
                        "co_occurrences": 1,
                        "created": datetime.now().isoformat(),
                    }
    
    def _add_node(self, node_id: str, content: str, category: str, weight: float = 0.5):
        """直接添加知识节点"""
        nodes = self.profile.knowledge_graph.setdefault("nodes", {})
        if node_id in nodes:
            nodes[node_id]["weight"] = min(nodes[node_id]["weight"] + 0.05, 1.0)
            nodes[node_id]["last_accessed"] = datetime.now().isoformat()
            nodes[node_id]["access_count"] = nodes[node_id].get("access_count", 0) + 1
        else:
            nodes[node_id] = {
                "content": content,
                "category": category,
                "weight": weight,
                "created": datetime.now().isoformat(),
                "last_accessed": datetime.now().isoformat(),
                "access_count": 1,
            }
    
    def _update_activity(self, now: datetime):
        """更新活跃模式"""
        patterns = self.profile.activity_patterns
        
        # 活跃小时
        hour = str(now.hour)
        patterns.setdefault("active_hours", {})[hour] = patterns.get("active_hours", {}).get(hour, 0) + 1
        
        # 活跃星期
        day = str(now.weekday())
        patterns.setdefault("active_days", {})[day] = patterns.get("active_days", {}).get(day, 0) + 1
        
        # 消息计数
        patterns["total_messages"] = patterns.get("total_messages", 0) + 1
    
    def _analyze_response_preference(self, user_msg: str, ai_response: str):
        """分析用户对回复的偏好"""
        resp_len = len(ai_response)
        
        # 简单启发式：如果用户消息短但 AI 回复很长，用户可能偏好简短回复
        user_len = len(user_msg)
        if user_len < 10 and resp_len > 200:
            self.profile.preferences["response_length"] = "short"
        elif user_len > 50 and resp_len < 50:
            self.profile.preferences["response_length"] = "detailed"
    
    def _update_confidence(self):
        """计算学习置信度"""
        meta = self.profile.learning_meta
        nodes_count = len(self.profile.knowledge_graph.get("nodes", {}))
        messages = self.profile.activity_patterns.get("total_messages", 0)
        
        # 置信度 = min(消息数/100, 1) * 0.4 + min(知识节点数/50, 1) * 0.3 + min(会话数/20, 1) * 0.3
        msg_score = min(messages / 100, 1.0)
        node_score = min(nodes_count / 50, 1.0)
        session_score = min(meta.get("learning_sessions", 0) / 20, 1.0)
        
        meta["confidence_score"] = round(
            msg_score * 0.4 + node_score * 0.3 + session_score * 0.3, 3
        )
    
    # ===== 个性化上下文生成 =====
    
    def get_personalized_context(self, current_message: str = "", max_chars: int = 1000) -> str:
        """
        生成个性化上下文，注入到 AI 的 System Prompt 中
        
        这是学习成果的输出端口，让 AI 能"记住"并"理解"用户
        """
        profile = self.profile
        parts = []
        
        # 1. 用户基本信息
        basic_info = []
        if profile.name:
            basic_info.append(f"名字: {profile.name}")
        if profile.gender:
            basic_info.append(f"性别: {profile.gender}")
        if profile.age_range:
            basic_info.append(f"年龄: {profile.age_range}")
        if profile.location:
            basic_info.append(f"地点: {profile.location}")
        if profile.occupation:
            basic_info.append(f"职业: {profile.occupation}")
        
        if basic_info:
            parts.append("[用户基本信息]\n" + "\n".join(basic_info))
        
        # 2. 性格特征
        personality = profile.personality
        personality_desc = []
        if personality.get("extraversion", 0.5) > 0.7:
            personality_desc.append("性格外向，喜欢社交")
        elif personality.get("extraversion", 0.5) < 0.3:
            personality_desc.append("性格内向，偏好独处")
        
        if personality.get("openness", 0.5) > 0.7:
            personality_desc.append("思想开放，喜欢新事物")
        if personality.get("agreeableness", 0.5) > 0.7:
            personality_desc.append("友善温和，容易相处")
        
        if personality_desc:
            parts.append("[性格特点]\n" + "、".join(personality_desc))
        
        # 3. 沟通风格偏好
        style = profile.communication_style
        style_desc = []
        if style.get("formality", 0.5) > 0.7:
            style_desc.append("偏好正式沟通")
        elif style.get("formality", 0.5) < 0.3:
            style_desc.append("偏好轻松随意的沟通")
        
        if style.get("emoji_usage", 0.5) > 0.5:
            style_desc.append("喜欢使用表情")
        if style.get("verbosity", 0.5) > 0.7:
            style_desc.append("表达详细")
        elif style.get("verbosity", 0.5) < 0.3:
            style_desc.append("表达简洁")
        
        if style_desc:
            parts.append("[沟通风格]\n" + "、".join(style_desc))
        
        # 4. 兴趣偏好
        topics = profile.preferences.get("topics", {})
        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:5]
            topic_str = "、".join([f"{t[0]}({t[1]}次)" for t in sorted_topics])
            parts.append(f"[兴趣话题]\n{topic_str}")
        
        # 5. 偏好
        prefs = []
        resp_len = profile.preferences.get("response_length", "medium")
        if resp_len == "short":
            prefs.append("偏好简短回复")
        elif resp_len == "detailed":
            prefs.append("偏好详细回复")
        
        if prefs:
            parts.append("[回复偏好]\n" + "、".join(prefs))
        
        # 6. 情感状态
        emotion = profile.emotional_state
        if emotion.get("dominant_emotion") != "neutral":
            parts.append(f"[近期情感]\n主导情绪: {emotion.get('dominant_emotion', 'neutral')}")
        
        # 7. 知识图谱摘要（与当前消息相关的高权重节点）
        relevant_nodes = self._get_relevant_nodes(current_message, max_nodes=8)
        if relevant_nodes:
            node_str = "\n".join([f"- {n['content']} (重要度:{n['weight']:.1f})" for n in relevant_nodes])
            parts.append(f"[记忆网络中的关键信息]\n{node_str}")
        
        # 合并并截断
        context = "\n\n".join(parts)
        if len(context) > max_chars:
            context = context[:max_chars] + "..."
        
        return context
    
    def _get_relevant_nodes(self, query: str, max_nodes: int = 8) -> list:
        """从知识图谱中获取与当前查询相关的节点"""
        nodes = self.profile.knowledge_graph.get("nodes", {})
        if not nodes:
            return []
        
        # 按权重排序，同时考虑关键词匹配
        scored_nodes = []
        query_chars = set(query) if query else set()
        
        for node_id, node in nodes.items():
            content = node.get("content", "")
            weight = node.get("weight", 0)
            
            # 基础分数 = 权重 * 访问次数加成
            access_count = node.get("access_count", 1)
            score = weight * (1 + min(access_count / 10, 0.5))
            
            # 关键词匹配加分
            if query:
                content_chars = set(content)
                overlap = len(query_chars & content_chars)
                if overlap > 0:
                    score += overlap / max(len(query_chars), 1) * 0.3
            
            scored_nodes.append((node, score))
        
        # 按分数排序
        scored_nodes.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in scored_nodes[:max_nodes]]
    
    # ===== 神经网络相关方法 =====
    
    def get_network_stats(self) -> dict:
        """获取记忆神经网络统计"""
        graph = self.profile.knowledge_graph
        nodes = graph.get("nodes", {})
        edges = graph.get("edges", {})
        
        # 按类别统计
        category_counts = defaultdict(int)
        for node in nodes.values():
            category_counts[node.get("category", "unknown")] += 1
        
        # 计算网络密度
        n = len(nodes)
        max_edges = n * (n - 1) / 2 if n > 1 else 1
        density = len(edges) / max_edges if max_edges > 0 else 0
        
        # 找出最强连接
        strongest_edges = sorted(
            edges.values(), 
            key=lambda e: e.get("strength", 0), 
            reverse=True
        )[:5]
        
        # 找出中心节点（被连接最多的）
        node_degree = defaultdict(int)
        for edge in edges.values():
            node_degree[edge.get("from", "")] += 1
            node_degree[edge.get("to", "")] += 1
        
        hub_nodes = sorted(node_degree.items(), key=lambda x: x[1], reverse=True)[:5]
        hub_details = []
        for node_id, degree in hub_nodes:
            if node_id in nodes:
                hub_details.append({
                    "content": nodes[node_id].get("content", ""),
                    "degree": degree,
                    "weight": nodes[node_id].get("weight", 0),
                })
        
        return {
            "total_nodes": n,
            "total_edges": len(edges),
            "network_density": round(density, 4),
            "category_distribution": dict(category_counts),
            "strongest_connections": [
                {
                    "from": nodes.get(e.get("from", ""), {}).get("content", "?"),
                    "to": nodes.get(e.get("to", ""), {}).get("content", "?"),
                    "strength": e.get("strength", 0),
                }
                for e in strongest_edges
            ],
            "hub_nodes": hub_details,
            "confidence_score": self.profile.learning_meta.get("confidence_score", 0),
        }
    
    def get_learning_progress(self) -> dict:
        """获取学习进度"""
        profile = self.profile
        meta = profile.learning_meta
        nodes = profile.knowledge_graph.get("nodes", {})
        
        # 计算各维度的学习完成度
        completeness = {
            "basic_info": sum(1 for f in ["name", "gender", "age_range", "location", "occupation"] 
                           if getattr(profile, f, "")) / 5,
            "personality": 0.5,  # 性格分析需要更多数据
            "communication_style": min(meta.get("learning_sessions", 0) / 20, 1.0),
            "preferences": min(len(profile.preferences.get("topics", {})) / 5, 1.0),
            "knowledge_graph": min(len(nodes) / 30, 1.0),
        }
        
        overall = sum(completeness.values()) / len(completeness)
        
        return {
            "overall_progress": round(overall, 3),
            "dimensions": {k: round(v, 3) for k, v in completeness.items()},
            "total_messages_analyzed": profile.activity_patterns.get("total_messages", 0),
            "total_learning_sessions": meta.get("learning_sessions", 0),
            "knowledge_nodes": len(nodes),
            "confidence_score": meta.get("confidence_score", 0),
            "first_seen": meta.get("first_seen", ""),
            "last_active": meta.get("last_active", ""),
        }
    
    def get_user_summary(self) -> str:
        """生成用户摘要（供 AI 了解用户）"""
        profile = self.profile
        summary_parts = []
        
        # 基本信息
        info = []
        if profile.name:
            info.append(f"叫{profile.name}")
        if profile.occupation:
            info.append(f"是{profile.occupation}")
        if profile.location:
            info.append(f"在{profile.location}")
        if profile.age_range:
            info.append(f"{profile.age_range}")
        if info:
            summary_parts.append("用户" + "，".join(info))
        
        # 兴趣
        topics = profile.preferences.get("topics", {})
        if topics:
            top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:3]
            summary_parts.append(f"主要兴趣: {', '.join(t[0] for t in top_topics)}")
        
        # 沟通风格
        style = profile.communication_style
        if style.get("formality", 0.5) < 0.3:
            summary_parts.append("沟通风格轻松随意")
        elif style.get("formality", 0.5) > 0.7:
            summary_parts.append("沟通风格偏正式")
        
        return "\n".join(summary_parts) if summary_parts else "（尚在学习中...）"
    
    # ===== 持久化 =====
    
    def _load_profile(self) -> UserProfile:
        """加载用户画像"""
        if self.profile_file.exists():
            try:
                with open(self.profile_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return UserProfile.from_dict(data)
            except Exception as e:
                print(f"[UserLearning] 加载画像失败: {e}")
        return UserProfile()
    
    def reset_profile(self):
        """重置用户画像"""
        self.profile = UserProfile()
        self.save_profile()
        print("[UserLearning] 用户画像已重置")

    # ================================================================
    #  高级功能：LLM 辅助分析、记忆衰减、多用户、摘要生成、反馈修正
    # ================================================================

    # ----- 1. LLM 辅助深度分析 -----

    def build_llm_analysis_prompt(self, recent_messages: list) -> str:
        """
        构建 LLM 分析 Prompt，用于深度理解用户
        
        Args:
            recent_messages: 最近的对话列表 [{"role": "user/assistant", "content": "..."}]
        
        Returns:
            发送给 LLM 的分析请求 Prompt
        """
        # 格式化最近对话
        conversation = ""
        for msg in recent_messages[-10:]:  # 最近 10 条
            role = "用户" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")[:200]
            conversation += f"{role}: {content}\n"

        prompt = f"""你是一个用户行为分析专家。请分析以下对话，提取用户的关键特征信息。

对话记录：
{conversation}

请以 JSON 格式返回分析结果（不要包含其他文字）：
{{
    "personality_traits": {{
        "extraversion": 0.0到1.0的分数,
        "agreeableness": 0.0到1.0,
        "conscientiousness": 0.0到1.0,
        "neuroticism": 0.0到1.0,
        "openness": 0.0到1.0
    }},
    "interests": ["兴趣1", "兴趣2"],
    "communication_style": "casual/formal/mixed",
    "emotional_trend": "positive/negative/neutral/mixed",
    "key_facts": ["从对话中提取的重要事实"],
    "user_goals": ["用户提到的目标或愿望"],
    "corrections": ["用户纠正过的错误信息"]
}}

评分标准：
- extraversion: 高分=主动分享、社交话题多；低分=回答简短、话题私密
- agreeableness: 高分=友善配合、少抱怨；低分=批评多、不耐烦
- conscientiousness: 高分=有条理、提到计划；低分=随意、拖延
- neuroticism: 高分=焦虑、情绪波动大；低分=情绪稳定
- openness: 高分=聊新事物、创意话题；低分=保守、重复话题"""
        return prompt

    def apply_llm_analysis(self, analysis: dict):
        """
        应用 LLM 分析结果到用户画像
        
        Args:
            analysis: LLM 返回的分析结果字典
        """
        # 1. 更新性格特征（与现有值做加权平均）
        traits = analysis.get("personality_traits", {})
        if traits:
            alpha = 0.4  # LLM 分析权重较高
            for key, value in traits.items():
                if key in self.profile.personality and isinstance(value, (int, float)):
                    old = self.profile.personality[key]
                    self.profile.personality[key] = round(
                        alpha * max(0, min(1, value)) + (1 - alpha) * old, 3
                    )

        # 2. 添加兴趣到话题偏好
        for interest in analysis.get("interests", []):
            topics = self.profile.preferences.setdefault("topics", {})
            topics[interest] = topics.get(interest, 0) + 3  # LLM 提取的兴趣给更高权重

        # 3. 更新沟通风格
        style = analysis.get("communication_style", "")
        if style == "formal":
            self.profile.communication_style["formality"] = round(
                0.4 * 1.0 + 0.6 * self.profile.communication_style["formality"], 3
            )
        elif style == "casual":
            self.profile.communication_style["formality"] = round(
                0.4 * 0.0 + 0.6 * self.profile.communication_style["formality"], 3
            )

        # 4. 提取重要事实作为知识节点
        for fact in analysis.get("key_facts", []):
            node_id = hashlib.md5(f"llm_fact_{fact}".encode()).hexdigest()[:12]
            self._add_node(node_id, fact, "personal", weight=0.85)

        # 5. 提取用户目标
        for goal in analysis.get("user_goals", []):
            node_id = hashlib.md5(f"llm_goal_{goal}".encode()).hexdigest()[:12]
            self._add_node(node_id, f"目标: {goal}", "goal", weight=0.8)

        # 6. 处理用户纠正（反馈修正循环）
        for correction in analysis.get("corrections", []):
            self._apply_correction(correction)

        # 保存
        self.save_profile()
        print(f"[UserLearning] LLM 分析已应用: 性格更新={bool(traits)}, 兴趣={len(analysis.get('interests', []))}个")

    # ----- 2. 记忆衰减机制 -----

    def apply_memory_decay(self, days_threshold: int = 30, decay_factor: float = 0.5):
        """
        对知识节点应用时间衰减
        
        规则：
        - 超过 days_threshold 天未访问的节点，权重 *= decay_factor
        - 超过 90 天未访问的节点，权重 *= 0.1（接近遗忘）
        - 最低权重 0.05（永不完全遗忘）
        """
        now = datetime.now()
        nodes = self.profile.knowledge_graph.get("nodes", {})
        decayed_count = 0

        for node_id, node in nodes.items():
            last_accessed = node.get("last_accessed", "")
            if not last_accessed:
                continue

            try:
                last_time = datetime.fromisoformat(last_accessed)
                days_since = (now - last_time).days

                if days_since > 90:
                    # 超过 90 天：大幅衰减
                    node["weight"] = round(max(0.05, node["weight"] * 0.1), 3)
                    decayed_count += 1
                elif days_since > days_threshold:
                    # 超过阈值：正常衰减
                    node["weight"] = round(max(0.05, node["weight"] * decay_factor), 3)
                    decayed_count += 1
            except (ValueError, TypeError):
                continue

        # 衰减边的强度
        edges = self.profile.knowledge_graph.get("edges", {})
        for edge_id, edge in edges.items():
            edge["strength"] = round(max(0.05, edge["strength"] * 0.95), 3)

        if decayed_count > 0:
            self.save_profile()
            print(f"[UserLearning] 记忆衰减: {decayed_count} 个节点权重已衰减")

        return decayed_count

    # ----- 3. 多用户（设备）支持 -----

    # 知识图谱最大节点数（防止文件无限增长）
    MAX_NODES = 500
    MAX_EDGES = 2000

    def __init__(self):
        self.data_dir = BASE_DIR / "data" / "user_learning"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.data_dir / "user_profile.json"
        self.graph_file = self.data_dir / "knowledge_graph.json"
        
        # 多用户支持：{device_id: UserProfile}
        self._profiles: dict[str, UserProfile] = {}
        self._current_device: str = "default"
        
        # 加载默认用户画像
        self.profile = self._load_profile()
        
        # 模式匹配规则
        self._init_patterns()
        
        # 对话缓冲区（用于 LLM 分析）
        self._message_buffer: list = []
        self._buffer_size = 10  # 每 10 条消息触发一次 LLM 分析
        self._llm_pending: bool = False  # 是否有待处理的 LLM 分析请求

    def set_device(self, device_id: str):
        """切换当前设备/用户"""
        self.save_profile()  # 保存当前用户
        self._current_device = device_id
        if device_id in self._profiles:
            self.profile = self._profiles[device_id]
        else:
            # 尝试从文件加载
            device_file = self.data_dir / f"profile_{device_id}.json"
            if device_file.exists():
                try:
                    with open(device_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.profile = UserProfile.from_dict(data)
                except Exception:
                    self.profile = UserProfile()
            else:
                self.profile = UserProfile()
            self._profiles[device_id] = self.profile

    def save_profile(self):
        """保存用户画像到文件（支持多用户）"""
        try:
            data = self.profile.to_dict()
            # 保存默认画像
            with open(self.profile_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 如果是非默认设备，也保存到独立文件
            if self._current_device != "default":
                device_file = self.data_dir / f"profile_{self._current_device}.json"
                with open(device_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            # 保存到内存缓存
            self._profiles[self._current_device] = self.profile
        except Exception as e:
            print(f"[UserLearning] 保存画像失败: {e}")

    # ----- 4. 对话摘要自动生成 -----

    def maybe_generate_summary(self, session_id: str) -> str | None:
        """
        检查是否需要生成对话摘要
        
        当消息缓冲区满时，返回需要摘要的提示
        返回 None 表示不需要生成摘要
        """
        messages = self._message_buffer
        if len(messages) < 10:
            return None

        # 构建摘要生成 Prompt
        conversation = ""
        for msg in messages[-10:]:
            role = "用户" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")[:150]
            conversation += f"{role}: {content}\n"

        summary_prompt = f"""请用 1-2 句话总结以下对话的核心内容，重点记录用户的需求、情感和重要信息：

{conversation}

摘要要求：
- 记录用户的主要话题和需求
- 记录用户的情感状态变化
- 记录任何重要的个人信息或偏好
- 简洁明了，不超过 100 字"""

        return summary_prompt

    def add_to_buffer(self, role: str, content: str):
        """添加消息到缓冲区"""
        self._message_buffer.append({"role": role, "content": content})
        # 保持缓冲区大小
        if len(self._message_buffer) > 30:
            self._message_buffer = self._message_buffer[-20:]

    def clear_buffer(self):
        """清空消息缓冲区"""
        self._message_buffer.clear()

    # ----- 5. 用户反馈修正循环 -----

    def _apply_correction(self, correction: str):
        """
        应用用户纠正
        
        当用户说"不是XX，是YY"时，修正知识图谱中的错误节点
        """
        # 检测修正模式（必须同时匹配"错误内容"和"正确内容"）
        correction_patterns = [
            r"不是(.{1,20})，(?:是|而是)(.{1,20})",
            r"(.{1,20})不对，(?:应该是|其实是)(.{1,20})",
            r"我说的(?:不是|错了)(.{1,20})(?:，|，)(?:是)(.{1,20})",
            r"更正[：:]?\s*(.{1,20})(?:→|->|是)(.{1,20})",
        ]

        for pattern in correction_patterns:
            match = re.search(pattern, correction)
            if match:
                wrong = match.group(1).strip()
                right = match.group(2).strip()
                
                # 过滤太短或无效的匹配
                if len(wrong) < 1 or len(right) < 1:
                    continue

                # 在知识图谱中查找包含错误信息的节点
                nodes = self.profile.knowledge_graph.get("nodes", {})
                edges = self.profile.knowledge_graph.get("edges", {})
                corrected = False
                nodes_to_rekey = []
                
                for node_id, node in nodes.items():
                    if wrong in node.get("content", ""):
                        # 修正节点内容
                        new_content = node["content"].replace(wrong, right)
                        node["content"] = new_content
                        node["weight"] = min(node["weight"] + 0.2, 1.0)
                        node["last_accessed"] = datetime.now().isoformat()
                        node["corrected"] = True
                        corrected = True
                        
                        # 重新生成节点 ID（因为 ID 基于 category+content）
                        new_id = hashlib.md5(f"{node['category']}_{new_content}".encode()).hexdigest()[:12]
                        if new_id != node_id:
                            nodes_to_rekey.append((node_id, new_id))
                        
                        print(f"[UserLearning] 知识修正: '{wrong}' → '{right}'")
                
                # 重新映射节点 ID（修复修正后 ID 不匹配问题）
                for old_id, new_id in nodes_to_rekey:
                    if new_id not in nodes:
                        nodes[new_id] = nodes.pop(old_id)
                        # 更新边中的引用
                        for edge in edges.values():
                            if edge.get("from") == old_id:
                                edge["from"] = new_id
                            if edge.get("to") == old_id:
                                edge["to"] = new_id

                # 如果是个人信息修正
                if hasattr(self.profile, "name") and wrong == self.profile.name:
                    self.profile.name = right
                    corrected = True
                
                # 修正成功则清空缓冲区（避免重复触发）
                if corrected:
                    self.clear_buffer()
                break

    def detect_correction(self, user_message: str) -> bool:
        """
        检测用户消息是否包含纠正信息
        
        使用更严格的模式匹配，减少误判：
        必须同时包含"否定词"和"修正模式"（如"不是X，是Y"）
        """
        # 先用完整模式匹配，避免简单关键词误判
        correction_patterns = [
            r"不是.{1,20}[，,].{1,20}",
            r".{1,20}不对[，,].{1,20}",
            r"我说的(?:不是|错了).{1,20}",
            r"更正[：:].{1,20}",
            r"纠正[：:].{1,20}",
            r".{1,20}其实是.{1,20}",
            r".{1,20}应该是.{1,20}",
            r"我之前说错了",
            r"更正一下[：:]?.{1,20}",
            r"改一下[：:]?.{1,20}",
        ]
        return any(re.search(pattern, user_message) for pattern in correction_patterns)

    # ----- 6. 知识图谱修剪（防止无限增长） -----

    def _prune_knowledge_graph(self):
        """当知识图谱超过上限时，删除最低权重的节点"""
        nodes = self.profile.knowledge_graph.get("nodes", {})
        edges = self.profile.knowledge_graph.get("edges", {})
        
        # 修剪节点
        if len(nodes) > self.MAX_NODES:
            # 按权重排序，保留最高的 MAX_NODES 个
            sorted_nodes = sorted(nodes.items(), key=lambda x: x[1].get("weight", 0), reverse=True)
            keep_ids = set(n[0] for n in sorted_nodes[:self.MAX_NODES])
            remove_ids = set(nodes.keys()) - keep_ids
            
            for rid in remove_ids:
                del nodes[rid]
            
            # 同时删除涉及已删除节点的边
            edges_to_remove = [
                eid for eid, edge in edges.items()
                if edge.get("from") in remove_ids or edge.get("to") in remove_ids
            ]
            for eid in edges_to_remove:
                del edges[eid]
            
            print(f"[UserLearning] 知识图谱修剪: 删除 {len(remove_ids)} 个节点, {len(edges_to_remove)} 条边")
        
        # 修剪边
        if len(edges) > self.MAX_EDGES:
            sorted_edges = sorted(edges.items(), key=lambda x: x[1].get("strength", 0), reverse=True)
            keep_edges = dict(sorted_edges[:self.MAX_EDGES])
            removed = len(edges) - len(keep_edges)
            self.profile.knowledge_graph["edges"] = keep_edges
            print(f"[UserLearning] 边修剪: 删除 {removed} 条弱连接")

    # ----- 7. 增强的 learn_from_message（修复所有缺陷） -----

    def learn_from_message(self, user_message: str, ai_response: str = "", session_id: str = ""):
        """
        从一条对话中学习（增强版 v2）
        
        修复：
        - LLM 触发后立即清空缓冲区，避免重复触发
        - 每个分析步骤独立 try-except，单步失败不影响整体
        - 知识图谱自动修剪
        """
        now = datetime.now()
        result = {
            "needs_llm_analysis": False,
            "llm_prompt": None,
            "needs_summary": False,
            "summary_prompt": None,
            "detected_correction": False,
        }
        
        # 添加到消息缓冲区
        self.add_to_buffer("user", user_message)
        if ai_response:
            self.add_to_buffer("assistant", ai_response)

        # 检测用户纠正（使用严格模式，减少误判）
        try:
            if self.detect_correction(user_message):
                self._apply_correction(user_message)
                result["detected_correction"] = True
        except Exception as e:
            print(f"[UserLearning] 纠正检测异常: {e}")

        # 规则引擎分析（每步独立 try-except）
        for step_name, step_fn in [
            ("活跃模式", lambda: self._update_activity(now)),
            ("个人信息", lambda: self._update_personal_info(self._extract_personal_info(user_message))),
            ("情感分析", lambda: self._update_emotional_state(self._analyze_emotion(user_message), now)),
            ("沟通风格", lambda: self._update_communication_style(self._analyze_communication_style(user_message))),
            ("话题提取", lambda: self._update_topic_preferences(self._extract_topics(user_message))),
            ("知识图谱", lambda: self._update_knowledge_graph(self._extract_knowledge(user_message), session_id)),
        ]:
            try:
                step_fn()
            except Exception as e:
                print(f"[UserLearning] {step_name}分析异常: {e}")

        if ai_response:
            try:
                self._analyze_response_preference(user_message, ai_response)
            except Exception as e:
                print(f"[UserLearning] 回复偏好分析异常: {e}")

        # 更新学习元数据
        self.profile.learning_meta["last_active"] = now.isoformat()
        self.profile.learning_meta["learning_sessions"] += 1
        try:
            self._update_confidence()
        except Exception:
            pass

        sessions = self.profile.learning_meta["learning_sessions"]
        
        # 每 5 次保存
        if sessions % 5 == 0:
            try:
                self.save_profile()
            except Exception as e:
                print(f"[UserLearning] 保存异常: {e}")

        # 每 50 次执行记忆衰减 + 知识图谱修剪
        if sessions % 50 == 0:
            try:
                self.apply_memory_decay()
                self._prune_knowledge_graph()
            except Exception as e:
                print(f"[UserLearning] 衰减/修剪异常: {e}")

        # 检查是否需要 LLM 深度分析（使用 _llm_pending 防止重复触发）
        if len(self._message_buffer) >= self._buffer_size and not self._llm_pending:
            self._llm_pending = True
            result["needs_llm_analysis"] = True
            result["llm_prompt"] = self.build_llm_analysis_prompt(self._buffer_snapshot())
            # 生成摘要（在清空缓冲区之前）
            summary_prompt = self.maybe_generate_summary(session_id)
            if summary_prompt:
                result["needs_summary"] = True
                result["summary_prompt"] = summary_prompt
            # 立即清空缓冲区，无论 LLM 分析是否成功都不会重复触发
            self.clear_buffer()

        return result

    def _buffer_snapshot(self) -> list:
        """获取缓冲区的快照副本（用于生成 prompt，不影响清空操作）"""
        return list(self._message_buffer)

    def on_llm_analysis_complete(self):
        """LLM 分析完成后的回调（重置 pending 标记）"""
        self._llm_pending = False


# ===== 全局实例 =====
learning_engine = UserLearningEngine()
