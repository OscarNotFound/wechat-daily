"""
detection.py - 微信安装检测和数据目录定位
"""

import logging
import os
import ctypes
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def find_wechat_db_storage() -> Optional[str]:
    """查找微信的 db_storage 目录路径"""
    candidates = _get_candidate_roots()

    for root in candidates:
        for dirpath, dirnames, _ in os.walk(root):
            if Path(dirpath).name.lower() == "db_storage":
                if any(f.endswith(".db") for f in os.listdir(dirpath)
                       for _ in [None]  # dummy
                       ) or _has_db_files_recursive(dirpath):
                    logger.info(f"找到 db_storage: {dirpath}")
                    return dirpath
            # 不要递归太深
            if len(Path(dirpath).parts) - len(Path(root).parts) > 4:
                dirnames.clear()

    return None


def _has_db_files_recursive(path: str) -> bool:
    for _, _, files in os.walk(path):
        if any(f.endswith(".db") for f in files):
            return True
    return False


def _get_candidate_roots() -> List[Path]:
    """获取可能包含微信数据的目录列表"""
    roots = []
    documents = Path.home() / "Documents"

    for name in ["xwechat_files", "WeChat Files", "wechat_files"]:
        p = documents / name
        if p.exists():
            roots.append(p)

    # 也检查其他盘符
    for drive in _get_drives():
        for name in ["xwechat_files", "WeChat Files"]:
            p = Path(drive) / name
            if p.exists() and p not in roots:
                roots.append(p)

    return roots


def _get_drives() -> List[str]:
    """获取 Windows 盘符列表"""
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{chr(65 + i)}:\\" for i in range(26) if bitmask & (1 << i)]
    except Exception:
        return ["C:\\", "D:\\"]


def find_wechat_exe() -> Tuple[Optional[str], Optional[str]]:
    """查找微信 exe 路径和版本号"""
    import psutil

    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] and proc.info["name"].lower() == "weixin.exe":
            try:
                exe_path = proc.exe()
                version = _get_file_version(exe_path)
                return exe_path, version
            except Exception:
                continue

    return None, None


def _get_file_version(path: str) -> Optional[str]:
    """获取 exe 文件版本号"""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except ImportError:
        # win32api 不可用，用 ctypes 替代
        return _get_version_ctypes(path)
    except Exception:
        return None


def _get_version_ctypes(path: str) -> Optional[str]:
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data)
        buf = ctypes.c_void_p()
        length = ctypes.c_uint()
        ctypes.windll.version.VerQueryValueW(data, "\\", ctypes.byref(buf), ctypes.byref(length))

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
            ]

        info = ctypes.cast(buf, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None
