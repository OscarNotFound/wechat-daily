"""
wechat_core - 微信数据库解密与消息读取核心模块

从 WeChatDataAnalysis 提取的精简版本，支持：
  1. 从微信进程提取数据库密钥（需要 wx_key）
  2. 解密 SQLCipher 4.0 加密的数据库（纯 Python）
  3. 从解密后的数据库读取消息（SQLite）
  4. 完整的 detect → decrypt → read 流水线
"""

from .pipeline import DirectReadPipeline

__all__ = ["DirectReadPipeline"]
