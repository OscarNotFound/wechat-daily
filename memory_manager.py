"""
memory_manager.py - 用户画像 & 持久记忆管理

负责：
  1. 加载 memory.yaml 中的用户画像
  2. 构建注入 prompt 的记忆文本
  3. 将 AI 反思结果合并到 memory.yaml
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MEMORY_FILE = Path(__file__).parent / "memory.yaml"


class MemoryManager:
    def __init__(self, memory_path: str = None):
        self.path = Path(memory_path) if memory_path else MEMORY_FILE
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return self._default()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # 确保所有字段存在
            for key, default in self._default().items():
                data.setdefault(key, default)
            return data
        except Exception as e:
            logger.warning(f"加载 memory.yaml 失败: {e}，使用默认值")
            return self._default()

    @staticmethod
    def _default() -> dict:
        return {
            "identity": {},
            "people": [],
            "groups": [],
            "recurring": [],
            "corrections": [],
            "patterns": [],
        }

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.data, f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.info(f"记忆已保存到 {self.path}")

    def build_prompt_text(self) -> str:
        """构建注入 prompt 的记忆文本"""
        parts = []

        identity = self.data.get("identity", {})
        if identity:
            info = []
            for key, label in [
                ("nickname", "昵称"),
                ("school", "学校"),
                ("major", "专业"),
                ("role", "身份"),
            ]:
                if identity.get(key):
                    info.append(f"{label}: {identity[key]}")
            if info:
                parts.append("【用户信息】\n" + "\n".join(info))

        people = self.data.get("people", [])
        if people:
            lines = []
            for p in people:
                line = f"- {p['name']}"
                if p.get("relation"):
                    line += f"（{p['relation']}）"
                if p.get("context"):
                    line += f"：{p['context']}"
                lines.append(line)
            parts.append("【常联系的人】\n" + "\n".join(lines))

        groups = self.data.get("groups", [])
        if groups:
            lines = [f"- {g['name']}：{g.get('context', '')}" for g in groups]
            parts.append("【群聊备注】\n" + "\n".join(lines))

        recurring = self.data.get("recurring", [])
        if recurring:
            lines = [f"- {r}" for r in recurring]
            parts.append("【周期性事件】\n" + "\n".join(lines))

        corrections = self.data.get("corrections", [])
        if corrections:
            lines = [f"- {c}" for c in corrections]
            parts.append("【注意事项（历史修正）】\n" + "\n".join(lines))

        patterns = self.data.get("patterns", [])
        if patterns:
            lines = [f"- {p}" for p in patterns]
            parts.append("【行为规律】\n" + "\n".join(lines))

        return "\n\n".join(parts) if parts else "（暂无历史记忆）"

    def merge_reflection(self, reflection: dict):
        """将 AI 反思结果合并到记忆中"""
        added = {"people": 0, "groups": 0, "recurring": 0, "patterns": 0, "corrections": 0}

        # 合并人物
        existing_names = {p["name"] for p in self.data.get("people", [])}
        for person in reflection.get("people", []):
            if person.get("name") and person["name"] not in existing_names:
                self.data["people"].append(person)
                existing_names.add(person["name"])
                added["people"] += 1

        # 合并群聊
        existing_groups = {g["name"] for g in self.data.get("groups", [])}
        for group in reflection.get("groups", []):
            if group.get("name") and group["name"] not in existing_groups:
                self.data["groups"].append(group)
                existing_groups.add(group["name"])
                added["groups"] += 1

        # 合并去重的字符串列表
        for key in ("recurring", "patterns", "corrections"):
            existing = set(self.data.get(key, []))
            for item in reflection.get(key, []):
                if item and item not in existing:
                    self.data[key].append(item)
                    existing.add(item)
                    added[key] += 1

        total = sum(added.values())
        if total > 0:
            details = ", ".join(f"{k}+{v}" for k, v in added.items() if v > 0)
            logger.info(f"记忆更新: {details}")
            self.save()
        else:
            logger.info("本次无新增记忆")
