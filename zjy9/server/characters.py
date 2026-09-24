"""角色卡：把「她是谁」从代码里抽出来。

在此之前人设硬编码在 `elysia_prompt.py` 里 —— 想换个角色要改代码、加个角色要发版。
现在每个角色是一张 JSON 卡：

    内置卡   server/characters/*.json        随项目发布（入库）
    用户卡   server/data/characters/*.json   用户自建（**不入库**，见 README）

同名 id 时用户卡覆盖内置卡，方便微调内置角色而不用动项目文件。

卡片字段（`id` / `name` / `system_prompt` 必填）：

    id             唯一标识，只允许小写字母、数字、下划线、连字符
    name           显示名，例如「爱莉希雅」
    full_name      全名/副标题（可选）
    source         出处，例如《崩坏3》（可选）
    description    一句话简介，给角色选择界面用（可选）
    user_address   角色怎么称呼用户，例如「舰长」（可选，供其他模块引用）
    system_prompt  人设正文（必填）
    few_shot       Few-Shot 示例，[{"role": "user"/"assistant", "content": "..."}]（可选）
    greeting       首次连接的问候语（可选，客户端可用）
    tags           标签列表（可选）
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import BASE_DIR, runtime

# 内置卡目录（入库）与用户卡目录（不入库）
BUILTIN_DIR = BASE_DIR / "characters"
USER_DIR = BASE_DIR / "data" / "characters"

# 默认角色：取不到配置或卡片时的兜底
DEFAULT_CHARACTER_ID = "elysia"

# 卡片大小上限：防止一张畸形卡片把上下文彻底撑爆
MAX_PROMPT_CHARS = 20000
MAX_FEWSHOT_ITEMS = 40
MAX_FEWSHOT_CHARS = 2000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class CharacterCard:
    id: str
    name: str
    system_prompt: str
    full_name: str = ""
    source: str = ""
    description: str = ""
    user_address: str = ""
    few_shot: list = field(default_factory=list)
    greeting: str = ""
    tags: list = field(default_factory=list)
    builtin: bool = True

    def to_summary(self) -> dict:
        """给接口/界面用的摘要（**不含** system_prompt 正文，避免列表接口过大）"""
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "source": self.source,
            "description": self.description,
            "user_address": self.user_address,
            "greeting": self.greeting,
            "tags": self.tags,
            "builtin": self.builtin,
            "prompt_chars": len(self.system_prompt),
            "few_shot_count": len(self.few_shot),
        }


class CharacterError(ValueError):
    """卡片格式不合法"""


def _validate(raw: dict, path: Path, builtin: bool) -> CharacterCard:
    if not isinstance(raw, dict):
        raise CharacterError("卡片内容必须是 JSON 对象")

    cid = str(raw.get("id") or "").strip()
    if not _ID_RE.match(cid):
        raise CharacterError(f"id 非法（只允许小写字母/数字/下划线/连字符）：{cid!r}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise CharacterError("缺少 name")

    prompt = str(raw.get("system_prompt") or "").strip()
    if not prompt:
        raise CharacterError("缺少 system_prompt")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise CharacterError(f"system_prompt 过长（>{MAX_PROMPT_CHARS} 字）")

    few_shot = []
    raw_few = raw.get("few_shot") or []
    if not isinstance(raw_few, list):
        raise CharacterError("few_shot 必须是数组")
    for item in raw_few[:MAX_FEWSHOT_ITEMS]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue        # 单条不合法就跳过，不因为一条坏数据废掉整张卡
        few_shot.append({"role": role, "content": content[:MAX_FEWSHOT_CHARS]})

    return CharacterCard(
        id=cid,
        name=name,
        system_prompt=prompt,
        full_name=str(raw.get("full_name") or "").strip(),
        source=str(raw.get("source") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        user_address=str(raw.get("user_address") or "").strip(),
        few_shot=few_shot,
        greeting=str(raw.get("greeting") or "").strip(),
        tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()][:10],
        builtin=builtin,
    )


# 卡片缓存：{签名: {id: card}}。签名取两个目录里文件的最大 mtime，
# 这样加/删/改卡片能立刻生效，而每次对话又不用重新读盘 + 解析。
_cache: dict = {"sig": None, "cards": {}}


def _dir_sig() -> tuple:
    parts = []
    for d in (BUILTIN_DIR, USER_DIR):
        if not d.exists():
            parts.append((str(d), 0.0, 0))
            continue
        files = sorted(d.glob("*.json"))
        mtimes = [f.stat().st_mtime for f in files]
        parts.append((str(d), max(mtimes) if mtimes else 0.0, len(files)))
    return tuple(parts)


def load_cards(force: bool = False) -> dict:
    """加载全部卡片，返回 {id: CharacterCard}。

    坏卡片（JSON 语法错、缺必填字段）只跳过并打印原因，不影响其它卡片 ——
    用户手写卡片出错时不该让整个服务起不来。
    """
    sig = _dir_sig()
    if not force and _cache["sig"] == sig:
        return _cache["cards"]

    cards: dict = {}
    for directory, builtin in ((BUILTIN_DIR, True), (USER_DIR, False)):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                card = _validate(raw, path, builtin)
            except Exception as e:
                print(f"[Character] 跳过 {path.name}：{e}")
                continue
            # 用户卡覆盖同名内置卡
            cards[card.id] = card

    _cache["sig"] = sig
    _cache["cards"] = cards
    return cards


def list_cards() -> list:
    """按 id 排序返回全部卡片（内置在前，同类按名称）"""
    cards = list(load_cards().values())
    cards.sort(key=lambda c: (not c.builtin, c.name))
    return cards


def get_card(card_id: str) -> CharacterCard | None:
    return load_cards().get(card_id)


def active_id() -> str:
    """当前激活的角色 id（运行时配置，缺省 elysia）"""
    try:
        return str(runtime("active_character", DEFAULT_CHARACTER_ID) or "").strip() \
            or DEFAULT_CHARACTER_ID
    except Exception:
        return DEFAULT_CHARACTER_ID


def active_card() -> CharacterCard | None:
    """当前激活的角色卡。

    配置指向的卡片不存在时**不报错**，退回默认角色；默认角色也没有就取第一张。
    对话链路不能因为一张卡片没配好就整个失败。
    """
    cards = load_cards()
    if not cards:
        return None
    card = cards.get(active_id())
    if card is None:
        card = cards.get(DEFAULT_CHARACTER_ID) or next(iter(cards.values()))
    return card


def activate(card_id: str) -> bool:
    """切换当前角色；卡片不存在返回 False"""
    if card_id not in load_cards():
        return False
    from config_persistence import save_config
    save_config({"active_character": card_id})
    return True
