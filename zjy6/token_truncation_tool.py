#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token Truncation Tool for LLM API Calls

这个工具用于解决 LLM API 调用时的 token 超限问题。
当输入 token 数量超过模型上下文长度限制时，可以自动截断或处理。

使用方法：
    1. 导入工具并在 API 调用前使用
    2. 或直接运行脚本检查文本的 token 数量

示例：
    from token_truncation_tool import TokenManager
    
    manager = TokenManager(max_tokens=60000)
    truncated_text = manager.truncate_text(long_text)
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional

# 项目根目录（本脚本所在目录），用于定位本地已下载的 Qwen tokenizer
PROJECT_ROOT = Path(__file__).resolve().parent


class TokenManager:
    """
    Token 管理器 - 用于统计和截断文本的 token 数量
    
    注意：由于不同模型的 tokenizer 不同，这里提供多种估算方法：
    1. 使用 transformers 加载真实 tokenizer（最精确，需要本地已有 tokenizer 目录）
    2. 使用 tiktoken（非 Qwen 模型可近似，Qwen 不适用）
    3. 字符数估算（快速但不精确）
    """
    
    # 常见模型的 token 估算比率（字符数 / token）
    # 注意：这只是「估算」，不是精确 token 数。
    # - 英文大致 4 个字符 = 1 token
    # - 中文等 CJK 字符在 Qwen 分词下大致 1~1.5 个字符 = 1 token
    # 这里对中文取偏保守（更小）的比率，宁可高估 token 数，也不要低估导致请求超限。
    CHAR_PER_TOKEN_RATIO = {
        'qwen': 1.5,     # Qwen 对中文约 1~1.5 个字符 = 1 token
        'gpt': 4,        # GPT 系列英文约 4 个字符 = 1 token
        'claude': 4,     # Claude 约 4 个字符 = 1 token
        'default': 1.5,  # 默认按中文保守估算
    }
    
    def __init__(
        self, 
        max_tokens: int = 60000,
        model_type: str = 'qwen',
        reserve_tokens: int = 1024
    ):
        """
        初始化 Token 管理器
        
        Args:
            max_tokens: 最大 token 数量（模型上下文长度）
            model_type: 模型类型，用于估算 token 数量
            reserve_tokens: 为模型输出预留的 token 数量
        """
        self.max_tokens = max_tokens
        self.model_type = model_type
        self.reserve_tokens = reserve_tokens
        self.available_tokens = max_tokens - reserve_tokens
        
        # 尝试加载 tiktoken
        self._tiktoken = None
        self._tiktoken_encoder = None
        try:
            import tiktoken
            self._tiktoken = tiktoken
            # 尝试加载 Qwen 的 tokenizer
            try:
                self._tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
            except:
                pass
        except ImportError:
            pass
        
        # 尝试加载 transformers 的真实 tokenizer（优先级最高，最准确）。
        # 只在本地已存在 tokenizer 目录时加载，避免联网下载；加载失败则回退到估算。
        self._transformers_tokenizer = None
        if self.model_type == 'qwen':
            try:
                from transformers import AutoTokenizer
                candidates = [
                    os.environ.get('QWEN_TOKENIZER_DIR'),
                    str(PROJECT_ROOT / 'models' / 'qwen' / 'Qwen-7B-Chat-Int4'),
                ]
                for candidate in candidates:
                    if not candidate or not os.path.isdir(candidate):
                        continue
                    try:
                        self._transformers_tokenizer = AutoTokenizer.from_pretrained(
                            candidate, trust_remote_code=True
                        )
                        break
                    except Exception as e:
                        print(f"⚠️  加载本地 Qwen tokenizer 失败（{candidate}）：{e}")
            except ImportError:
                pass
    
    def estimate_token_count(self, text: str) -> int:
        """
        估算文本的 token 数量
        
        Args:
            text: 输入文本
            
        Returns:
            估算的 token 数量
        """
        if not text:
            return 0
        
        # 1) 优先使用真实 tokenizer（最准确）
        if self._transformers_tokenizer is not None:
            try:
                return len(self._transformers_tokenizer.encode(text))
            except Exception:
                pass
        
        # 2) 非 Qwen 模型可用 tiktoken(cl100k_base) 近似。
        #    注意 cl100k_base 并不是 Qwen 的分词表，对中文偏差很大，因此 Qwen 不走这条路。
        if self._tiktoken_encoder is not None and self.model_type != 'qwen':
            try:
                return len(self._tiktoken_encoder.encode(text))
            except Exception:
                pass
        
        # 3) 字符数估算（这是估算值，不是精确 token 数）
        char_count = len(text)
        ratio = self.CHAR_PER_TOKEN_RATIO.get(self.model_type, self.CHAR_PER_TOKEN_RATIO['default'])
        return int(char_count / ratio)
    
    def truncate_text(
        self, 
        text: str, 
        max_tokens: Optional[int] = None
    ) -> str:
        """
        截断文本以符合 token 限制
        
        Args:
            text: 输入文本
            max_tokens: 最大 token 数量（默认使用初始化时的值）
            
        Returns:
            截断后的文本
        """
        max_tok = max_tokens or self.available_tokens
        
        current_tokens = self.estimate_token_count(text)
        
        if current_tokens <= max_tok:
            return text
        
        # 使用二分查找找到合适的截断点
        left, right = 0, len(text)
        
        while left < right:
            mid = (left + right) // 2
            truncated = text[:mid]
            tok_count = self.estimate_token_count(truncated)
            
            if tok_count < max_tok:
                left = mid + 1
            else:
                right = mid
        
        # 返回截断后的文本，尽量在完整句子处截断
        truncated_text = text[:left]
        
        # 尝试在最近的句号、换行处截断
        for sep in ['。\n', '.\n', '。', '.', '!\n', '?\n', '!', '?', '\n', ' ']:
            idx = truncated_text.rfind(sep)
            if idx > len(truncated_text) * 0.5:  # 至少保留一半内容
                truncated_text = truncated_text[:idx + 1]
                break
        
        return truncated_text
    
    def truncate_messages(
        self, 
        messages: List[Dict[str, Any]],
        system_message: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        截断对话消息列表
        
        策略：
        1. 保留系统消息（如果有）
        2. 保留最近的对话，删除最早的对话
        3. 如果单条消息过长，截断该消息
        
        Args:
            messages: 对话消息列表
            system_message: 系统消息（可选）
            
        Returns:
            截断后的消息列表
        """
        if not messages:
            return messages
        
        result = []
        
        # 添加系统消息
        if system_message:
            result.append(system_message)
        
        # 从最近的对话开始向前收集，最后统一反转回正常时间顺序。
        # 说明：原先用 reversed(messages) + insert(固定下标) 的写法虽然结果顺序正确，
        # 但依赖 insert 下标恰好互补，非常容易看错/改错，这里改成显式反转，语义一目了然。
        kept = []
        total_tokens = self.estimate_token_count(
            result[0].get('content', '') if result else ''
        )
        
        for msg in reversed(messages):
            msg_content = msg.get('content', '')
            msg_tokens = self.estimate_token_count(msg_content)
            
            if total_tokens + msg_tokens <= self.available_tokens:
                kept.append(msg)
                total_tokens += msg_tokens
            else:
                # 尝试截断单条消息
                if msg_tokens > self.available_tokens * 0.5:
                    # 消息太长，尝试截断
                    truncated_content = self.truncate_text(
                        msg_content, 
                        self.available_tokens - total_tokens
                    )
                    if truncated_content:
                        truncated_msg = msg.copy()
                        truncated_msg['content'] = truncated_content
                        kept.append(truncated_msg)
                # 否则跳过这条消息
        
        # kept 是「从新到旧」，反转后接在系统消息之后即为正确的时间顺序
        result.extend(reversed(kept))
        return result
    
    def check_and_truncate(
        self, 
        text: str, 
        warn_if_truncated: bool = True
    ) -> tuple[str, bool]:
        """
        检查文本是否需要截断，如果需要则截断
        
        Args:
            text: 输入文本
            warn_if_truncated: 是否需要截断时打印警告
            
        Returns:
            (截断后的文本，是否需要截断)
        """
        current_tokens = self.estimate_token_count(text)
        needs_truncation = current_tokens > self.available_tokens
        
        if needs_truncation:
            if warn_if_truncated:
                print(f"⚠️  Token 超限警告：")
                print(f"   当前 token 数：{current_tokens}")
                print(f"   最大 token 数：{self.available_tokens}")
                print(f"   截断后 token 数：{self.estimate_token_count(self.truncate_text(text))}")
            
            return self.truncate_text(text), True
        
        return text, False


class ConversationHistory:
    """
    对话历史管理器 - 使用滑动窗口维护对话历史
    """
    
    def __init__(
        self, 
        max_messages: int = 10,
        token_manager: Optional[TokenManager] = None
    ):
        """
        初始化对话历史管理器
        
        Args:
            max_messages: 最大保留的对话轮数
            token_manager: Token 管理器实例
        """
        self.max_messages = max_messages
        self.token_manager = token_manager or TokenManager()
        self.messages: List[Dict[str, Any]] = []
    
    def add_message(self, role: str, content: str):
        """添加一条消息"""
        self.messages.append({
            'role': role,
            'content': content
        })
        
        # 如果消息过多，移除最早的
        if len(self.messages) > self.max_messages * 2:
            self.messages = self.messages[-self.max_messages * 2:]
    
    def get_messages(self) -> List[Dict[str, Any]]:
        """获取当前对话历史（已截断）"""
        return self.token_manager.truncate_messages(self.messages)
    
    def clear(self):
        """清空对话历史"""
        self.messages.clear()
    
    def __len__(self) -> int:
        return len(self.messages)


def truncate_for_api_call(
    prompt: str,
    max_tokens: int = 60000,
    model_type: str = 'qwen'
) -> str:
    """
    快速截断函数 - 用于 API 调用前处理
    
    Args:
        prompt: 输入提示
        max_tokens: 最大 token 数量
        model_type: 模型类型
        
    Returns:
        截断后的提示
    """
    manager = TokenManager(max_tokens=max_tokens, model_type=model_type)
    truncated, _ = manager.check_and_truncate(prompt, warn_if_truncated=True)
    return truncated


def truncate_messages_for_api(
    messages: List[Dict[str, Any]],
    max_tokens: int = 60000,
    model_type: str = 'qwen',
    system_message: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    快速截断消息列表 - 用于 API 调用前处理
    
    Args:
        messages: 消息列表
        max_tokens: 最大 token 数量
        model_type: 模型类型
        system_message: 系统消息（可选）
        
    Returns:
        截断后的消息列表
    """
    manager = TokenManager(max_tokens=max_tokens, model_type=model_type)
    return manager.truncate_messages(messages, system_message)


# ============ 使用示例 ============

if __name__ == '__main__':
    # 示例 1: 基本使用
    print("=== Token 截断工具示例 ===\n")
    
    # 创建一个较长的文本
    long_text = "这是一个测试文本。" * 10000
    
    # 创建 Token 管理器
    manager = TokenManager(max_tokens=65536, model_type='qwen', reserve_tokens=1024)
    
    # 检查并截断
    truncated, was_truncated = manager.check_and_truncate(long_text)
    
    if was_truncated:
        print(f"原始长度：{len(long_text)} 字符")
        print(f"截断后长度：{len(truncated)} 字符")
    
    print("\n=== 对话历史管理示例 ===\n")
    
    # 创建对话历史
    history = ConversationHistory(max_messages=5)
    
    # 添加一些对话
    for i in range(10):
        history.add_message('user', f'用户问题 {i}: ' + '这是一个问题。' * 100)
        history.add_message('assistant', f'助手回答 {i}: ' + '这是一个回答。' * 100)
    
    # 获取截断后的消息
    messages = history.get_messages()
    print(f"原始消息数：{len(history.messages)}")
    print(f"截断后消息数：{len(messages)}")
    
    print("\n=== 快速函数使用示例 ===\n")
    
    # 使用快速函数
    short_prompt = "你好，请帮我写一段代码。"
    result = truncate_for_api_call(short_prompt, max_tokens=65536)
    print(f"短提示无需截断：{result}")
    
    print("\n工具使用说明：")
    print("1. 在 API 调用前，使用 truncate_for_api_call() 处理输入")
    print("2. 对于多轮对话，使用 ConversationHistory 类管理历史")
    print("3. 可以根据需要调整 max_tokens 和 reserve_tokens 参数")
