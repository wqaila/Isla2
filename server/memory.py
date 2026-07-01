"""
RAG 记忆系统 - 双后端可切换
支持 ChromaDB（精准语义搜索）和 TF-IDF（轻量低内存）
通过 config.py 中的 MEMORY_BACKEND 切换
"""
import json
import hashlib
import re
from datetime import datetime
from pathlib import Path
from abc import ABC, abstractmethod

from config import BASE_DIR


# ===== 抽象基类 =====

class MemoryBackend(ABC):
    """记忆后端抽象接口"""

    @abstractmethod
    def add_document(self, collection: str, doc_id: str, content: str, metadata: dict):
        pass

    @abstractmethod
    def search(self, collection: str, query: str, n_results: int = 5) -> list:
        """返回 [{"id": ..., "content": ..., "distance": ..., "metadata": ...}]"""
        pass

    @abstractmethod
    def count(self, collection: str) -> int:
        pass

    @abstractmethod
    def clear(self, collection: str):
        pass

    @abstractmethod
    def clear_all(self):
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


# ===== ChromaDB 后端（精准语义搜索） =====

class ChromaBackend(MemoryBackend):
    """ChromaDB 向量数据库后端 - 使用嵌入模型进行语义搜索"""

    def __init__(self):
        import chromadb
        from chromadb.config import Settings

        self.db_path = BASE_DIR / "data" / "memory_db"
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(anonymized_telemetry=False)
        )

        self._collections = {}
        for name in ["conversations", "facts", "summaries"]:
            self._collections[name] = self.client.get_or_create_collection(
                name=name,
                metadata={"description": f"{name} collection"}
            )

    def _get_collection(self, name: str):
        if name not in self._collections:
            self._collections[name] = self.client.get_or_create_collection(name=name)
        return self._collections[name]

    def add_document(self, collection: str, doc_id: str, content: str, metadata: dict):
        col = self._get_collection(collection)
        col.upsert(ids=[doc_id], documents=[content], metadatas=[metadata])

    def search(self, collection: str, query: str, n_results: int = 5) -> list:
        col = self._get_collection(collection)
        try:
            results = col.query(query_texts=[query], n_results=n_results)
            if not results or not results["documents"]:
                return []
            items = []
            for i, doc in enumerate(results["documents"][0]):
                items.append({
                    "id": results["ids"][0][i] if results["ids"] else "",
                    "content": doc,
                    "distance": results["distances"][0][i] if results["distances"] else 0,
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                })
            return items
        except Exception:
            return []

    def count(self, collection: str) -> int:
        return self._get_collection(collection).count()

    def clear(self, collection: str):
        try:
            self.client.delete_collection(collection)
            self._collections[collection] = self.client.get_or_create_collection(name=collection)
        except Exception:
            pass

    def clear_all(self):
        for name in ["conversations", "facts", "summaries"]:
            self.clear(name)

    def get_name(self) -> str:
        return "chromadb"


# ===== TF-IDF 后端（轻量低内存） =====

