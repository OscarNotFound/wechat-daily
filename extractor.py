"""
extractor.py - 微信聊天记录提取模块

从 WeChatDataAnalysis 导出的 JSON 文件中读取聊天记录。
导出目录结构：
  export/wechat_chat_export_wxid_xxx_.../
    manifest.json
    conversations/
      0001_群名_chatroom_xxx/
        meta.json
        messages.json
      0002_昵称_wxid_xxx/
        meta.json
        messages.json
"""

import os
import json
import logging
import glob
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WeChatExtractor:
    """微信聊天记录提取器（适配 WeChatDataAnalysis 导出格式）"""

    def __init__(self, config: dict):
        self.export_dir = config["wechat"].get("export_dir", "export")
        self.my_wxid = config["wechat"].get("my_wxid", "")
        self.my_nickname = config["wechat"]["my_nickname"]
        self.excluded_groups = set(config["wechat"].get("excluded_groups", []))
        self.important_groups = set(config["wechat"].get("important_groups", []))

    def extract_auto(self, target_date: Optional[datetime] = None) -> dict:
        """
        从导出目录读取聊天记录。

        Args:
            target_date: 目标日期，如果为 None 则处理所有消息

        Returns:
            标准化的聊天数据结构
        """
        if target_date is None:
            target_date = datetime.now()

        date_str = target_date.strftime("%Y-%m-%d")
        logger.info(f"开始提取 {date_str} 的聊天记录...")

        # 查找最新的导出目录
        export_base = self._find_export_dir()
        if not export_base:
            raise FileNotFoundError(
                f"在 {self.export_dir} 下找不到导出数据。\n"
                f"请先用 WeChatDataAnalysis 导出聊天记录，\n"
                f"然后把导出的文件夹解压到 {self.export_dir} 目录下。"
            )

        logger.info(f"使用导出目录: {export_base}")

        # 读取 manifest
        manifest_path = os.path.join(export_base, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            # 自动获取 wxid
            if not self.my_wxid:
                self.my_wxid = manifest.get("account", "")
                logger.info(f"自动识别 wxid: {self.my_wxid}")

        # 遍历所有对话目录
        conversations_dir = os.path.join(export_base, "conversations")
        if not os.path.exists(conversations_dir):
            raise FileNotFoundError(f"找不到 conversations 目录: {conversations_dir}")

        all_chats = []
        total_messages = 0

        for conv_dir in sorted(Path(conversations_dir).iterdir()):
            if not conv_dir.is_dir():
                continue

            try:
                chat = self._read_conversation(conv_dir, date_str)
                if chat and chat["messages"]:
                    all_chats.append(chat)
                    total_messages += len(chat["messages"])
            except Exception as e:
                logger.warning(f"读取对话 {conv_dir.name} 失败: {e}")

        result = {
            "date": date_str,
            "chats": all_chats,
        }

        logger.info(f"提取完成: {len(all_chats)} 个对话, 共 {total_messages} 条消息")
        return result

    def _find_export_dir(self) -> Optional[str]:
        """查找最新的导出目录"""
        if not os.path.exists(self.export_dir):
            return None

        # 查找 export 下的 wechat_chat_export_* 目录（排除 ZIP 文件）
        pattern = os.path.join(self.export_dir, "wechat_chat_export_*")
        export_dirs = sorted(
            [d for d in glob.glob(pattern) if os.path.isdir(d)],
            reverse=True,
        )

        if export_dirs:
            return export_dirs[0]

        # 如果 export 目录本身就包含 manifest.json
        if os.path.exists(os.path.join(self.export_dir, "manifest.json")):
            return self.export_dir

        # 递归查找
        for root, dirs, files in os.walk(self.export_dir):
            if "manifest.json" in files and "conversations" in dirs:
                return root

        return None

    def _read_conversation(self, conv_dir: Path, date_str: str) -> Optional[dict]:
        """读取单个对话"""
        meta_path = conv_dir / "meta.json"
        messages_path = conv_dir / "messages.json"

        if not messages_path.exists():
            return None

        # 读取 meta 信息
        meta = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

        chat_name = meta.get("displayName", conv_dir.name)
        is_group = meta.get("isGroup", "@chatroom" in str(conv_dir.name))
        username = meta.get("username", "")

        # 检查是否在排除列表中
        if chat_name in self.excluded_groups:
            return None

        # 读取消息
        with open(messages_path, "r", encoding="utf-8") as f:
            msg_data = json.load(f)

        # messages 可能直接是列表，也可能在 dict 的 "messages" 键下
        if isinstance(msg_data, dict):
            raw_messages = msg_data.get("messages", [])
        elif isinstance(msg_data, list):
            raw_messages = msg_data
        else:
            return None

        # 过滤和转换消息
        messages = []
        for msg in raw_messages:
            parsed = self._parse_message(msg, date_str, chat_name, is_group)
            if parsed:
                messages.append(parsed)

        if not messages:
            return None

        return {
            "chat_name": chat_name,
            "chat_type": "group" if is_group else "private",
            "is_important": chat_name in self.important_groups,
            "username": username,
            "messages": messages,
        }

    def _parse_message(self, msg: dict, date_str: str, chat_name: str, is_group: bool) -> Optional[dict]:
        """解析单条消息"""
        # 获取时间
        time_text = msg.get("createTimeText", "")
        create_time = msg.get("createTime", 0)

        # 如果有 createTimeText 直接用
        if time_text:
            msg_date = time_text[:10]  # "2026-04-01"
        elif create_time:
            msg_date = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")
            time_text = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
        else:
            return None

        # 过滤非目标日期
        if date_str and msg_date != date_str:
            return None

        # 获取消息类型
        render_type = msg.get("renderType", "")
        msg_type = msg.get("type", 0)

        # 获取内容（不同类型有不同字段）
        content = ""
        if render_type == "text":
            content = msg.get("content", "")
        elif render_type == "link":
            title = msg.get("title", "")
            url = msg.get("url", "")
            content = f"[链接] {title}" + (f" {url}" if url else "")
        elif render_type == "image":
            content = "[图片]"
        elif render_type == "video":
            content = "[视频]"
        elif render_type == "voice":
            length = msg.get("voiceLength", "")
            content = f"[语音 {length}s]" if length else "[语音]"
        elif render_type == "emoji":
            content = "[表情]"
        elif render_type == "file":
            content = f"[文件] {msg.get('title', '')}"
        elif render_type == "quote":
            content = msg.get("content", "")
            quote_content = msg.get("quoteContent", "")
            if quote_content:
                content = f"{content} (引用: {quote_content[:50]})"
        elif render_type == "redpacket":
            content = "[红包]"
        elif render_type == "transfer":
            amount = msg.get("amount", "")
            content = f"[转账 {amount}]" if amount else "[转账]"
        elif render_type == "system":
            content = msg.get("content", "[系统消息]")
        elif render_type == "voip":
            content = "[通话]"
        elif render_type == "chathistory":
            content = "[聊天记录]"
        else:
            content = msg.get("content", "")

        if not content:
            return None

        # 判断发送者
        is_sent = msg.get("isSent", False)
        sender_wxid = msg.get("senderUsername", "")

        if is_sent:
            sender = self.my_nickname
        else:
            # 私聊: 发送者就是对方
            # 群聊: 使用 senderUsername
            sender = sender_wxid if is_group else chat_name

        return {
            "sender": sender,
            "time": time_text,
            "content": content,
            "type": render_type or "text",
            "is_sent": is_sent,
        }

    def cleanup(self):
        """清理临时文件（当前模式不需要清理）"""
        pass
