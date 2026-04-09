"""
processor.py - 消息清洗与处理模块

负责：
  1. 过滤无用消息（系统消息、表情包等）
  2. 按对话分组、按重要性排序
  3. Token 控制（防止超长对话超出 AI 限制）
  4. 构建发送给 AI 的结构化文本
"""

import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 需要过滤的系统消息模式
SYSTEM_PATTERNS = [
    r"撤回了一条消息",
    r"拍了拍",
    r"加入了群聊",
    r"退出了群聊",
    r"修改群名为",
    r"邀请.*加入了群聊",
    r"你已添加了.*现在可以开始聊天了",
    r"以上是打招呼的内容",
    r"\[收到一条网页消息.*\]",
    r"<msg>.*</msg>",              # XML格式的系统消息
    r"^\[.*\]$",                   # 纯表情包 [微笑] [捂脸] 等
    r"^https?://\S+$",            # 纯链接（保留带说明的链接）
]

SYSTEM_RE = re.compile("|".join(SYSTEM_PATTERNS), re.DOTALL)

# 任务信号关键词（规则层，辅助 LLM）
TASK_SIGNAL_PATTERNS = [
    # 指派类
    r"(你|帮我|麻烦).{0,10}(一下|搞|弄|做|写|发|交|提交|整理|准备|确认|回复)",
    # 承诺类
    r"(我来|我去|我负责|包在我|我搞定|我处理|我明天|我晚点|我回头)",
    # 时间信号
    r"(截止|deadline|ddl|最晚|之前|前完成)",
    r"(明天|后天|下周|这周|周[一二三四五六日天]|月底|\d+[月号日])",
    # 提醒类
    r"(记得|别忘了|千万别忘|提醒一下|不要忘了)",
    # @某人
    r"@\S+",
]
TASK_SIGNAL_RE = re.compile("|".join(TASK_SIGNAL_PATTERNS), re.IGNORECASE)

# 已完成信号
DONE_SIGNAL_PATTERNS = [
    r"(搞定了|弄好了|做完了|交了|发了|提交了|完成了|OK了|ok了|好了已经)",
    r"(已经|已).{0,6}(发|交|做|弄|搞|提交|完成|报名|回复|接龙)",
]
DONE_SIGNAL_RE = re.compile("|".join(DONE_SIGNAL_PATTERNS))

# 第一人称替换：把他人消息中的"我"替换为发送者名，消除视角混淆
# 只替换句中独立出现的"我"，不替换"我们"
_FIRST_PERSON_RE = re.compile(r"(?<![我们])我(?!们)")


def _replace_first_person(content: str, sender_name: str) -> str:
    """把消息中的第一人称"我"替换为发送者姓名，避免 AI 视角混淆。"""
    return _FIRST_PERSON_RE.sub(sender_name, content)


