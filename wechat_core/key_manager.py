"""
key_manager.py - 密钥管理（提取 + 存储）

首次运行时通过 wx_key 从微信进程提取密钥，之后从本地文件读取。
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import wx_key
except ImportError:
    wx_key = None

KEY_STORE_FILE = Path(__file__).parent.parent / "data" / "keys.json"


def get_stored_key(account: str = "") -> Optional[str]:
    """从本地存储读取已保存的密钥"""
    store = _load_store()
    if account:
        entry = store.get(account, {})
        return entry.get("db_key")

    # 没指定 account，返回最近更新的
    if not store:
        return None
    latest = max(store.values(), key=lambda v: v.get("updated_at", ""))
    return latest.get("db_key")


def save_key(account: str, db_key: str):
    """保存密钥到本地"""
    store = _load_store()
    store[account] = {
        "db_key": db_key,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    KEY_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_STORE_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"密钥已保存: {account}")


def extract_key_from_process(exe_path: str, version: str) -> dict:
    """从微信进程提取密钥（需要 wx_key 模块）"""
    if wx_key is None:
        raise RuntimeError(
            "wx_key 模块未安装。\n"
            "请安装：pip install wx_key\n"
            "或使用 WeChatDataAnalysis 的 UI 先解密一次，密钥会自动保存。"
        )

    import psutil
    from packaging import version as pkg_version

    config = _get_hook_config(version)
    if not config:
        raise RuntimeError(f"不支持的微信版本: {version}")

    # 关闭现有微信进程
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] == "Weixin.exe":
            proc.terminate()
    time.sleep(1)

    # 启动微信
    import subprocess
    subprocess.Popen(exe_path)
    time.sleep(2)

    # 找到新进程 PID
    pid = None
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        if proc.info["name"] == "Weixin.exe":
            pid = proc.info["pid"]
            break

    if not pid:
        raise RuntimeError("无法启动微信进程")

    # Hook 提取
    if not wx_key.initialize_hook(
        pid, "",
        config["pattern"], config["mask"], config["offset"],
        config.get("md5_pattern", ""), config.get("md5_mask", ""), config.get("md5_offset", 0),
    ):
        raise RuntimeError(f"Hook 初始化失败: {wx_key.get_last_error_msg()}")

    db_key = None
    start = time.time()
    try:
        while time.time() - start < 60:
            data = wx_key.poll_key_data()
            if data and "key" in data:
                db_key = data["key"]
                break
            time.sleep(0.1)
    finally:
        wx_key.cleanup_hook()

    if not db_key:
        raise TimeoutError("获取密钥超时，请在弹出的微信中完成登录")

    return {"db_key": db_key}


def try_load_from_wechat_data_analysis() -> Optional[str]:
    """尝试从 WeChatDataAnalysis 的 key store 读取密钥"""
    candidates = [
        Path.home() / "AppData" / "Roaming" / "wechat-data-analysis-desktop" / "output" / "account_keys.json",
        Path.home() / "AppData" / "Roaming" / "WeChatDataAnalysis" / "output" / "account_keys.json",
        Path("output") / "account_keys.json",
    ]

    # 也检查环境变量
    import os
    env_dir = os.environ.get("WECHAT_TOOL_DATA_DIR", "")
    if env_dir:
        candidates.insert(0, Path(env_dir) / "output" / "account_keys.json")
    env_out = os.environ.get("WECHAT_TOOL_OUTPUT_DIR", "")
    if env_out:
        candidates.insert(0, Path(env_out) / "account_keys.json")

    for path in candidates:
        if path.exists():
            try:
                store = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(store, dict) and store:
                    latest = max(store.items(), key=lambda kv: kv[1].get("updated_at", ""))
                    key = latest[1].get("db_key")
                    if key:
                        logger.info(f"从 WeChatDataAnalysis 读取到密钥: {path}")
                        # 同步保存到本项目的 key store
                        save_key(latest[0], key)
                        return key
            except Exception:
                continue

    return None


def _load_store() -> dict:
    if not KEY_STORE_FILE.exists():
        return {}
    try:
        data = json.loads(KEY_STORE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_hook_config(version_str: str) -> Optional[dict]:
    from packaging import version as pkg_version

    v = pkg_version.parse(version_str)

    if v > pkg_version.parse("4.1.6.14"):
        return {
            "pattern": " ".join(f"{b:02X}" for b in [
                0x24, 0x50, 0x48, 0xC7, 0x45, 0x00, 0xFE, 0xFF, 0xFF, 0xFF,
                0x44, 0x89, 0xCF, 0x44, 0x89, 0xC3, 0x49, 0x89, 0xD6, 0x48,
                0x89, 0xCE, 0x48, 0x89,
            ]),
            "mask": "xxxxxxxxxxxxxxxxxxxxxxxx",
            "offset": -3,
            "md5_pattern": "48 8D 4D 00 48 89 4D B0 48 89 45 B8 48 8D 7D 00 48 8D 55 B0 48 89 F9",
            "md5_mask": "xxx?xxxxxxxxxxx?xxxxxxx",
            "md5_offset": 4,
        }

    if pkg_version.parse("4.1.4") <= v <= pkg_version.parse("4.1.6.14"):
        return {
            "pattern": " ".join(f"{b:02X}" for b in [
                0x24, 0x08, 0x48, 0x89, 0x6C, 0x24, 0x10, 0x48, 0x89, 0x74,
                0x00, 0x18, 0x48, 0x89, 0x7C, 0x00, 0x20, 0x41, 0x56, 0x48,
                0x83, 0xEC, 0x50, 0x41,
            ]),
            "mask": "xxxxxxxxxx?xxxx?xxxxxxxx",
            "offset": -3,
            "md5_pattern": "48 8D 4D 00 48 89 4D B0 48 89 45 B8 48 8D 7D 00 48 8D 55 B0 48 89 F9",
            "md5_mask": "xxx?xxxxxxxxxxx?xxxxxxx",
            "md5_offset": 4,
        }

    return None
