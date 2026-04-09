"""
pipeline.py - 直接读取微信数据库的完整流程

detect db_storage → get/extract key → decrypt DBs → read messages

替代原来依赖 WeChatDataAnalysis HTTP API 的导出流程。
"""

import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import detection, decrypt, key_manager
from .reader import WeChatReader

logger = logging.getLogger(__name__)

# 需要解密的核心数据库
CORE_DBS = ["session.db", "contact.db"]
# 消息数据库的匹配模式
MSG_DB_PATTERNS = ["message*.db", "MSG*.db"]


class DirectReadPipeline:
    """直接从微信加密数据库读取消息的完整流程"""

    def __init__(self, config: dict):
        wechat_cfg = config.get("wechat", {})
        self.my_wxid = wechat_cfg.get("my_wxid", "")
        self.my_nickname = wechat_cfg.get("my_nickname", "")
        self.excluded_groups = set(wechat_cfg.get("excluded_groups", []))
        self.important_groups = set(wechat_cfg.get("important_groups", []))
        self._decrypt_dir: Optional[Path] = None

    def read_date(self, target_date: datetime) -> dict:
        """
        完整流程：定位 → 解密 → 读取 → 返回与 extractor 兼容的数据格式

        Raises:
            RuntimeError: 任何步骤失败时
        """
        # 1. 查找 db_storage
        logger.info("查找微信数据目录...")
        db_storage = detection.find_wechat_db_storage()
        if not db_storage:
            raise RuntimeError(
                "未找到微信 db_storage 目录。\n"
                "请确保微信已登录且数据存储在默认位置。"
            )
        logger.info(f"找到 db_storage: {db_storage}")
        db_storage_path = Path(db_storage)

        # 从路径自动推导 wxid（如未在 config 中配置）
        # 路径格式：.../xwechat_files/wxid_XXXXXXXX_XXXX/db_storage
        if not self.my_wxid:
            account_dir = db_storage_path.parent.name
            parts = account_dir.rsplit("_", 1)
            if len(parts) == 2 and len(parts[1]) == 4 and account_dir.startswith("wxid_"):
                self.my_wxid = parts[0]
                logger.info(f"自动推导 wxid: {self.my_wxid}")

        # 2. 获取密钥
        logger.info("获取数据库密钥...")
        db_key = self._get_key()
        if not db_key:
            raise RuntimeError(
                "未能获取数据库密钥。\n"
                "请先用 WeChatDataAnalysis 解密一次数据库，密钥会自动保存。\n"
                "或安装 wx_key 模块从微信进程提取。"
            )
        logger.info("密钥获取成功")

        # 3. 解密数据库到临时目录
        self._decrypt_dir = Path(tempfile.mkdtemp(prefix="wechat_decrypt_"))
        logger.info(f"解密数据库到: {self._decrypt_dir}")

        decrypted_count = self._decrypt_databases(db_storage_path, db_key)
        if decrypted_count == 0:
            raise RuntimeError("没有成功解密任何数据库文件")
        logger.info(f"成功解密 {decrypted_count} 个数据库")

        # 4. 读取消息
        logger.info("读取消息...")
        reader = WeChatReader(
            str(self._decrypt_dir),
            my_wxid=self.my_wxid,
            my_nickname=self.my_nickname,
        )
        result = reader.read_date(
            target_date,
            excluded_groups=self.excluded_groups,
            important_groups=self.important_groups,
        )

        return result

    def cleanup(self):
        """清理解密的临时文件"""
        if self._decrypt_dir and self._decrypt_dir.exists():
            try:
                shutil.rmtree(self._decrypt_dir)
                logger.info(f"已清理临时目录: {self._decrypt_dir}")
            except Exception as e:
                logger.warning(f"清理临时目录失败: {e}")

    def _get_key(self) -> Optional[str]:
        """尝试多种方式获取密钥"""
        # 1. 从本地 key store 读取
        key = key_manager.get_stored_key(self.my_wxid)
        if key:
            logger.info("从本地 key store 读取到密钥")
            return key

        # 2. 从 WeChatDataAnalysis 的 key store 读取
        key = key_manager.try_load_from_wechat_data_analysis()
        if key:
            logger.info("从 WeChatDataAnalysis 读取到密钥")
            return key

        # 3. 尝试从微信进程提取（需要 wx_key）
        try:
            exe_path, version = detection.find_wechat_exe()
            if exe_path and version:
                logger.info(f"检测到微信: {exe_path} (v{version})")
                result = key_manager.extract_key_from_process(exe_path, version)
                key = result.get("db_key")
                if key:
                    # 保存以便下次使用
                    account = self.my_wxid or "default"
                    key_manager.save_key(account, key)
                    return key
        except Exception as e:
            logger.warning(f"从微信进程提取密钥失败: {e}")

        return None

    def _decrypt_databases(self, db_storage: Path, db_key: str) -> int:
        """解密所有需要的数据库文件"""
        count = 0

        # 解密核心数据库
        for db_name in CORE_DBS:
            src = self._find_db_file(db_storage, db_name)
            if src:
                dst = self._decrypt_dir / db_name
                if self._decrypt_one(src, dst, db_key):
                    count += 1

        # 解密消息数据库（可能在 message/ 子目录下）
        for pattern in MSG_DB_PATTERNS:
            for src in db_storage.rglob(pattern):
                if src.is_file() and not src.name.startswith("biz_"):
                    dst = self._decrypt_dir / src.name
                    if self._decrypt_one(src, dst, db_key):
                        count += 1

        return count

    def _find_db_file(self, base: Path, name: str) -> Optional[Path]:
        """在 db_storage 中查找数据库文件（可能在子目录中）"""
        direct = base / name
        if direct.exists():
            return direct

        # 搜索子目录（最多2层）
        for p in base.rglob(name):
            if p.is_file():
                return p
        return None

    def _decrypt_one(self, src: Path, dst: Path, db_key: str) -> bool:
        """解密单个数据库文件"""
        try:
            # 先检查是否已经是明文 SQLite
            with open(src, "rb") as f:
                header = f.read(16)
            if header.startswith(b"SQLite format 3\x00"):
                # 已经是明文，直接复制
                shutil.copy2(src, dst)
                logger.debug(f"复制明文数据库: {src.name}")
                return True

            success = decrypt.decrypt_database(str(src), str(dst), db_key)
            if success:
                # 验证解密结果
                if self._verify_sqlite(dst):
                    logger.debug(f"解密成功: {src.name}")
                    return True
                else:
                    logger.warning(f"解密后验证失败: {src.name}")
                    dst.unlink(missing_ok=True)
                    return False
            return False
        except Exception as e:
            logger.warning(f"解密 {src.name} 失败: {e}")
            return False

    @staticmethod
    def _verify_sqlite(path: Path) -> bool:
        """验证文件是否是有效的 SQLite 数据库"""
        try:
            conn = sqlite3.connect(str(path))
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return True
        except Exception:
            return False
