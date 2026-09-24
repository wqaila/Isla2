"""
RAG 记忆系统 - 双后端可切换
支持 ChromaDB（精准语义搜索）和 TF-IDF（轻量低内存）
通过 config.py 中的 MEMORY_BACKEND 切换
"""
import json
import hashlib
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from abc import ABC, abstractmethod

from config import BASE_DIR, runtime
from logger_service import logger


# ===== 提示词友好化工具 =====

# 历史遗留的存储格式会在内容开头带 [标签]（如 "[姓名] 舰长叫..."）。
# 这种方括号标签被注入系统提示后，模型很容易把它当成自己的输出格式照抄，
# 于是回复里就冒出 "[历史对话]" 之类的字样。这里统一剥掉。
_LEADING_TAG_RE = re.compile(r"^(?:\s*\[[^\[\]]{1,20}\]\s*)+")


def strip_leading_tag(text: str) -> str:
    """去掉文本开头的一个或多个 [标签]，用于注入提示词前做归一化。"""
    return _LEADING_TAG_RE.sub("", text).strip()


# 合法的记忆集合名（白名单）。接口层用它校验，避免任意集合名越界读写。
VALID_COLLECTIONS = ("conversations", "facts", "summaries")


# ===== 混合检索（BM25 + 向量）=====
#
# 只用 TF-IDF 余弦的问题：它衡量的是"整体像不像"，对"必须精确命中的关键词"
# （人名、专有名词、明确的词）不够敏感 —— 而记忆检索里恰恰经常是这类查询
# （问"我喜欢喝什么"要能命中写着"咖啡"的那条）。
# BM25 的词频饱和 + 文档长度归一化正好补这块。
#
# 两路结果用 RRF（Reciprocal Rank Fusion）融合：只看排名、不看原始分数，
# 因此**不需要把两种分量的量纲对齐**，比加权求和稳得多。

_LATIN_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list:
    """中英混合分词（供 BM25 使用）。

    中文没有空格，这里用「单字 + 相邻双字」：双字能抓住"咖啡""名字"这类最小的
    有意义单位，单字保证召回。英文/数字按整词切并转小写。
    """
    tokens = [w.lower() for w in _LATIN_RE.findall(text)]
    for run in _CJK_RUN_RE.findall(text):
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class _BM25:
    """极简 BM25（Okapi），纯 Python 实现，不引入新依赖。"""

    def __init__(self, tokenized_docs: list, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.n = len(tokenized_docs)
        self.doc_len = [len(d) for d in tokenized_docs]
        self.avg_len = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in tokenized_docs]

        df = Counter()
        for d in tokenized_docs:
            df.update(set(d))
        # BM25 标准 IDF 形式，+0.5 平滑以避免除零与负值
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5))
                    for t, c in df.items()}

    def scores(self, query_tokens: list) -> list:
        out = [0.0] * self.n
        avg = self.avg_len or 1.0
        for t in query_tokens:
            idf = self.idf.get(t)
            if idf is None:          # 查询里的词没在任何文档出现过
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if not f:
                    continue
                dl = self.doc_len[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / avg)
                out[i] += idf * f * (self.k1 + 1) / denom
        return out


