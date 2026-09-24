"""角色提示词构建。

人设本身**已经不在这个文件里了** —— 抽到了角色卡（见 `characters.py`）：

    内置卡   server/characters/*.json        随项目发布
    用户卡   server/data/characters/*.json   用户自建（不入库）

本模块只负责把「当前角色卡 + 各类上下文」组装成消息列表。

历史沿革：v3 时间感知版、v4 长度约束对齐版；
v5（本版）人设改为角色卡驱动，换角色不用再改代码。
"""
import characters
from config import runtime


def build_messages(user_message: str, history: list = None,
                   few_shot: bool = True, memory_context: str = "",
                   learning_context: str = "", emotion_context: str = "",
                   time_context: str = "", max_history: int = None,
                   card=None) -> list:
    """
    构建完整的消息列表：人设 + 时间上下文 + 记忆 + 学习上下文 + 情感指导 + Few-Shot + 历史 + 用户消息

    Args:
        user_message: 用户最新消息
        history: 历史消息列表 [{"role": "user/assistant", "content": "..."}]
        few_shot: 是否包含 Few-Shot 示例（示例会占掉约 500 token 上下文，
                  长对话场景可通过运行时配置 use_fewshot_local 关闭）
        memory_context: RAG 检索到的记忆上下文
        learning_context: 用户学习引擎的个性化上下文
        emotion_context: 情感识别引擎的情绪自适应指导
        time_context: 时间上下文感知文本 (from time_context.build_time_enhanced_context)
        max_history: 最多携带多少条历史消息（None 则读运行时配置，默认 20）
        card: 指定角色卡；默认取当前激活的角色。测试或"预演某个角色"时可直接传。

    Returns:
        完整的消息列表
    """
    if card is None:
        card = characters.active_card()

    system_content = card.system_prompt if card else ""

    # 记忆/画像注入里怎么称呼用户：跟随角色卡（爱莉希雅是「舰长」）。
    # 没设就用中性的「用户」—— 直接用「你」会让"关于你的信息"这种句子产生歧义。
    address = (card.user_address if card else "") or "用户"

    # 注入时间上下文（v3 新增，优先放在记忆之前）
    if time_context:
        system_content += f"\n\n{time_context}"

    if memory_context:
        system_content += (f"\n\n以下是你从之前的对话中记住的关于{address}的信息，在回答时请自然地融入这些记忆，"
                           f"让{address}感受到你记得他。这些只是供你参考的背景资料，不要原样复述它们：\n"
                           + memory_context)

    if learning_context:
        system_content += (f"\n\n以下是你通过长期学习了解到的{address}画像，请根据这些信息调整你的回复风格和内容，"
                           f"让回复更贴近{address}。同样只是参考，不要原样复述：\n"
                           + learning_context)

    if emotion_context:
        system_content += f"\n\n{emotion_context}"

    messages = [{"role": "system", "content": system_content}]

    if few_shot and card and card.few_shot:
        messages.extend(card.few_shot)

    if history:
        if max_history is None:
            try:
                max_history = int(runtime("max_history_messages", 20))
            except Exception:
                max_history = 20
        recent = history[-max_history:] if len(history) > max_history else history
        # 过滤掉过长的助手回复（可能是旧垃圾输出），避免污染上下文
        messages.extend(m for m in recent
                        if m["role"] == "user" or len(m["content"]) <= 200)

    messages.append({"role": "user", "content": user_message})

    return messages
