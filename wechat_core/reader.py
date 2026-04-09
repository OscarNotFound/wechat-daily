"""
reader.py - 从解密后的微信 SQLite 数据库读取消息

支持：
  - 从 session.db 获取会话列表
  - 从 contact.db 获取联系人信息
  - 从 MSG*.db / message*.db 查询指定日期的消息
  - 处理 zstd 压缩内容
  - 解析群聊发送者
"""

import hashlib
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import zstandard as zstd
except ImportError:
    zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 消息基础类型
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_VIDEO = 43
MSG_TYPE_APP = 49
MSG_TYPE_SYSTEM = 10000


class WeChatReader:
    """从解密后的 SQLite 数据库直接读取微信消息"""

    def __init__(self, db_dir: str, my_wxid: str = "", my_nickname: str = ""):
        self.db_dir = Path(db_dir)
        self.my_wxid = my_wxid
        self.my_nickname = my_nickname
        self._contacts: dict = {}
        self._name2id: dict = {}

    def read_date(self, target_date: datetime, excluded_groups: set = None,
                  important_groups: set = None) -> dict:
        """读取指定日期的所有聊天记录，返回与 extractor 兼容的格式"""
        excluded_groups = excluded_groups or set()
        important_groups = important_groups or set()

        date_str = target_date.strftime("%Y-%m-%d")
        start_ts = int(target_date.replace(hour=0, minute=0, second=0).timestamp())
        end_ts = start_ts + 86400

        # 加载联系人
        self._load_contacts()

        # 获取会话列表
        sessions = self._get_sessions()
        if not sessions:
            logger.warning("未找到任何会话")
            return {"date": date_str, "chats": []}

        # 查找所有消息数据库
        msg_dbs = self._find_msg_dbs()
        if not msg_dbs:
            logger.warning("未找到消息数据库文件")
            return {"date": date_str, "chats": []}

        all_chats = []
        for username, display_name in sessions:
            is_group = "@chatroom" in username

            # 过滤排除的群
            if display_name in excluded_groups:
                continue

            # 在消息数据库中查找该会话的消息
            messages = self._query_messages(msg_dbs, username, start_ts, end_ts, is_group)
            if not messages:
                continue

            all_chats.append({
                "chat_name": display_name,
                "chat_type": "group" if is_group else "private",
                "is_important": display_name in important_groups,
                "username": username,
                "messages": messages,
            })

        logger.info(f"直接读取完成: {len(all_chats)} 个对话")
        return {"date": date_str, "chats": all_chats}

    def _load_contacts(self):
        """从 contact.db 加载联系人信息"""
        contact_db = self._find_db("contact.db")
        if not contact_db:
            logger.warning("未找到 contact.db")
            return

        try:
            conn = sqlite3.connect(str(contact_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT username, remark, nick_name, alias FROM contact"
            ).fetchall()
            for row in rows:
                uname = row["username"]
                self._contacts[uname] = {
                    "remark": row["remark"] or "",
                    "nick_name": row["nick_name"] or "",
                    "alias": row["alias"] or "",
                }
            conn.close()
            logger.info(f"加载 {len(self._contacts)} 个联系人")
        except Exception as e:
            logger.warning(f"加载联系人失败: {e}")

    def _get_sessions(self) -> List[Tuple[str, str]]:
        """从 session.db 获取会话列表，返回 [(username, display_name), ...]"""
        session_db = self._find_db("session.db")
        if not session_db:
            logger.warning("未找到 session.db")
            return []

        try:
            conn = sqlite3.connect(str(session_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT username FROM SessionTable ORDER BY sort_timestamp DESC"
            ).fetchall()
            conn.close()

            result = []
            for row in rows:
                username = row["username"]
                display = self._resolve_display_name(username)
                result.append((username, display))
            return result
        except Exception as e:
            logger.warning(f"读取会话列表失败: {e}")
            return []

    def _resolve_display_name(self, username: str) -> str:
        """解析显示名称：备注 > 昵称 > username"""
        contact = self._contacts.get(username, {})
        return contact.get("remark") or contact.get("nick_name") or username

    def _find_msg_dbs(self) -> List[Path]:
        """查找所有消息数据库文件"""
        patterns = ["message*.db", "MSG*.db", "msg*.db"]
        dbs = []
        for pat in patterns:
            dbs.extend(self.db_dir.glob(pat))
        # 去重
        seen = set()
        result = []
        for db in dbs:
            if db.name.lower() not in seen:
                seen.add(db.name.lower())
                result.append(db)
        return result

    def _find_db(self, name: str) -> Optional[Path]:
        """查找指定名称的数据库文件"""
        # 精确匹配
        p = self.db_dir / name
        if p.exists():
            return p
        # 不区分大小写
        for f in self.db_dir.iterdir():
            if f.name.lower() == name.lower() and f.is_file():
                return f
        return None

    def _query_messages(self, msg_dbs: List[Path], username: str,
                        start_ts: int, end_ts: int, is_group: bool) -> List[dict]:
        """在消息数据库中查询指定会话、指定时间范围的消息"""
        md5_hex = hashlib.md5(username.encode("utf-8")).hexdigest()
        table_candidates = [f"msg_{md5_hex}", f"chat_{md5_hex}"]

        for db_path in msg_dbs:
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row

                # 找到存在的表
                table = self._find_table(conn, table_candidates)
                if not table:
                    conn.close()
                    continue

                # 加载 Name2Id 映射（如果有）
                name2id = self._load_name2id(conn)

                # 检查有哪些列
                cols_info = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
                col_names = {c["name"].lower() for c in cols_info}
                has_compress = "compress_content" in col_names

                select_cols = "local_id, local_type, real_sender_id, create_time, message_content"
                if has_compress:
                    select_cols += ", compress_content"

                rows = conn.execute(
                    f"SELECT {select_cols} FROM [{table}] "
                    f"WHERE create_time >= ? AND create_time < ? "
                    f"ORDER BY create_time ASC",
                    (start_ts, end_ts),
                ).fetchall()

                conn.close()

                if not rows:
                    continue

                messages = []
                for row in rows:
                    parsed = self._parse_db_message(row, is_group, name2id, has_compress)
                    if parsed:
                        messages.append(parsed)
                return messages

            except Exception as e:
                logger.debug(f"查询 {db_path.name} 中 {username} 失败: {e}")
                try:
                    conn.close()
                except Exception:
                    pass

        return []

    def _find_table(self, conn: sqlite3.Connection, candidates: List[str]) -> Optional[str]:
        """检查哪个候选表名存在"""
        existing = {r[0].lower() for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for name in candidates:
            if name.lower() in existing:
                return name
        return None

    def _load_name2id(self, conn: sqlite3.Connection) -> dict:
        """加载 Name2Id 表的映射 {rowid: username}"""
        try:
            rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
            return {r["rowid"]: r["user_name"] for r in rows}
        except Exception:
            return {}

    def _parse_db_message(self, row: sqlite3.Row, is_group: bool,
                          name2id: dict, has_compress: bool) -> Optional[dict]:
        """解析数据库中的一条消息记录"""
        create_time = row["create_time"]
        local_type = row["local_type"]

        # 提取基础类型（低32位）
        base_type = local_type & 0xFFFFFFFF
        sub_type = (local_type >> 32) & 0xFFFFFFFF if local_type > 0xFFFFFFFF else 0

        # 跳过系统消息
        if base_type == MSG_TYPE_SYSTEM:
            return None

        # 时间格式化
        time_text = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")

        # 获取内容
        content = self._decode_content(row, has_compress)

        # 解析消息类型和显示内容
        render_type, display_content = self._format_content(base_type, sub_type, content)
        if not display_content:
            return None

        # 解析发送者
        sender_id = row["real_sender_id"]
        sender_username = name2id.get(sender_id, "")
        is_sent = sender_username == self.my_wxid if self.my_wxid else False

        if is_sent:
            sender = self.my_nickname or self.my_wxid
        elif is_group and sender_username:
            sender = self._resolve_display_name(sender_username)
        elif not is_group:
            # 私聊：对方 username 就是会话 username，从联系人表解析显示名
            sender = self._resolve_display_name(sender_username) if sender_username else ""
        else:
            sender = self._resolve_display_name(sender_username) if sender_username else str(sender_id)

        return {
            "sender": sender,
            "time": time_text,
            "content": display_content,
            "type": render_type,
            "is_sent": is_sent,
        }

    def _decode_content(self, row: sqlite3.Row, has_compress: bool) -> str:
        """解码消息内容，处理 zstd 压缩"""
        # 优先尝试 compress_content
        if has_compress:
            compressed = row["compress_content"]
            if compressed:
                text = self._try_decompress(compressed)
                if text:
                    return text

        # 回退到 message_content
        raw = row["message_content"]
        if raw is None:
            return ""

        if isinstance(raw, bytes):
            text = self._try_decompress(raw)
            if text:
                return text
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return ""

        return str(raw)

    def _try_decompress(self, data) -> Optional[str]:
        """尝试 zstd 解压缩"""
        if data is None:
            return None

        raw_bytes = None
        if isinstance(data, bytes):
            raw_bytes = data
        elif isinstance(data, str):
            # 尝试 hex 解码
            try:
                raw_bytes = bytes.fromhex(data)
            except ValueError:
                pass

        if raw_bytes and raw_bytes[:4] == ZSTD_MAGIC:
            if zstd is None:
                logger.debug("zstandard 未安装，跳过压缩内容")
                return None
            try:
                decompressed = zstd.ZstdDecompressor().decompress(raw_bytes)
                return decompressed.decode("utf-8", errors="replace")
            except Exception:
                return None

        return None

    def _format_content(self, base_type: int, sub_type: int, content: str) -> Tuple[str, str]:
        """根据消息类型格式化内容，返回 (render_type, display_content)"""
        if base_type == MSG_TYPE_TEXT:
            return "text", content

        if base_type == MSG_TYPE_IMAGE:
            return "image", "[图片]"

        if base_type == MSG_TYPE_VOICE:
            return "voice", "[语音]"

        if base_type == MSG_TYPE_VIDEO:
            return "video", "[视频]"

        if base_type == MSG_TYPE_APP:
            return self._parse_app_message(sub_type, content)

        # 引用消息
        if base_type == 244813135921 & 0xFFFFFFFF:
            return "quote", content

        return "text", content

    def _parse_app_message(self, sub_type: int, content: str) -> Tuple[str, str]:
        """解析 App 消息（type=49）"""
        # 尝试从 XML 中提取标题
        title = self._extract_xml_tag(content, "title") or ""

        if sub_type == 5:  # 链接
            url = self._extract_xml_tag(content, "url") or ""
            return "link", f"[链接] {title}" + (f" {url}" if url else "")
        if sub_type == 6:  # 文件
            return "file", f"[文件] {title}"
        if sub_type == 33 or sub_type == 36:  # 小程序
            return "link", f"[小程序] {title}"
        if sub_type == 19:  # 聊天记录
            return "chathistory", "[聊天记录]"
        if sub_type == 2001:  # 红包
            return "redpacket", "[红包]"
        if sub_type == 2000:  # 转账
            return "transfer", "[转账]"

        if title:
            return "link", f"[链接] {title}"
        return "text", content if content and len(content) < 500 else ""

    @staticmethod
    def _extract_xml_tag(xml_str: str, tag: str) -> Optional[str]:
        """简单提取 XML 标签内容"""
        pattern = rf"<{tag}[^>]*>(.*?)</{tag}>"
        m = re.search(pattern, xml_str, re.DOTALL)
        if m:
            text = m.group(1).strip()
            # 去除 CDATA
            if text.startswith("<![CDATA[") and text.endswith("]]>"):
                text = text[9:-3]
            return text
        return None