def _rrf_rank(score_lists: list, k: int = 60) -> list:
    """把多路打分融合成一个排序。

    返回 [(文档下标, 归一化融合分)]，按融合分降序。
    RRF: score(d) = Σ 1/(k + rank_i(d)) —— 只看排名，不看分数量纲。
    归一化分 = 融合分 / 理论上限，方便对外仍以 distance = 1 - score 返回。
    """
    fused = defaultdict(float)
    for scores in score_lists:
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        for pos, idx in enumerate(order):
            if scores[idx] <= 0:
                break               # 该路判为不相关，后面只会更差
            fused[idx] += 1.0 / (k + pos + 1)
    if not fused:
        return []
    ceiling = len(score_lists) * (1.0 / (k + 1))
    return sorted(((idx, s / ceiling) for idx, s in fused.items()),
                  key=lambda x: x[1], reverse=True)


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
    def list_all(self, collection: str, limit: int = 0) -> list:
        """列出集合内的条目（不经过相似度检索）。

        ⚠️ 不要用 search(collection, "") 来代替本方法：空查询在 TF-IDF 后端
        会得到全零相似度，再被最小相似度阈值过滤掉，最终永远返回空列表。
        清理、裁剪、导出等"遍历全部条目"的场景必须走这里。
        """
        pass

    @abstractmethod
    def count(self, collection: str) -> int:
        pass

    @abstractmethod
    def delete_document(self, collection: str, doc_id: str) -> bool:
        """删除单条记忆，返回是否真的删掉了。

        为什么需要它：此前后端只有"整库 clear"，想删掉一条错记的记忆
        只能 clear + 把其余条目重新 add 回去 —— 既慢又容易在中途出错时
        把整个记忆库搞坏。管理 UI 必须要有单条删除。
        """
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

    def list_all(self, collection: str, limit: int = 0) -> list:
        """列出集合内所有条目（ChromaDB 走 get()，不做向量检索）"""
        col = self._get_collection(collection)
        try:
            kwargs = {"include": ["documents", "metadatas"]}
            if limit and limit > 0:
                kwargs["limit"] = limit
            raw = col.get(**kwargs)
        except Exception:
            return []

        ids = raw.get("ids") or []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        items = []
        for i, doc_id in enumerate(ids):
            items.append({
                "id": doc_id,
                "content": docs[i] if i < len(docs) else "",
                "distance": 0.0,
                "metadata": metas[i] if i < len(metas) else {},
            })
        return items

    def count(self, collection: str) -> int:
        return self._get_collection(collection).count()

    def delete_document(self, collection: str, doc_id: str) -> bool:
        col = self._get_collection(collection)
        try:
            existing = col.get(ids=[doc_id], include=[])
            if not (existing.get("ids") or []):
                return False
            col.delete(ids=[doc_id])
            return True
        except Exception:
            return False

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

    # JSON 落盘的最小间隔（秒）。每条消息都全量重写一次 JSON 是 O(N) 写放大，
    # 这里做节流；进程退出前必须调用 flush() 保证不丢数据。
    _SAVE_INTERVAL = 2.0

    def __init__(self):
        self.db_path = BASE_DIR / "data" / "memory_tfidf"
        self.db_path.mkdir(parents=True, exist_ok=True)

        # 每个集合存储为 JSON 文件
        # {collection: {doc_id: {"content": str, "metadata": dict}}}
        self._data = {}
        self._dirty = set()

        # 每个集合的修改版本号：任何增删改都会 +1。
        # TF-IDF 缓存记下构建时的版本号，版本一致就直接复用，避免每次搜索都重新 fit。
        self._version: dict = {}
        self._last_flush: dict = {}

        # TF-IDF 缓存
        # {collection: {"vectorizer": ..., "tfidf_matrix": ..., "doc_ids": [...], "version": int}}
        self._tfidf_cache: dict = {}

        # 并发保护：FastAPI 的事件循环是单线程的，但 learning 分析跑在
        # create_task 里，且后台线程也可能写入，这里统一加锁。
        self._lock = threading.RLock()

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
        self._version[collection] = 0

    def _save(self, collection: str):
        """立即把集合落盘"""
        with self._lock:
            path = self._file_path(collection)
            tmp_path = path.with_suffix(".json.tmp")
            # 先写临时文件再原子替换，避免写一半崩溃导致整个记忆库损坏
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data.get(collection, {}), f, ensure_ascii=False, indent=1)
            os.replace(tmp_path, path)
            self._dirty.discard(collection)
            self._last_flush[collection] = time.time()

    def _maybe_save(self, collection: str):
        """节流落盘：距上次写入不足 _SAVE_INTERVAL 就攒着，等 flush() 一起写"""
        last = self._last_flush.get(collection, 0.0)
        if time.time() - last >= self._SAVE_INTERVAL:
            self._save(collection)

    def flush(self):
        """把所有未落盘的集合写入磁盘（优雅关闭时调用）"""
        for collection in list(self._dirty):
            self._save(collection)

    def _touch(self, collection: str):
        """标记集合已变更：版本 +1 并让 TF-IDF 缓存失效"""
        self._version[collection] = self._version.get(collection, 0) + 1
        self._tfidf_cache.pop(collection, None)
        self._dirty.add(collection)

    def add_document(self, collection: str, doc_id: str, content: str, metadata: dict):
        with self._lock:
            if collection not in self._data:
                self._data[collection] = {}
            self._data[collection][doc_id] = {"content": content, "metadata": metadata}
            self._touch(collection)
            self._maybe_save(collection)

    def list_all(self, collection: str, limit: int = 0) -> list:
        """列出集合内所有条目（不走相似度检索，保证空查询也能拿到全部数据）"""
        with self._lock:
            docs = self._data.get(collection, {})
            items = [
                {
                    "id": did,
                    "content": doc.get("content", ""),
                    "distance": 0.0,
                    "metadata": doc.get("metadata", {}),
                }
                for did, doc in docs.items()
            ]
        if limit and limit > 0:
            return items[:limit]
        return items

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

        # 缓存是否有效：比较版本号（而不是文档数）。
        # 只用文档数判断会漏掉"同数量但内容被覆盖"的情况；而每次写入都无条件
        # 失效缓存又会让缓存形同虚设（每次搜索都全量重训）。
        version = self._version.get(collection, 0)
        cache = self._tfidf_cache.get(collection)
        if cache is None or cache["version"] != version:
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
                "version": version,
                # BM25 索引与向量矩阵同源，一起构建、一起失效
                "bm25": _BM25([_tokenize(c) for c in doc_contents]),
            }
            cache = self._tfidf_cache[collection]
        else:
            vectorizer = cache["vectorizer"]
            tfidf_matrix = cache["tfidf_matrix"]

        # 仅对查询文本进行 transform（不重新 fit）
        query_vec = vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, tfidf_matrix).flatten().tolist()

        # 混合检索可以用运行时配置关掉（万一新逻辑在某些数据上表现异常，
        # 能一键退回纯向量，不必改代码重发）
        try:
            hybrid = bool(runtime("memory_hybrid_search", True))
        except Exception:
            hybrid = True

        if hybrid:
            ranked = _rrf_rank([similarities, cache["bm25"].scores(_tokenize(query))])
        else:
            ranked = [(i, s) for i, s in sorted(
                enumerate(similarities), key=lambda x: x[1], reverse=True) if s > 0]

        results = []
        for idx, score in ranked[:n_results]:
            if score <= 0:          # 两路都判为不相关
                continue
            did = doc_ids[idx]
            results.append({
                "id": did,
                "content": docs[did]["content"],
                "distance": 1.0 - score,   # 转换为距离（越小越相关）
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
        with self._lock:
            return len(self._data.get(collection, {}))

    def delete_document(self, collection: str, doc_id: str) -> bool:
        with self._lock:
            docs = self._data.get(collection, {})
            if doc_id not in docs:
                return False
            del docs[doc_id]
            self._touch(collection)     # 版本 +1 并让 TF-IDF 缓存失效
            self._save(collection)      # 删除是不可逆操作，立即落盘而不是节流
            return True

    def clear(self, collection: str):
        with self._lock:
            self._data[collection] = {}
            self._touch(collection)
            self._save(collection)

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
        logger.info("memory", f"使用后端: {backend.get_name()}")

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
            logger.error("memory", f"保存对话失败: {e}")

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
            logger.error("memory", f"保存事实失败: {e}")

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
            logger.error("memory", f"保存摘要失败: {e}")

    # ===== 会话中期摘要（分层记忆的中间层）=====
    #
    # 为什么需要：对话历史只喂「最近 N 条」，聊久了早前内容就彻底丢失
    # （表现就是"她忘了我们之前说过什么"）。这里把「最近窗口之外」的旧消息
    # 压成一份摘要常驻，补上短期窗口与长期事实之间的那一层。

    def set_session_summary(self, session_id: str, summary: str,
                            covered_until: int = 0):
        """写入/覆盖某个会话的中期摘要（每个会话只保留一份，原地覆盖）。

        covered_until = 已总结到第几条消息（按时间正序计数）。下次只总结这之后
        的新增部分，避免每轮都把整段对话重新压一遍。
        """
        doc_id = hashlib.md5(f"session_summary_{session_id}".encode()).hexdigest()
        try:
            self.backend.add_document("summaries", doc_id, summary, {
                "session_id": session_id,
                "covered_until": int(covered_until),
                "timestamp": datetime.now().isoformat(),
                "type": "session_summary",
            })
        except Exception as e:
            logger.error("memory", f"保存会话摘要失败: {e}")

    def get_session_summary(self, session_id: str) -> dict | None:
        """取某个会话的中期摘要；没有则返回 None。

        summaries 集合很小（每个会话最多一条），直接遍历即可 —— 比给后端抽象
        接口加 get_document、再改两个后端实现更省事。
        """
        for item in self.backend.list_all("summaries"):
            meta = item.get("metadata") or {}
            if (meta.get("type") == "session_summary"
                    and meta.get("session_id") == session_id):
                return {
                    "content": item.get("content", ""),
                    "covered_until": int(meta.get("covered_until") or 0),
                }
        return None

    def search_memory(self, query: str, n_results: int = 5) -> list:
        results = []
        for collection in ["conversations", "facts", "summaries"]:
            try:
                items = self.backend.search(collection, query, n_results)
                for item in items:
                    item["collection"] = collection
                results.extend(items)
            except Exception as e:
                logger.error("memory", f"搜索 {collection} 失败: {e}")

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
            content = strip_leading_tag(r["content"])
            if total_len + len(content) > max_chars:
                break

            coll = r.get("collection", "")
            # 注意：这里刻意不使用 [xxx] 方括号标签。模型会把标签当成输出格式
            # 照抄（实测回复里冒出过 "[历史对话]"），改用自然语言引导句。
            if coll == "facts":
                context_parts.append(f"- 你记得关于舰长的事：{content}")
            elif coll == "conversations":
                short = content[:150] + "..." if len(content) > 150 else content
                context_parts.append(f"- 你们之前聊过的内容：{short}")
            elif coll == "summaries":
                context_parts.append(f"- 之前对话的摘要：{content}")
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

    def list_all(self, collection: str) -> list:
        """列出某集合的全部条目（供导出/清理使用）"""
        return self.backend.list_all(collection)

    def delete_entry(self, collection: str, doc_id: str) -> bool:
        """删除单条记忆。集合名做白名单校验，避免越界操作。

        返回 True 表示确实删掉了一条；False 表示集合非法或条目不存在。
        """
        if collection not in VALID_COLLECTIONS:
            return False
        if not doc_id:
            return False
        return self.backend.delete_document(collection, doc_id)

    def flush(self):
        """把待落盘的记忆写入磁盘（优雅关闭时调用）"""
        flush = getattr(self.backend, "flush", None)
        if callable(flush):
            flush()

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
                    excess = count - max_count
                    # 用 list_all 取全部条目（不能用空查询 search，见 MemoryBackend.list_all 的说明）
                    all_items = self.backend.list_all(collection)
                    if not all_items:
                        continue
                    # 按时间戳排序，删除最旧的
                    all_items.sort(key=lambda x: x.get("metadata", {}).get("timestamp", ""))
                    # TF-IDF 后端不支持单条删除，通过重建集合实现批量删除
                    keep_items = all_items[excess:]
                    self.backend.clear(collection)
                    for item in keep_items:
                        self.backend.add_document(
                            collection,
                            item["id"],
                            item["content"],
                            item.get("metadata", {})
                        )
                    self.flush()
                    cleaned[collection] = len(all_items) - len(keep_items)
            except Exception as e:
                logger.error("memory", f"清理 {collection} 失败: {e}")
        
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
                    logger.warning("memory", f"{collection} 条目数 ({count}) 超过阈值 ({max_count})，开始清理")
                    self._trim_collection(collection, max_count)
            except Exception as e:
                logger.error("memory", f"自动清理 {collection} 失败: {e}")

    def _trim_collection(self, collection: str, target_count: int):
        """裁剪集合到目标数量（保留最新的）"""
        try:
            all_items = self.backend.list_all(collection)
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
            self.flush()
            logger.info("memory", f"{collection} 已裁剪到 {len(keep_items)} 条")
        except Exception as e:
            logger.error("memory", f"裁剪 {collection} 失败: {e}")


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
            logger.warning("memory", "ChromaDB 未安装，回退到 TF-IDF")
        except Exception as e:
            logger.warning("memory", f"ChromaDB 初始化失败: {e}，回退到 TF-IDF")

    # 默认使用 TF-IDF
    return MemoryManager(TfidfBackend())


# 全局实例
memory_manager = create_memory_manager()