class TfidfBackend(MemoryBackend):
    """TF-IDF 后端 - 使用 scikit-learn 进行文本相似度搜索，内存占用极小"""

    def __init__(self):
        self.db_path = BASE_DIR / "data" / "memory_tfidf"
        self.db_path.mkdir(parents=True, exist_ok=True)

        # 每个集合存储为 JSON 文件
        # {collection: {doc_id: {"content": str, "metadata": dict}}}
        self._data = {}
        self._dirty = set()

        # TF-IDF 缓存：避免每次搜索重新 fit
        # {collection: {"vectorizer": ..., "tfidf_matrix": ..., "doc_ids": [...], "doc_contents": [...], "doc_count": int}}
        self._tfidf_cache: dict = {}

        for name in ["conversations", "facts", "summaries"]:
            self._load(name)

    def _file_path(self, collection: str) -> Path:
        return self.db_path / f"{collection}.json"

    def _load(self, collection: str):
        path = self._file_path(collection)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data[collection] = json.load(f)
            except Exception:
                self._data[collection] = {}
        else:
            self._data[collection] = {}

    def _save(self, collection: str):
        if collection in self._dirty:
            path = self._file_path(collection)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data.get(collection, {}), f, ensure_ascii=False, indent=1)
            self._dirty.discard(collection)

    def _invalidate_cache(self, collection: str):
        """使指定集合的 TF-IDF 缓存失效"""
        self._tfidf_cache.pop(collection, None)

    def add_document(self, collection: str, doc_id: str, content: str, metadata: dict):
        if collection not in self._data:
            self._data[collection] = {}
        self._data[collection][doc_id] = {"content": content, "metadata": metadata}
        self._dirty.add(collection)
        self._save(collection)
        self._invalidate_cache(collection)

    def search(self, collection: str, query: str, n_results: int = 5) -> list:
        docs = self._data.get(collection, {})
        if not docs:
            return []

        # TF-IDF 相似度计算
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            # 回退到简单关键词匹配
            return self._keyword_search(collection, query, n_results)

        doc_ids = list(docs.keys())
        doc_contents = [docs[did]["content"] for did in doc_ids]

        # 检查缓存是否有效（文档数量变化则需要重建）
        cache = self._tfidf_cache.get(collection)
        if cache is None or cache["doc_count"] != len(doc_ids):
            vectorizer = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(2, 4),
                max_features=5000,
            )
            tfidf_matrix = vectorizer.fit_transform(doc_contents)
            self._tfidf_cache[collection] = {
                "vectorizer": vectorizer,
                "tfidf_matrix": tfidf_matrix,
                "doc_ids": doc_ids,
                "doc_count": len(doc_ids),
            }
            cache = self._tfidf_cache[collection]
        else:
            vectorizer = cache["vectorizer"]
            tfidf_matrix = cache["tfidf_matrix"]

        # 仅对查询文本进行 transform（不重新 fit）
        query_vec = vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()

        # 按相似度排序
        indexed = sorted(enumerate(similarities), key=lambda x: x[1], reverse=True)

        results = []
        for idx, sim in indexed[:n_results]:
            if sim < 0.01:  # 过滤完全不相关的
                continue
            did = doc_ids[idx]
            results.append({
                "id": did,
                "content": docs[did]["content"],
                "distance": 1.0 - sim,  # 转换为距离（越小越相关）
                "metadata": docs[did].get("metadata", {}),
            })

        return results

    def _keyword_search(self, collection: str, query: str, n_results: int) -> list:
        """简单关键词匹配回退方案"""
        docs = self._data.get(collection, {})
        if not docs:
            return []

        # 中文分词（简单字符级）
        query_chars = set(query)
        scored = []
        for did, doc in docs.items():
            content = doc["content"]
            content_chars = set(content)
            overlap = len(query_chars & content_chars)
            if overlap > 0:
                score = overlap / max(len(query_chars), 1)
                scored.append((did, content, doc.get("metadata", {}), score))

        scored.sort(key=lambda x: x[3], reverse=True)
        results = []
        for did, content, meta, score in scored[:n_results]:
            results.append({
                "id": did,
                "content": content,
                "distance": 1.0 - score,
                "metadata": meta,
            })
        return results

    def count(self, collection: str) -> int:
        return len(self._data.get(collection, {}))

    def clear(self, collection: str):
        self._data[collection] = {}
        self._dirty.add(collection)
        self._save(collection)
        self._invalidate_cache(collection)

    def clear_all(self):
        for name in ["conversations", "facts", "summaries"]:
            self.clear(name)

    def get_name(self) -> str:
        return "tfidf"


# ===== 记忆管理器（统一接口） =====