class MessageProcessor:
    """消息清洗与处理器"""

    def __init__(self, config: dict):
        proc_cfg = config.get("processing", {})
        self.max_messages_per_chat = proc_cfg.get("max_messages_per_chat", 200)
        self.min_messages_threshold = proc_cfg.get("min_messages_threshold", 3)
        self.ignored_msg_types = set(proc_cfg.get("ignored_msg_types", []))
        self.my_nickname = config["wechat"]["my_nickname"]

    def process(self, raw_data: dict) -> dict:
        """
        处理原始聊天数据，返回清洗后的结构化数据。

        Args:
            raw_data: extractor 提取的原始数据

        Returns:
            处理后的数据，格式同输入但经过清洗和排序
        """
        date_str = raw_data["date"]
        chats = raw_data["chats"]

        logger.info(f"开始处理 {date_str} 的 {len(chats)} 个对话...")

        processed_chats = []
        stats = {
            "total_chats": len(chats),
            "total_messages": 0,
            "filtered_messages": 0,
            "kept_chats": 0,
            "private_chats": 0,
            "group_chats": 0,
        }

        for chat in chats:
            # 过滤消息
            filtered_msgs = self._filter_messages(chat["messages"])
            stats["total_messages"] += len(chat["messages"])
            stats["filtered_messages"] += len(chat["messages"]) - len(filtered_msgs)

            # 跳过消息太少的对话
            if len(filtered_msgs) < self.min_messages_threshold:
                continue

            # 截断过长的对话
            if len(filtered_msgs) > self.max_messages_per_chat:
                filtered_msgs = self._smart_truncate(
                    filtered_msgs, self.max_messages_per_chat
                )

            processed_chat = {
                **chat,
                "messages": filtered_msgs,
                "message_count": len(filtered_msgs),
                "my_message_count": sum(
                    1 for m in filtered_msgs if m["sender"] == self.my_nickname
                ),
                "time_range": self._get_time_range(filtered_msgs),
            }

            processed_chats.append(processed_chat)

            if chat["chat_type"] == "private":
                stats["private_chats"] += 1
            else:
                stats["group_chats"] += 1

        # 排序：重要群聊 > 私聊 > 普通群聊，每类内部按消息数降序
        processed_chats.sort(key=self._chat_sort_key, reverse=True)

        stats["kept_chats"] = len(processed_chats)
        logger.info(
            f"处理完成: 保留 {stats['kept_chats']}/{stats['total_chats']} 个对话, "
            f"过滤 {stats['filtered_messages']} 条消息"
        )

        return {
            "date": date_str,
            "chats": processed_chats,
            "stats": stats,
        }

    def _filter_messages(self, messages: list) -> list:
        """过滤无用消息"""
        result = []
        for msg in messages:
            # 跳过非文本类型
            if msg.get("type", "text") in self.ignored_msg_types:
                continue

            content = msg.get("content", "").strip()

            # 跳过空消息
            if not content:
                continue

            # 跳过系统消息
            if SYSTEM_RE.search(content):
                continue

            # 跳过纯表情（连续多个 emoji）
            if self._is_pure_emoji(content):
                continue

            result.append(msg)

        return result

    def _smart_truncate(self, messages: list, max_count: int) -> list:
        """
        智能截断：保留开头、结尾和"我"发送的消息。
        比简单的头尾截断更能保留有用信息。
        """
        if len(messages) <= max_count:
            return messages

        # 始终保留的消息：我发的消息（通常包含重要信息和承诺）
        my_messages = [m for m in messages if m["sender"] == self.my_nickname]
        other_messages = [m for m in messages if m["sender"] != self.my_nickname]

        # 如果我的消息就已经很多，只保留我的消息的一部分
        if len(my_messages) >= max_count:
            return my_messages[:max_count]

        # 保留所有我的消息，然后从其他消息中均匀采样补足
        remaining_slots = max_count - len(my_messages)
        if other_messages and remaining_slots > 0:
            step = max(1, len(other_messages) // remaining_slots)
            sampled_others = other_messages[::step][:remaining_slots]
        else:
            sampled_others = []

        # 合并并按时间排序
        result = my_messages + sampled_others
        result.sort(key=lambda m: m.get("time", ""))

        return result

    def _chat_sort_key(self, chat: dict) -> tuple:
        """对话排序权重：重要群聊 > 私聊(我参与多的) > 普通群聊"""
        is_important = 2 if chat.get("is_important") else 0
        is_private = 1 if chat["chat_type"] == "private" else 0
        my_ratio = chat.get("my_message_count", 0) / max(chat.get("message_count", 1), 1)
        msg_count = chat.get("message_count", 0)

        return (is_important, is_private, my_ratio, msg_count)

    def _get_time_range(self, messages: list) -> dict:
        """获取消息的时间范围"""
        if not messages:
            return {"start": "", "end": ""}
        times = [m.get("time", "") for m in messages if m.get("time")]
        return {"start": min(times), "end": max(times)} if times else {"start": "", "end": ""}

    @staticmethod
    def _is_pure_emoji(text: str) -> bool:
        """检查是否是纯表情符号"""
        # 移除所有 emoji 字符后检查是否为空
        emoji_pattern = re.compile(
            r"[\U0001F600-\U0001F64F"  # 表情
            r"\U0001F300-\U0001F5FF"    # 符号
            r"\U0001F680-\U0001F6FF"    # 交通
            r"\U0001F1E0-\U0001F1FF"    # 旗帜
            r"\U00002702-\U000027B0"    # 杂项
            r"\U0001f900-\U0001f9FF"    # 补充
            r"\U0000fe00-\U0000fe0f"    # 变体选择
            r"\U0000200d"               # ZWJ
            r"]+",
            flags=re.UNICODE,
        )
        stripped = emoji_pattern.sub("", text).strip()
        return len(stripped) == 0

    # ------------------------------------------------------------------
    # 构建 AI prompt 用的文本
    # ------------------------------------------------------------------
    def build_prompt_text(self, processed_data: dict) -> str:
        """
        将处理后的聊天记录构建为发送给 AI 的文本。
        格式清晰、紧凑，便于 AI 理解。
        """
        date_str = processed_data["date"]
        chats = processed_data["chats"]
        stats = processed_data.get("stats", {})

        lines = []
        lines.append(f"日期: {date_str}")
        lines.append(f"对话总数: {stats.get('kept_chats', len(chats))}")
        lines.append(f"(私聊: {stats.get('private_chats', 0)}, "
                      f"群聊: {stats.get('group_chats', 0)})")
        lines.append("")
        lines.append("=" * 50)

        for i, chat in enumerate(chats, 1):
            chat_type_label = "私聊" if chat["chat_type"] == "private" else "群聊"
            important_tag = " [重要]" if chat.get("is_important") else ""

            lines.append(f"\n--- 对话 {i}: {chat['chat_name']} ({chat_type_label}){important_tag} ---")

            time_range = chat.get("time_range", {})
            if time_range.get("start"):
                lines.append(f"时间: {time_range['start']} ~ {time_range['end']}")

            lines.append(f"消息数: {chat.get('message_count', len(chat['messages']))}")
            lines.append("")

            for msg in chat["messages"]:
                time_short = msg["time"].split(" ")[-1][:5] if " " in msg["time"] else ""
                sender = msg["sender"] or chat.get("chat_name", "对方")
                content = msg["content"].replace("\n", " ").strip()

                # 截断过长的单条消息
                if len(content) > 300:
                    content = content[:300] + "..."

                # 规则层标注：给含任务信号的消息加标记
                tags = []
                if TASK_SIGNAL_RE.search(content):
                    tags.append("⚡任务信号")
                if DONE_SIGNAL_RE.search(content):
                    tags.append("✅已完成")
                if sender == self.my_nickname:
                    tag_prefix = "[我]"
                else:
                    tag_prefix = ""
                    # 他人消息：把句中第一人称"我"替换为发送者姓名，避免 AI 视角混淆
                    content = _replace_first_person(content, sender)

                tag_str = " ".join(tags)
                if tag_str:
                    tag_str = f" ({tag_str})"

                lines.append(f"[{time_short}] {tag_prefix}{sender}: {content}{tag_str}")

            lines.append("")

        return "\n".join(lines)
