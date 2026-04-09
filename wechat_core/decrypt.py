"""
decrypt.py - 微信 4.x 数据库解密（SQLCipher 4.0）

纯 Python 实现，仅依赖 cryptography 库。
"""

import hashlib
import hmac
import logging

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

SQLITE_HEADER = b"SQLite format 3\x00"


def decrypt_database(db_path: str, output_path: str, key_hex: str) -> bool:
    if len(key_hex) != 64:
        raise ValueError("密钥必须是 64 位十六进制字符串")

    key_bytes = bytes.fromhex(key_hex)

    with open(db_path, "rb") as f:
        data = f.read()

    if len(data) < 4096:
        return False

    if data.startswith(SQLITE_HEADER):
        with open(output_path, "wb") as f:
            f.write(data)
        return True

    salt = data[:16]
    mac_salt = bytes(b ^ 0x3A for b in salt)

    derived_key = PBKDF2HMAC(
        algorithm=hashes.SHA512(), length=32, salt=salt,
        iterations=256000, backend=default_backend(),
    ).derive(key_bytes)

    mac_key = PBKDF2HMAC(
        algorithm=hashes.SHA512(), length=32, salt=mac_salt,
        iterations=2, backend=default_backend(),
    ).derive(derived_key)

    page_size = 4096
    iv_size = 16
    hmac_size = 64
    reserve_size = iv_size + hmac_size
    if reserve_size % 16 != 0:
        reserve_size = ((reserve_size // 16) + 1) * 16

    result = bytearray(SQLITE_HEADER)
    total_pages = len(data) // page_size
    ok = 0

    for i in range(total_pages):
        page = data[i * page_size:(i + 1) * page_size]
        page_num = i + 1
        offset = 16 if i == 0 else 0

        # HMAC 验证
        hmac_start = page_size - reserve_size + iv_size
        stored_hmac = page[hmac_start:hmac_start + hmac_size]
        data_end = page_size - reserve_size + iv_size

        mac = hmac.new(mac_key, digestmod=hashlib.sha512)
        mac.update(page[offset:data_end])
        mac.update(page_num.to_bytes(4, "little"))
        if stored_hmac != mac.digest():
            continue

        iv = page[page_size - reserve_size:page_size - reserve_size + iv_size]
        encrypted = page[offset:page_size - reserve_size]

        cipher = Cipher(algorithms.AES(derived_key), modes.CBC(iv), backend=default_backend())
        decrypted = cipher.decryptor().update(encrypted) + cipher.decryptor().finalize()

        # 重新解密（上面 finalize 了一半）
        dec = Cipher(algorithms.AES(derived_key), modes.CBC(iv), backend=default_backend()).decryptor()
        decrypted = dec.update(encrypted) + dec.finalize()

        result.extend(decrypted)
        result.extend(page[page_size - reserve_size:])
        ok += 1

    with open(output_path, "wb") as f:
        f.write(result)

    logger.info(f"解密完成: {ok}/{total_pages} 页成功 → {output_path}")
    return ok > 0