class MemoryManager:
    """RAG 记忆管理器 - 自动选择后端"""

    def __init__(self, backend: MemoryBackend):
        self.backend = backend
        print(f"[Memory] 使用后端: {backend.get_name()}")

    def add_conversation(self, session_id: str, user_msg: str, ai_msg: str,
                         timestamp: str = None):
        ts = timestamp or datetime.now().isoformat()
        doc_id = hashlib.md5(f"{session_id}_{ts}_{user_msg[:50]}".encode()).hexdigest()
        content = f"用户: {user_msg}\n爱莉希雅: {ai_msg}"
        try:
            self.backend.add_document("conversations", doc_id, content, {
                "session_id": session_id,
                "user_msg": user_msg[:200],
                "ai_msg": ai_msg[:200],
                "timestamp": ts,
                "type": "conversation",
            })
        except Exception as e:
            print(f"[Memory] 保存对话失败: {e}")

    def add_fact(self, fact: str, source: str = "auto", session_id: str = ""):
        doc_id = hashlib.md5(f"fact_{fact}".encode()).hexdigest()
        try:
            self.backend.add_document("facts", doc_id, fact, {
                "source": source,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "type": "fact",
            })
        except Exception as e:
            print(f"[Memory] 保存事实失败: {e}")

    def add_summary(self, summary: str, session_id: str, message_range: str = ""):
        doc_id = hashlib.md5(f"summary_{session_id}_{summary[:50]}".encode()).hexdigest()
        try:
            self.backend.add_document("summaries", doc_id, summary, {
                "session_id": session_id,
                "message_range": message_range,
                "timestamp": datetime.now().isoformat(),
                "type": "summary",
            })
        except Exception as e:
            print(f"[Memory] 保存摘要失败: {e}")

    def search_memory(self, query: str, n_results: int = 5) -> list:
        results = []
        for collection in ["conversations", "facts", "summaries"]:
            try:
                items = self.backend.search(collection, query, n_results)
                for item in items:
                    item["collection"] = collection
                results.extend(items)
            except Exception as e:
                print(f"[Memory] 搜索 {collection} 失败: {e}")

        # 按距离排序
        results.sort(key=lambda x: x["distance"])
        return results[:n_results * 2]

    def get_memory_context(self, query: str, max_chars: int = 800) -> str:
        results = self.search_memory(query, n_results=3)
        if not results:
            return ""

        context_parts = []
        total_len = 0

        for r in results:
            if r["distance"] > 1.5:
                continue
            content = r["content"]
            if total_len + len(content) > max_chars:
                break

            coll = r.get("collection", "")
            if coll == "facts":
                context_parts.append(f"[记忆] {content}")
            elif coll == "conversations":
                short = content[:150] + "..." if len(content) > 150 else content
                context_parts.append(f"[历史对话] {short}")
            elif coll == "summaries":
                context_parts.append(f"[对话摘要] {content}")
            total_len += len(content)

        return "\n".join(context_parts) if context_parts else ""

    def extract_and_save_facts(self, user_msg: str, ai_msg: str, session_id: str = ""):
        """从用户消息和AI回复中提取关键事实（v2: 更丰富的模式 + 双向提取 + 去重）"""
        facts = []
        seen_hashes = set()
        
        # 扩展的用户事实提取模式
        patterns = {
            # 身份与称呼
            "我名字叫": "姓名", "我叫": "姓名", "我是": "身份", "我是学生": "身份",
            "我的名字": "姓名", "可以叫我": "称呼", "喊我": "称呼",
            "我叫作": "姓名",
            # 偏好与爱好
            "我喜欢": "偏好", "我爱好": "爱好", "我的爱好": "爱好", "我的兴趣": "兴趣",
            "我最喜欢": "偏好", "我特别喜欢": "偏好", "我热爱": "偏好",
            "我讨厌": "不喜欢", "我不喜欢": "不喜欢", "我烦": "不喜欢",
            # 个人信息
            "我住在": "位置", "我家在": "位置", "我在": "位置",
            "我做": "工作", "我的工作": "工作", "我从事": "工作", "我是医生": "职业",
            "我今年": "年龄", "我生日": "生日", "我的生日": "生日",
            "我属": "生肖", "我星座": "星座",
            # 愿望与目标
            "我想": "愿望", "我希望": "愿望", "我的梦想": "梦想",
            "我想学": "目标", "我计划": "计划",
            # 宠物与家人
            "我养了": "宠物", "我家有": "家庭", "我和": "关系",
        }
        
        # 排除词列表（防止误匹配）
        exclude_after = {
            "我在": set("想做说看写读唱跑走打吃喝睡来去有没和给让找问听告诉"),
            "我是": set("她他它这那很真太不有没来去想"),
            "我想": set("一下下要"),
            "我和": set("你也"),
        }
        
        for keyword, category in patterns.items():
            if keyword not in user_msg:
                continue
            
            # 找所有匹配位置（可能多次出现）
            positions = []
            pos = 0
            while True:
                idx = user_msg.find(keyword, pos)
                if idx == -1:
                    break
                positions.append(idx)
                pos = idx + 1
            
            for idx in positions:
                # 提取上下文
                context = user_msg[max(0, idx - 10):min(len(user_msg), idx + 50)].strip()
                
                # 排除误匹配
                if keyword in exclude_after and len(user_msg) > idx + len(keyword):
                    after_char = user_msg[idx + len(keyword):idx + len(keyword) + 1]
                    if after_char in exclude_after[keyword]:
                        continue
                
                fact = f"[{category}] {context}"
                fact_hash = hashlib.md5(fact.encode()).hexdigest()
                if fact_hash in seen_hashes:
                    continue
                seen_hashes.add(fact_hash)
                
                facts.append(fact)
                self.add_fact(fact, source="auto_extract", session_id=session_id)
        
        # 从 AI 回复中提取对用户的理解（如果 AI 回复中包含了总结性内容）
        if ai_msg and len(ai_msg) > 20:
            ai_facts = self._extract_facts_from_ai_reply(ai_msg)
            for fact in ai_facts:
                fact_hash = hashlib.md5(fact.encode()).hexdigest()
                if fact_hash not in seen_hashes:
                    seen_hashes.add(fact_hash)
                    facts.append(fact)
                    self.add_fact(fact, source="ai_inferred", session_id=session_id)
        
        return facts
    
    def _extract_facts_from_ai_reply(self, ai_msg: str) -> list:
        """从 AI 回复中提取可能包含用户信息的总结性语句"""
        facts = []
        # 匹配模式："舰长喜欢..."、"舰长在..."、"舰长想..." 等
        patterns = [
            (r"舰长(?:你|他|她)?喜欢([^，。！？\n]{2,20})", "偏好"),
            (r"舰长(?:你|他|她)?在([^，。！？\n]{2,20})", "信息"),
            (r"舰长(?:你|他|她)?想([^，。！？\n]{2,20})", "愿望"),
            (r"舰长(?:你|他|她)?是([^，。！？\n]{2,20})", "身份"),
        ]
        for pattern, category in patterns:
            matches = re.findall(pattern, ai_msg)
            for match in matches:
                if len(match.strip()) >= 2:
                    facts.append(f"[AI推断-{category}] 舰长{match.strip()}")
        return facts[:2]  # 最多提取2条

    def get_stats(self) -> dict:
        try:
            conv_count = self.backend.count("conversations")  # BUG #14 FIX: 避免重复调用
            facts_count = self.backend.count("facts")
            summaries_count = self.backend.count("summaries")
            return {
                "backend": self.backend.get_name(),
                "conversations": conv_count,
                "facts": facts_count,
                "summaries": summaries_count,
                "total": conv_count + facts_count + summaries_count,
            }
        except Exception:
            return {"backend": "unknown", "conversations": 0, "facts": 0, "summaries": 0, "total": 0}

    def clear_all(self):
        self.backend.clear_all()

    def clear_collection(self, collection: str):
        self.backend.clear(collection)

    def cleanup_old_entries(self, max_conversations: int = 5000, max_facts: int = 2000, max_summaries: int = 500):
        """清理过期记忆条目，防止无限增长"""
        cleaned = {"conversations": 0, "facts": 0, "summaries": 0}
        
        for collection, max_count in [
            ("conversations", max_conversations),
            ("facts", max_facts),
            ("summaries", max_summaries),
        ]:
            try:
                count = self.backend.count(collection)
                if count > max_count:
                    # 搜索所有条目，按时间排序删除最旧的
                    excess = count - max_count
                    # 通过搜索空字符串获取所有条目（按默认顺序）
                    all_items = self.backend.search(collection, "", n_results=count)
                    # 按时间戳排序，删除最旧的
                    all_items.sort(key=lambda x: x.get("metadata", {}).get("timestamp", ""))
                    # 通过重建集合实现批量删除（TF-IDF 后端不支持单条删除）
                    keep_items = all_items[excess:]
                    self.backend.clear(collection)
                    for item in keep_items:
                        self.backend.add_document(
                            collection,
                            item["id"],
                            item["content"],
                            item.get("metadata", {})
                        )
                    cleaned[collection] = min(excess, count)
            except Exception as e:
                print(f"[Memory] 清理 {collection} 失败: {e}")
        
        return cleaned

    def auto_cleanup(self):
        """自动清理：当条目超过阈值时清理最旧的记忆"""
        try:
            from config_persistence import get_value
            max_conv = get_value("memory_max_conversations", 5000)
            max_facts = get_value("memory_max_facts", 2000)
            max_summ = get_value("memory_max_summaries", 500)
        except ImportError:
            max_conv, max_facts, max_summ = 5000, 2000, 500
        
        # 检查是否需要清理
        for collection, max_count in [
            ("conversations", max_conv),
            ("facts", max_facts),
            ("summaries", max_summ),
        ]:
            try:
                count = self.backend.count(collection)
                if count > max_count * 1.2:  # 超过 20% 时触发清理
                    print(f"[Memory] {collection} 条目数 ({count}) 超过阈值 ({max_count})，开始清理...")
                    self._trim_collection(collection, max_count)
            except Exception as e:
                print(f"[Memory] 自动清理 {collection} 失败: {e}")

    def _trim_collection(self, collection: str, target_count: int):
        """裁剪集合到目标数量（保留最新的）"""
        try:
            all_items = self.backend.search(collection, "", n_results=self.backend.count(collection))
            if len(all_items) <= target_count:
                return
            
            # 按时间排序，保留最新的
            all_items.sort(key=lambda x: x.get("metadata", {}).get("timestamp", ""), reverse=True)
            keep_items = all_items[:target_count]
            
            # 重建集合：清空后重新添加保留的条目
            self.backend.clear(collection)
            for item in keep_items:
                self.backend.add_document(
                    collection, 
                    item["id"], 
                    item["content"], 
                    item.get("metadata", {})
                )
            print(f"[Memory] {collection} 已裁剪到 {len(keep_items)} 条")
        except Exception as e:
            print(f"[Memory] 裁剪 {collection} 失败: {e}")


# ===== 初始化 =====

def create_memory_manager(backend_name: str = None) -> MemoryManager:
    """根据配置创建记忆管理器"""
    if backend_name is None:
        try:
            from config import MEMORY_BACKEND
            backend_name = MEMORY_BACKEND
        except ImportError:
            backend_name = "tfidf"

    if backend_name == "chromadb":
        try:
            backend = ChromaBackend()
            return MemoryManager(backend)
        except ImportError:
            print("[Memory] ChromaDB 未安装，回退到 TF-IDF")
        except Exception as e:
            print(f"[Memory] ChromaDB 初始化失败: {e}，回退到 TF-IDF")

    # 默认使用 TF-IDF
    return MemoryManager(TfidfBackend())


# 全局实例
memory_manager = create_memory_manager()
