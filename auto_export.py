"""
auto_export.py - 自动调用 WeChatDataAnalysis 后端 API 导出聊天记录

功能：
  1. 通过 HTTP API 创建导出任务（JSON 格式，按日期过滤）
  2. 轮询等待导出完成
  3. 下载 ZIP 并解压到 export/ 目录

用法：
  python auto_export.py                     # 导出今天
  python auto_export.py --date 2026-04-01   # 导出指定日期

前提：
  - WeChatDataAnalysis 后端已启动（默认 http://127.0.0.1:10392）
  - 微信 PC 端在线运行
  - 数据库已解密（至少手动解密过一次）
"""

import argparse
import logging
import time
import zipfile
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:10392"
POLL_INTERVAL = 2  # 秒
MAX_WAIT = 600  # 最长等待 10 分钟


class AutoExporter:
    def __init__(self, api_url: str, export_dir: str = "export"):
        self.api_url = api_url.rstrip("/")
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def health_check(self) -> bool:
        try:
            r = requests.get(f"{self.api_url}/api/health", timeout=5)
            return r.status_code == 200
        except requests.ConnectionError:
            return False

    def create_export_job(self, start_time: int, end_time: int, account: str = "") -> dict:
        payload = {
            "scope": "all",
            "format": "json",
            "start_time": start_time,
            "end_time": end_time,
            "include_hidden": False,
            "include_official": False,
            "include_media": False,
            "message_types": [],
            "output_dir": str(self.export_dir.resolve()),
        }
        if account:
            payload["account"] = account

        r = requests.post(f"{self.api_url}/api/chat/exports", json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()

        job = data.get("job") or data
        export_id = job.get("exportId") or job.get("export_id")
        if not export_id:
            raise RuntimeError(f"API 未返回 exportId: {data}")

        logger.info(f"导出任务已创建: {export_id}")
        return job

    def wait_for_job(self, export_id: str) -> dict:
        elapsed = 0
        while elapsed < MAX_WAIT:
            r = requests.get(f"{self.api_url}/api/chat/exports/{export_id}", timeout=10)
            r.raise_for_status()
            data = r.json()
            job = data.get("job") or data

            status = job.get("status", "")
            progress = job.get("progress", {})
            done = progress.get("conversationsDone", 0)
            total = progress.get("conversationsTotal", 0)
            msgs = progress.get("messagesExported", 0)

            logger.info(f"  状态: {status} | 对话: {done}/{total} | 消息: {msgs}")

            if status == "done":
                return job
            if status in ("error", "cancelled"):
                raise RuntimeError(f"导出失败: {job.get('error', status)}")

            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

        raise TimeoutError(f"导出超时（等待 {MAX_WAIT}s）")

    def download_and_extract(self, export_id: str, job: dict) -> str:
        zip_path = job.get("zipPath", "")

        if zip_path and Path(zip_path).exists():
            logger.info(f"ZIP 已在本地: {zip_path}")
            return self._extract_zip(Path(zip_path))

        logger.info("正在下载导出文件...")
        r = requests.get(
            f"{self.api_url}/api/chat/exports/{export_id}/download",
            timeout=300,
            stream=True,
        )
        r.raise_for_status()

        buf = BytesIO()
        for chunk in r.iter_content(chunk_size=8192):
            buf.write(chunk)
        buf.seek(0)

        return self._extract_zip_from_bytes(buf)

    def _extract_zip(self, zip_path: Path) -> str:
        dest = self.export_dir / zip_path.stem
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest)

        logger.info(f"解压完成: {dest}")
        return str(dest)

    def _extract_zip_from_bytes(self, buf: BytesIO) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.export_dir / f"wechat_chat_export_{ts}"
        dest.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(buf, "r") as zf:
            zf.extractall(dest)

        logger.info(f"解压完成: {dest}")
        return str(dest)

    def export_date(self, target_date: datetime, account: str = "") -> str:
        date_str = target_date.strftime("%Y-%m-%d")
        logger.info(f"开始自动导出 {date_str} 的聊天记录...")

        if not self.health_check():
            raise ConnectionError(
                f"无法连接 WeChatDataAnalysis 后端 ({self.api_url})。\n"
                "请确保：\n"
                "  1. WeChatDataAnalysis 已启动\n"
                "  2. 微信 PC 端在线运行\n"
                "  3. API 地址和端口正确"
            )

        start_dt = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = target_date.replace(hour=23, minute=59, second=59, microsecond=0)
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        job = self.create_export_job(start_ts, end_ts, account)
        export_id = job.get("exportId") or job.get("export_id")

        job = self.wait_for_job(export_id)
        extracted_path = self.download_and_extract(export_id, job)

        logger.info(f"导出完成: {extracted_path}")
        return extracted_path


def main():
    parser = argparse.ArgumentParser(description="自动导出微信聊天记录")
    parser.add_argument("--date", help="目标日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    wechat_cfg = config.get("wechat", {})
    api_url = wechat_cfg.get("tool_api_url", DEFAULT_API_URL)
    export_dir = wechat_cfg.get("export_dir", "export")

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        target_date = datetime.now()

    exporter = AutoExporter(api_url, export_dir)
    exporter.export_date(target_date)


if __name__ == "__main__":
    main()
