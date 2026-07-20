"""分层长期记忆：规则提取 + 磁盘持久化（audio/backend/memory）。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 长期记忆固定落盘目录：VPet/audio/backend/memory（绝对路径，与 cwd 无关）
DEFAULT_MEMORY_DIR = (Path(__file__).resolve().parents[1] / "memory").resolve()
_DEFAULT_USER_ID = "default"


def _ts_iso(ts: float) -> str:
    """Unix 时间戳 → 本地可读 ISO 字符串（方便打开 JSON 查看）。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _ts_date(ts: float) -> str:
    """Unix 时间戳 → 本地日期 YYYY-MM-DD。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


_DATE_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}\]\s*")


def _strip_date_prefix(text: str) -> str:
    return _DATE_PREFIX_RE.sub("", (text or "").strip())


def _format_memory_line(entry: "MemoryEntry") -> str:
    """Prompt / 展示用：统一带日期，避免 content 里已有日期时重复。"""
    body = _strip_date_prefix(entry.content)
    return f"- [{_ts_date(entry.created_at)}] {body}（{entry.category}，{entry.importance.name}）"


def _parse_ts(data: dict, key: str, iso_key: str) -> float:
    """优先读 Unix 字段；缺失时尝试解析 *_iso。"""
    if key in data and data[key] is not None:
        try:
            return float(data[key])
        except (TypeError, ValueError):
            pass
    iso = data.get(iso_key)
    if isinstance(iso, str) and iso.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(iso.strip(), fmt)
                if dt.tzinfo is not None:
                    return dt.astimezone().timestamp()
                return dt.timestamp()
            except ValueError:
                continue
    return time.time()


class MemoryImportance(Enum):
    LOW = 1  # 日常琐事，约 1 天后衰减完
    MEDIUM = 2  # 个人偏好，约 3 天后衰减完
    HIGH = 3  # 重要事件，约 30衰减


@dataclass
class MemoryEntry:
    """记忆条目"""

    content: str
    importance: MemoryImportance
    category: str  # family / pet / health / work / hobby / other
    created_at: float
    last_accessed: float
    access_count: int = 0
    source_conversation_id: str = ""
    id: str = ""

    def to_dict(self) -> dict:
        """落盘字段：数值时间戳用于计算衰减，*_iso 方便人读。"""
        return {
            "id": self.id,
            "content": self.content,
            "importance": self.importance.name,
            "category": self.category,
            "created_at": self.created_at,
            "created_at_iso": _ts_iso(self.created_at),
            "last_accessed": self.last_accessed,
            "last_accessed_iso": _ts_iso(self.last_accessed),
            "access_count": self.access_count,
            "source_conversation_id": self.source_conversation_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        importance = data.get("importance", "MEDIUM")
        if isinstance(importance, int):
            importance = MemoryImportance(importance)
        else:
            importance = MemoryImportance[str(importance)]
        return cls(
            content=str(data.get("content", "")),
            importance=importance,
            category=str(data.get("category", "other")),
            created_at=_parse_ts(data, "created_at", "created_at_iso"),
            last_accessed=_parse_ts(data, "last_accessed", "last_accessed_iso"),
            access_count=int(data.get("access_count", 0)),
            source_conversation_id=str(data.get("source_conversation_id", "")),
            id=str(data.get("id", "")),
        )


class LayeredMemoryManager:
    """分层记忆管理器（内存缓存 + JSON 持久化）。

    存储布局::
        audio/backend/memory/
          default.json          # 默认用户
          {user_id}.json
          profiles.json         # 用户画像（可选）

    设计：
    - 工作记忆仍由 learn_agent.memory.Memory 负责（对话上下文）
    - 本类负责长期事实：提取 → 去重 → 衰减 → 检索 → 落盘
    """

    DECAY_RATES = {
        MemoryImportance.LOW: 1 * 86400,  # 1 天
        MemoryImportance.MEDIUM: 3 * 86400,  # 3 天
        MemoryImportance.HIGH: 30 * 86400,  # 30 
    }

    MAX_MEMORIES_PER_USER = 200
    _EXTRACT_RULES: List[tuple[str, MemoryImportance, List[str]]] = [
        ("pet", MemoryImportance.MEDIUM, ["猫", "狗", "宠物", "养了", "猫咪", "小狗"]),
        ("health", MemoryImportance.HIGH, ["生病", "住院", "手术", "不舒服", "医院", "感冒", "发烧"]),
        ("family", MemoryImportance.HIGH, ["妈妈", "爸爸", "孩子", "家人", "老公", "老婆", "父母"]),
        ("work", MemoryImportance.MEDIUM, ["上班", "加班", "老板", "同事", "考试", "作业", "deadline"]),
        ("hobby", MemoryImportance.LOW, ["喜欢", "爱好", "游戏", "动漫", "音乐", "电影"]),
    ]

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        default_user_id: str = _DEFAULT_USER_ID,
        autosave: bool = True,
    ):
        self.storage_dir = (
            Path(storage_dir).expanduser().resolve()
            if storage_dir
            else DEFAULT_MEMORY_DIR
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.default_user_id = default_user_id
        self.autosave = autosave

        self._memories: Dict[str, List[MemoryEntry]] = {}
        self._user_profiles: Dict[str, Dict[str, Any]] = {}
        self._loaded_users: set[str] = set()
        self._lock = threading.RLock()
        self._profiles_loaded = False

        logger.info("长期记忆目录: %s", self.storage_dir)
        self._load_profiles()

    # ── path helpers ────────────────────────────────────────────────────

    @staticmethod
    def _safe_user_id(user_id: str) -> str:
        cleaned = re.sub(r"[^\w\-.]", "_", (user_id or _DEFAULT_USER_ID).strip())
        return cleaned[:64] or _DEFAULT_USER_ID

    def _user_path(self, user_id: str) -> Path:
        return self.storage_dir / f"{self._safe_user_id(user_id)}.json"

    def _profiles_path(self) -> Path:
        return self.storage_dir / "profiles.json"

    # ── persistence ─────────────────────────────────────────────────────

    def _load_profiles(self) -> None:
        path = self._profiles_path()
        if not path.is_file():
            self._profiles_loaded = True
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._user_profiles = data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("加载用户画像失败: %s", exc)
        self._profiles_loaded = True

    def _save_profiles(self) -> None:
        self._atomic_write(self._profiles_path(), self._user_profiles)

    def _ensure_loaded(self, user_id: str) -> None:
        uid = self._safe_user_id(user_id)
        if uid in self._loaded_users:
            return

        path = self._user_path(uid)
        memories: List[MemoryEntry] = []
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("memories", raw) if isinstance(raw, dict) else raw
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("content"):
                            memories.append(MemoryEntry.from_dict(item))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("加载记忆失败 user=%s: %s", uid, exc)

        self._memories[uid] = memories
        self._loaded_users.add(uid)
        logger.info("已加载记忆 user=%s count=%d path=%s", uid, len(memories), path)

    def _save_user(self, user_id: str) -> None:
        uid = self._safe_user_id(user_id)
        self._ensure_loaded(uid)
        now = time.time()
        path = self._user_path(uid)
        payload = {
            "user_id": uid,
            "updated_at": now,
            "updated_at_iso": _ts_iso(now),
            "count": len(self._memories.get(uid, [])),
            "memories": [m.to_dict() for m in self._memories.get(uid, [])],
        }
        self._atomic_write(path, payload)
        logger.info("长期记忆已写入: %s (count=%d)", path, payload["count"])
        print(f"[Memory] 已保存到 {path} (共 {payload['count']} 条)")

    @staticmethod
    def _atomic_write(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    # ── store / retrieve ────────────────────────────────────────────────

    def store(
        self,
        user_id: str,
        content: str,
        importance: MemoryImportance,
        category: str,
        source_conversation_id: str = "",
    ) -> Optional[MemoryEntry]:
        """存储记忆；内容过短或重复则跳过 / 合并。"""
        content = (content or "").strip()
        if len(content) < 2:
            return None

        uid = self._safe_user_id(user_id)
        with self._lock:
            self._ensure_loaded(uid)
            memories = self._memories.setdefault(uid, [])

            # 去重：同类别 + 高度相似内容 → 刷新访问，不重复写入
            normalized = self._normalize(content)
            for existing in memories:
                if existing.category != category:
                    continue
                if self._is_similar(self._normalize(existing.content), normalized):
                    existing.last_accessed = time.time()
                    existing.access_count += 1
                    # 保留更高重要性
                    if importance.value > existing.importance.value:
                        existing.importance = importance
                        existing.content = content[:400]
                    if self.autosave:
                        self._save_user(uid)
                    return existing

            now = time.time()
            entry = MemoryEntry(
                content=content[:400],
                importance=importance,
                category=category,
                created_at=now,
                last_accessed=now,
                source_conversation_id=source_conversation_id,
                id=f"{uid}-{int(now * 1000)}-{len(memories)}",
            )
            memories.append(entry)
            self._prune_unlocked(uid)

            if self.autosave:
                self._save_user(uid)

            logger.info(
                "存储记忆: user=%s category=%s importance=%s",
                uid,
                category,
                importance.name,
            )
            return entry

    def retrieve(
        self,
        user_id: str,
        current_topic: str = "",
        limit: int = 10,
    ) -> List[MemoryEntry]:
        """检索相关记忆（按综合得分）。"""
        uid = self._safe_user_id(user_id)
        with self._lock:
            self._ensure_loaded(uid)
            all_memories = list(self._memories.get(uid, []))
            if not all_memories:
                return []

            scored = [(m, self._compute_score(m, current_topic)) for m in all_memories]
            # 完全衰减的条目排到后面；得分 > 0 优先
            scored.sort(key=lambda x: x[1], reverse=True)
            result = [m for m, score in scored if score > 0.05][:limit]
            if not result:
                result = [m for m, _ in scored[:limit]]

            now = time.time()
            for m in result:
                m.last_accessed = now
                m.access_count += 1

            if self.autosave and result:
                self._save_user(uid)

            return result

    def _compute_score(self, memory: MemoryEntry, current_topic: str) -> float:
        now = time.time()
        age_seconds = max(0.0, now - memory.created_at)
        decay_threshold = self.DECAY_RATES[memory.importance]
        time_decay = max(0.0, 1.0 - age_seconds / decay_threshold)

        importance_weight = memory.importance.value / MemoryImportance.HIGH.value
        access_bonus = min(0.2, memory.access_count * 0.05)

        relevance = 0.35
        topic = (current_topic or "").strip().lower()
        if topic:
            if memory.category and memory.category.lower() in topic:
                relevance = 1.0
            else:
                # 简单关键词重叠
                mem_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", memory.content.lower()))
                topic_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", topic))
                if mem_tokens and topic_tokens:
                    overlap = len(mem_tokens & topic_tokens) / max(1, len(topic_tokens))
                    relevance = max(relevance, min(1.0, 0.4 + overlap))

        return time_decay * importance_weight + access_bonus + relevance * 0.3

    # ── prune / dedupe helpers ──────────────────────────────────────────

    def prune(self, user_id: str) -> int:
        """清理已完全衰减或超量的记忆，返回删除条数。"""
        uid = self._safe_user_id(user_id)
        with self._lock:
            self._ensure_loaded(uid)
            before = len(self._memories.get(uid, []))
            self._prune_unlocked(uid)
            after = len(self._memories.get(uid, []))
            removed = before - after
            if removed and self.autosave:
                self._save_user(uid)
            return removed

    def _prune_unlocked(self, uid: str) -> None:
        memories = self._memories.get(uid, [])
        if not memories:
            return

        now = time.time()
        kept: List[MemoryEntry] = []
        for m in memories:
            age = now - m.created_at
            threshold = self.DECAY_RATES[m.importance]
            # HIGH 永不因时间硬删；LOW/MEDIUM 衰减完毕且长期未访问则删
            if m.importance != MemoryImportance.HIGH and age > threshold:
                if now - m.last_accessed > threshold * 0.5:
                    continue
            kept.append(m)

        if len(kept) > self.MAX_MEMORIES_PER_USER:
            # 先按得分保留 Top-N
            scored = sorted(
                kept,
                key=lambda m: self._compute_score(m, ""),
                reverse=True,
            )
            kept = scored[: self.MAX_MEMORIES_PER_USER]

        self._memories[uid] = kept

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", "", text).lower()

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        # 包含关系（短句被长句覆盖）
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) >= 6 and shorter in longer:
            return True
        return False

    # ── extract from conversation ───────────────────────────────────────

    def extract_and_store(
        self,
        user_id: str,
        conversation: List[Dict],
        source_conversation_id: str = "",
    ) -> List[str]:
        """从对话中提取关键信息并持久化。"""
        extracted: List[str] = []

        for msg in conversation:
            content = msg.get("content", "")
            role = msg.get("role", "")
            if role != "user" or not isinstance(content, str):
                continue

            for category, importance, keywords in self._EXTRACT_RULES:
                if any(kw in content for kw in keywords):
                    entry = self.store(
                        user_id,
                        content[:200],
                        importance,
                        category,
                        source_conversation_id=source_conversation_id,
                    )
                    if entry:
                        extracted.append(f"[{category}] {content[:100]}")
                    break  # 每条用户消息只归一类，避免重复刷屏

        return extracted

    def extract_from_text(self, user_id: str, user_text: str) -> List[str]:
        """便捷：从单条用户文本提取记忆。"""
        return self.extract_and_store(
            user_id,
            [{"role": "user", "content": user_text}],
        )

    @staticmethod
    def normalize_voice_transcript(transcript: str) -> str:
        """清洗语音识别文本：去首尾空白、压缩空白、去掉常见 ASR 噪声标记。"""
        if not transcript or not isinstance(transcript, str):
            return ""
        text = transcript.strip()
        text = re.sub(r"\s+", " ", text)
        # 去掉方括号/尖括号时间戳或噪声标签
        text = re.sub(r"[\[\<][^\]\>]*[\]\>]", "", text)
        text = text.strip(" \t\"'“”‘’。．.…")
        return text.strip()

    def _classify_utterance(
        self, text: str
    ) -> tuple[str, MemoryImportance]:
        for category, importance, keywords in self._EXTRACT_RULES:
            if any(kw in text for kw in keywords):
                return category, importance
        return "other", MemoryImportance.MEDIUM

    def remember_voice_utterance(
        self,
        user_id: str,
        transcript: str,
        *,
        source_conversation_id: str = "voice-asr",
    ) -> Optional[MemoryEntry]:
        """
        仅记录语音识别得到的用户原话（已格式化）。
        不记录助手回复、情绪陪伴台词、HTTP 调试输入。
        """
        text = self.normalize_voice_transcript(transcript)
        if not text:
            return None

        # 过滤过短语气词
        fillers = {"嗯", "啊", "哦", "呃", "唔", "嗯嗯", "啊啊", "哦哦", "哈", "呵"}
        if text in fillers:
            return None
        if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text)) < 2:
            return None

        category, importance = self._classify_utterance(text)
        # 统一格式：带日期，便于 prompt / JSON 按时间阅读
        content = f"[{_ts_date(time.time())}] 用户说：{text}"
        return self.store(
            user_id,
            content,
            importance,
            category,
            source_conversation_id=source_conversation_id,
        )

    # ── profile / prompt helpers ────────────────────────────────────────

    def update_profile(self, user_id: str, **fields: Any) -> None:
        uid = self._safe_user_id(user_id)
        with self._lock:
            profile = self._user_profiles.setdefault(uid, {})
            profile.update(fields)
            profile["updated_at"] = time.time()
            self._save_profiles()

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        uid = self._safe_user_id(user_id)
        with self._lock:
            return dict(self._user_profiles.get(uid, {}))

    def format_for_prompt(self, user_id: str, topic: str = "", limit: int = 5) -> str:
        memories = self.retrieve(user_id, current_topic=topic, limit=limit)
        if not memories:
            return "暂无历史信息"
        return "\n".join(_format_memory_line(m) for m in memories)

    def list_users(self) -> List[str]:
        users = {p.stem for p in self.storage_dir.glob("*.json") if p.name != "profiles.json"}
        users.update(self._loaded_users)
        return sorted(users)


@dataclass
class PersonaConfig:
    """AI 人设配置"""

    name: str = "小暖"
    personality: str = "温柔、善解人意、偶尔幽默，像桌角小宠物"
    speaking_style: str = (
        "只用第一人称当面说话；一次尽量 1～3 句、偏短口语；"
        "禁止第三人称旁白、禁止描写自己的表情/动作/眼神/声音比喻、禁止小说体"
    )
    boundaries: str = "不提供医疗诊断，不替代专业心理咨询，遇到危机情况建议拨打热线"


class PersonaAwareResponder:
    """人设感知的回复生成器"""

    def build_system_prompt(
        self,
        persona: PersonaConfig,
        memories: List[MemoryEntry] | None = None,
        memory_text: str | None = None,
        tools_prompt: str = "",
    ) -> str:
        if memory_text is None:
            memories = memories or []
            memory_text = "\n".join(_format_memory_line(m) for m in memories[:5])

        tools_block = ""
        if tools_prompt and tools_prompt.strip():
            tools_block = f"\n【工具能力】\n{tools_prompt.strip()}\n"

        return (
            f"你是{persona.name}，用户电脑上的陪伴桌宠，性格{persona.personality}。\n"
            f"说话风格：{persona.speaking_style}\n"
            f"边界：{persona.boundaries}\n"
            f"{tools_block}\n"
            f"你了解以下关于用户的信息：\n{memory_text or '暂无历史信息'}\n\n"
            f"【输出硬规则】\n"
            f"- 直接输出要对用户说的话，不要写「{persona.name}笑了」「她轻轻…」这类旁白。\n"
            f"- 不要用引号包住整段回复，不要舞台指示。\n"
            f"- 日常闲聊保持短句；需要工具时先调用工具，最终对用户的说明也要简短。\n"
            f"如果用户提到了你已知的信息，自然地表现出你记得，但不要刻意罗列。"
            f"需要读写文件或执行命令时，优先调用工具，不要声称做不到。"
        )
