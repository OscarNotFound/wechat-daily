"""
ai_analyzer.py - AI 分析模块

支持多种 AI 后端：
  - DeepSeek（推荐，国内直连，便宜）
  - 通义千问 / 阿里云百炼
  - OpenAI / GPT
  - Claude（需翻墙）

所有后端使用 OpenAI SDK 的兼容接口调用。
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

from openai import OpenAI

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# 各平台的 API 地址
API_BASES = {
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

# 各平台推荐的默认模型
DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "openai": "gpt-4o",
}


class AIAnalyzer:
    """AI 分析器，支持多种后端"""

    def __init__(self, config: dict):
        ai_cfg = config["ai"]
        self.provider = ai_cfg.get("provider", "deepseek").lower()
        api_key = ai_cfg["api_key"]
        self.model = ai_cfg.get("model", DEFAULT_MODELS.get(self.provider, "deepseek-chat"))
        self.max_tokens = ai_cfg.get("max_tokens", 4096)
        self.my_nickname = config.get("wechat", {}).get("my_nickname", "")

        # 确定 API 地址
        base_url = ai_cfg.get("base_url", API_BASES.get(self.provider, ""))
        if not base_url:
            base_url = API_BASES["deepseek"]

        # 使用 OpenAI SDK（兼容 DeepSeek / 通义千问 / OpenAI）
        self.client = OpenAI(api_key=api_key, base_url=base_url)

        logger.info(f"AI 后端: {self.provider}, 模型: {self.model}")

        # 加载 prompt 模板
        self.summary_prompt = self._load_prompt("daily_summary.txt")
        self.task_prompt = self._load_prompt("task_extraction.txt")
        self.reflection_prompt = self._load_prompt("reflection.txt")

    def _load_prompt(self, filename: str) -> str:
        path = PROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning(f"Prompt 文件不存在: {path}")
        return ""

    def _call_api(self, prompt: str) -> str:
        """调用 AI API（OpenAI 兼容格式）"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI API 调用失败: {e}")
            raise

    # ------------------------------------------------------------------
    # 每日总结
    # ------------------------------------------------------------------
    def generate_daily_summary(self, chat_text: str, date_str: str, memory_text: str = "") -> dict:
        logger.info(f"正在生成 {date_str} 的每日总结...")

        prompt = (
            self.summary_prompt
            .replace("{chat_content}", chat_text)
            .replace("{my_nickname}", self.my_nickname)
            .replace("{user_memory}", memory_text)
        )
        raw_response = self._call_api(prompt)

        result = self._parse_summary(raw_response, date_str)
        logger.info(f"每日总结生成完成，包含 {len(result.get('key_items', []))} 个关键事项")
        return result

    def _parse_summary(self, response: str, date_str: str) -> dict:
        result = {
            "date": date_str,
            "summary": response,
            "key_items": [],
            "tags": [],
            "status": "正常",
        }

        lines = response.split("\n")
        in_key_items = False

        for line in lines:
            stripped = line.strip()

            if "关键事项" in stripped:
                in_key_items = True
                continue
            elif stripped.startswith("###") or stripped.startswith("##"):
                in_key_items = False

            if in_key_items and stripped.startswith("- "):
                item = stripped.lstrip("- ").strip()
                if item:
                    result["key_items"].append(item)

            if "今日标签" in stripped or "标签" in stripped:
                tag_text = stripped.split("标签")[-1].strip().strip("：:").strip()
                if not tag_text:
                    continue
                for sep in ["、", "，", ",", " ", "｜", "|"]:
                    if sep in tag_text:
                        result["tags"] = [
                            t.strip().strip("#") for t in tag_text.split(sep) if t.strip()
                        ]
                        break
                else:
                    result["tags"] = [tag_text.strip("#")]

            if "状态" in stripped and ("忙碌" in stripped or "正常" in stripped or "轻松" in stripped):
                for status in ["忙碌", "正常", "轻松"]:
                    if status in stripped:
                        result["status"] = status
                        break

        return result

    # ------------------------------------------------------------------
    # 任务提取
    # ------------------------------------------------------------------
    def extract_tasks(self, chat_text: str, date_str: str, memory_text: str = "") -> list:
        logger.info(f"正在提取 {date_str} 的任务...")

        prompt = (
            self.task_prompt
            .replace("{chat_content}", chat_text)
            .replace("{today_date}", date_str)
            .replace("{my_nickname}", self.my_nickname)
            .replace("{user_memory}", memory_text)
        )

        raw_response = self._call_api(prompt)
        tasks = self._parse_tasks(raw_response)

        logger.info(f"提取到 {len(tasks)} 个任务")
        return tasks

    def _parse_tasks(self, response: str) -> list:
        json_str = response.strip()

        # 移除 markdown 代码块标记
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_str = "\n".join(lines)

        try:
            data = json.loads(json_str)
            tasks = data.get("tasks", [])

            valid_tasks = []
            for task in tasks:
                if not task.get("title"):
                    continue
                task.setdefault("detail", "")
                task.setdefault("deadline", None)
                task.setdefault("deadline_text", "")
                task.setdefault("priority", "medium")
                task.setdefault("confidence", "medium")
                task.setdefault("source_chat", "")
                task.setdefault("source_message", "")
                task.setdefault("category", "other")

                if task["priority"] not in ("high", "medium", "low"):
                    task["priority"] = "medium"
                if task["confidence"] not in ("high", "medium", "low"):
                    task["confidence"] = "medium"
                if task["category"] not in ("work", "life", "social", "other"):
                    task["category"] = "other"

                # 丢弃低置信度任务
                if task["confidence"] == "low":
                    logger.info(f"  跳过低置信度任务: {task['title']}")
                    continue

                valid_tasks.append(task)

            return valid_tasks

        except json.JSONDecodeError as e:
            logger.error(f"解析任务 JSON 失败: {e}")
            logger.debug(f"原始响应: {response[:500]}")
            return []

    # ------------------------------------------------------------------
    # 反思 & 记忆更新
    # ------------------------------------------------------------------
    def reflect(self, chat_text: str, summary: dict, tasks: list, current_memory: str) -> dict:
        logger.info("正在进行 AI 反思，提取新记忆...")

        summary_text = summary.get("summary", "")
        tasks_text = "\n".join(
            f"- [{t.get('priority')}] {t['title']}"
            + (f"（DDL: {t['deadline']}）" if t.get("deadline") else "")
            + (f"（时间: {t.get('time')}）" if t.get("time") else "")
            + (f"（地点: {t.get('location')}）" if t.get("location") else "")
            for t in tasks
        ) if tasks else "（无任务）"

        prompt = (
            self.reflection_prompt
            .replace("{chat_content}", chat_text[:3000])
            .replace("{summary_text}", summary_text)
            .replace("{tasks_text}", tasks_text)
            .replace("{current_memory}", current_memory)
            .replace("{my_nickname}", self.my_nickname)
        )

        raw_response = self._call_api(prompt)
        return self._parse_reflection(raw_response)

    def _parse_reflection(self, response: str) -> dict:
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_str = "\n".join(lines)

        try:
            data = json.loads(json_str)
            return {
                "people": data.get("people", []),
                "groups": data.get("groups", []),
                "recurring": data.get("recurring", []),
                "patterns": data.get("patterns", []),
                "corrections": data.get("corrections", []),
            }
        except json.JSONDecodeError as e:
            logger.warning(f"解析反思 JSON 失败: {e}")
            return {"people": [], "groups": [], "recurring": [], "patterns": [], "corrections": []}


class AIAnalyzerMock:
    """Mock 版本，用于测试"""

    def generate_daily_summary(self, chat_text: str, date_str: str) -> dict:
        return {
            "date": date_str,
            "summary": f"## 📅 {date_str} 每日总结\n\n这是一个测试总结。",
            "key_items": ["测试事项1", "测试事项2"],
            "tags": ["测试"],
            "status": "正常",
        }

    def extract_tasks(self, chat_text: str, date_str: str) -> list:
        return [
            {
                "title": "测试任务",
                "detail": "这是一个测试任务",
                "deadline": date_str,
                "deadline_text": "今天",
                "priority": "medium",
                "source_chat": "测试对话",
                "source_message": "测试消息",
                "category": "other",
            }
        ]
