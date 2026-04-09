"""
notion_writer.py - Notion 写入模块

负责：
  1. 创建/检查 Notion 数据库
  2. 写入每日总结
  3. 写入待办任务
  4. 避免重复写入
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# 优先级颜色映射
PRIORITY_COLORS = {
    "high": "red",
    "medium": "yellow",
    "low": "green",
}

# 分类标签颜色映射
CATEGORY_COLORS = {
    "work": "blue",
    "life": "green",
    "social": "purple",
    "other": "gray",
}

CATEGORY_LABELS = {
    "work": "💼 工作",
    "life": "🏠 生活",
    "social": "👥 社交",
    "other": "📎 其他",
}

PRIORITY_LABELS = {
    "high": "🔴 高",
    "medium": "🟡 中",
    "low": "🟢 低",
}


class NotionWriter:
    """Notion API 写入器"""

    def __init__(self, config: dict):
        notion_cfg = config["notion"]
        self.api_key = notion_cfg["api_key"]
        self.summary_db_id = notion_cfg["daily_summary_db_id"]
        self.todo_db_id = notion_cfg["todo_tasks_db_id"]
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    # ------------------------------------------------------------------
    # 写入每日总结
    # ------------------------------------------------------------------
    def write_daily_summary(self, summary: dict) -> str:
        """
        写入每日总结到 Notion 数据库。

        Args:
            summary: AI 生成的总结数据
                {
                    "date": "2025-06-01",
                    "summary": "总结内容...",
                    "key_items": ["事项1", ...],
                    "tags": ["标签1", ...],
                    "status": "忙碌"
                }

        Returns:
            创建的 Notion 页面 ID
        """
        date_str = summary["date"]

        # 检查是否已经存在该日期的总结
        existing = self._find_summary_by_date(date_str)
        if existing:
            logger.info(f"{date_str} 的总结已存在，正在更新...")
            return self._update_summary(existing, summary)

        logger.info(f"正在写入 {date_str} 的每日总结到 Notion...")

        # 构建页面属性
        properties = {
            "日期": {
                "date": {"start": date_str}
            },
            "标题": {
                "title": [{"text": {"content": f"📅 {date_str} 每日总结"}}]
            },
            "状态": {
                "select": {"name": summary.get("status", "正常")}
            },
        }

        # 添加标签（多选）
        if summary.get("tags"):
            properties["标签"] = {
                "multi_select": [{"name": tag} for tag in summary["tags"][:5]]
            }

        # 构建页面内容 (blocks)
        children = self._build_summary_blocks(summary)

        # 创建页面
        page_data = {
            "parent": {"database_id": self.summary_db_id},
            "properties": properties,
            "children": children,
        }

        response = self._api_request("POST", "/pages", page_data)
        page_id = response.get("id", "")

        logger.info(f"每日总结已写入 Notion, page_id: {page_id}")
        return page_id

    def _build_summary_blocks(self, summary: dict) -> list:
        """构建总结页面的内容块"""
        blocks = []

        # 总结正文
        summary_text = summary.get("summary", "")
        # 将总结按段落拆分为多个 block
        paragraphs = [p.strip() for p in summary_text.split("\n") if p.strip()]

        for para in paragraphs:
            # 跳过已经在属性中的 markdown 标题
            if para.startswith("## 📅"):
                continue

            # 转换 markdown 标题为 Notion heading
            if para.startswith("### "):
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"text": {"content": para.lstrip("# ").strip()}}]
                    }
                })
            elif para.startswith("## "):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"text": {"content": para.lstrip("# ").strip()}}]
                    }
                })
            elif para.startswith("- "):
                # 列表项
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"text": {"content": para.lstrip("- ").strip()}}]
                    }
                })
            else:
                # 普通段落
                # Notion API 限制每个 rich_text 块最多 2000 字符
                content = para[:2000]
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": content}}]
                    }
                })

        # 添加分隔线
        blocks.append({"object": "block", "type": "divider", "divider": {}})

        # 关键事项
        if summary.get("key_items"):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"text": {"content": "📌 关键事项"}}]
                }
            })
            for item in summary["key_items"]:
                blocks.append({
                    "object": "block",
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"text": {"content": item}}],
                        "checked": False,
                    }
                })

        return blocks

    # ------------------------------------------------------------------
    # 写入待办任务
    # ------------------------------------------------------------------
    def write_tasks(self, tasks: list, date_str: str) -> list:
        """
        写入待办任务到 Notion 数据库。

        Args:
            tasks: AI 提取的任务列表
            date_str: 提取日期

        Returns:
            创建的页面 ID 列表
        """
        if not tasks:
            logger.info("没有任务需要写入")
            return []

        # 删除该日期的旧任务，避免重跑时重复
        self._delete_tasks_by_date(date_str)

        logger.info(f"正在写入 {len(tasks)} 个任务到 Notion...")
        page_ids = []

        for task in tasks:
            try:
                page_id = self._write_single_task(task, date_str)
                page_ids.append(page_id)
            except Exception as e:
                logger.error(f"写入任务失败 [{task.get('title')}]: {e}")

        logger.info(f"成功写入 {len(page_ids)}/{len(tasks)} 个任务")
        return page_ids

    def _write_single_task(self, task: dict, date_str: str) -> str:
        """写入单个任务"""
        # 检查是否已存在相同标题的任务
        existing = self._find_task_by_title(task["title"])
        if existing:
            logger.info(f"任务已存在，跳过: {task['title']}")
            return existing

        priority = task.get("priority", "medium")
        category = task.get("category", "other")

        properties = {
            "任务": {
                "title": [{"text": {"content": task["title"]}}]
            },
            "优先级": {
                "select": {
                    "name": PRIORITY_LABELS.get(priority, "🟡 中"),
                    "color": PRIORITY_COLORS.get(priority, "yellow"),
                }
            },
            "分类": {
                "select": {
                    "name": CATEGORY_LABELS.get(category, "📎 其他"),
                    "color": CATEGORY_COLORS.get(category, "gray"),
                }
            },
            "状态": {
                "select": {
                    "name": "❓ 待确认" if task.get("confidence") == "medium" else "📋 待处理"
                }
            },
            "创建日期": {
                "date": {"start": date_str}
            },
            "来源": {
                "rich_text": [{"text": {"content": task.get("source_chat", "")}}]
            },
        }

        # 添加截止日期（如果有）
        if task.get("deadline"):
            properties["截止日期"] = {
                "date": {"start": task["deadline"]}
            }

        # 页面内容
        children = []

        if task.get("detail"):
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": task["detail"]}}]
                }
            })

        if task.get("source_message"):
            children.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"text": {"content": task["source_message"]}}],
                    "icon": {"emoji": "💬"},
                }
            })

        info_parts = []
        if task.get("deadline_text"):
            info_parts.append(f"⏰ 截止: {task['deadline_text']}")
        if task.get("time"):
            info_parts.append(f"🕐 时间: {task['time']}")
        if task.get("location"):
            info_parts.append(f"📍 地点: {task['location']}")

        if info_parts:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": "\n".join(info_parts)}}]
                }
            })

        page_data = {
            "parent": {"database_id": self.todo_db_id},
            "properties": properties,
            "children": children,
        }

        response = self._api_request("POST", "/pages", page_data)
        return response.get("id", "")

    # ------------------------------------------------------------------
    # 查重与更新
    # ------------------------------------------------------------------
    def _find_summary_by_date(self, date_str: str) -> str | None:
        """查找指定日期是否已有总结"""
        query = {
            "filter": {
                "property": "日期",
                "date": {"equals": date_str}
            }
        }
        try:
            response = self._api_request(
                "POST", f"/databases/{self.summary_db_id}/query", query
            )
            results = response.get("results", [])
            return results[0]["id"] if results else None
        except Exception:
            return None

    def _delete_tasks_by_date(self, date_str: str):
        """删除指定创建日期的所有旧任务（用于重跑去重）"""
        query = {
            "filter": {
                "property": "创建日期",
                "date": {"equals": date_str}
            }
        }
        try:
            response = self._api_request(
                "POST", f"/databases/{self.todo_db_id}/query", query
            )
            results = response.get("results", [])
            if not results:
                return
            logger.info(f"发现 {len(results)} 个 {date_str} 的旧任务，正在删除...")
            for page in results:
                self._api_request("PATCH", f"/pages/{page['id']}", {"archived": True})
            logger.info(f"已删除 {len(results)} 个旧任务")
        except Exception as e:
            logger.warning(f"删除旧任务失败: {e}")

    def _find_task_by_title(self, title: str) -> str | None:
        """查找是否已存在相同标题的任务"""
        query = {
            "filter": {
                "property": "任务",
                "title": {"equals": title}
            }
        }
        try:
            response = self._api_request(
                "POST", f"/databases/{self.todo_db_id}/query", query
            )
            results = response.get("results", [])
            return results[0]["id"] if results else None
        except Exception:
            return None

    def _update_summary(self, page_id: str, summary: dict) -> str:
        """更新已有的总结页面"""
        # 更新属性
        properties = {
            "状态": {"select": {"name": summary.get("status", "正常")}},
        }
        if summary.get("tags"):
            properties["标签"] = {
                "multi_select": [{"name": tag} for tag in summary["tags"][:5]]
            }

        self._api_request("PATCH", f"/pages/{page_id}", {"properties": properties})

        # 删除旧内容块
        try:
            blocks_resp = self._api_request("GET", f"/blocks/{page_id}/children")
            for block in blocks_resp.get("results", []):
                self._api_request("DELETE", f"/blocks/{block['id']}")
        except Exception as e:
            logger.warning(f"清理旧内容失败: {e}")

        # 写入新内容
        children = self._build_summary_blocks(summary)
        self._api_request(
            "PATCH",
            f"/blocks/{page_id}/children",
            {"children": children}
        )

        return page_id

    # ------------------------------------------------------------------
    # Notion 数据库初始化（首次使用时创建数据库）
    # ------------------------------------------------------------------
    def setup_databases(self, parent_page_id: str) -> dict:
        """
        在指定的 Notion 页面下创建所需的数据库。
        仅首次使用时需要调用。

        Args:
            parent_page_id: 要创建数据库的父页面 ID

        Returns:
            {"summary_db_id": "...", "todo_db_id": "..."}
        """
        logger.info("正在创建 Notion 数据库...")

        # 创建每日总结数据库
        summary_db = self._create_database(
            parent_page_id,
            title="📅 每日总结",
            properties={
                "标题": {"title": {}},
                "日期": {"date": {}},
                "状态": {
                    "select": {
                        "options": [
                            {"name": "忙碌", "color": "red"},
                            {"name": "正常", "color": "yellow"},
                            {"name": "轻松", "color": "green"},
                        ]
                    }
                },
                "标签": {"multi_select": {"options": []}},
            }
        )

        # 创建待办任务数据库
        todo_db = self._create_database(
            parent_page_id,
            title="✅ 待办任务",
            properties={
                "任务": {"title": {}},
                "状态": {
                    "select": {
                        "options": [
                            {"name": "📋 待处理", "color": "gray"},
                            {"name": "🔄 进行中", "color": "blue"},
                            {"name": "✅ 已完成", "color": "green"},
                            {"name": "❌ 已取消", "color": "red"},
                        ]
                    }
                },
                "优先级": {
                    "select": {
                        "options": [
                            {"name": "🔴 高", "color": "red"},
                            {"name": "🟡 中", "color": "yellow"},
                            {"name": "🟢 低", "color": "green"},
                        ]
                    }
                },
                "分类": {
                    "select": {
                        "options": [
                            {"name": "💼 工作", "color": "blue"},
                            {"name": "🏠 生活", "color": "green"},
                            {"name": "👥 社交", "color": "purple"},
                            {"name": "📎 其他", "color": "gray"},
                        ]
                    }
                },
                "截止日期": {"date": {}},
                "创建日期": {"date": {}},
                "来源": {"rich_text": {}},
            }
        )

        result = {
            "summary_db_id": summary_db["id"],
            "todo_db_id": todo_db["id"],
        }

        logger.info(f"数据库创建完成: {result}")
        return result

    def _create_database(self, parent_page_id: str, title: str, properties: dict) -> dict:
        """创建 Notion 数据库"""
        data = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"text": {"content": title}}],
            "properties": properties,
        }
        return self._api_request("POST", "/databases", data)

    # ------------------------------------------------------------------
    # HTTP 请求封装
    # ------------------------------------------------------------------
    def _api_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """发送 Notion API 请求"""
        url = f"{NOTION_API_URL}{endpoint}"

        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, timeout=30)
            elif method == "POST":
                resp = requests.post(url, headers=self.headers, json=data, timeout=30)
            elif method == "PATCH":
                resp = requests.patch(url, headers=self.headers, json=data, timeout=30)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self.headers, timeout=30)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            if resp.status_code >= 400:
                error_body = resp.json() if resp.text else {}
                error_msg = error_body.get("message", resp.text[:200])
                logger.error(f"Notion API 错误 [{resp.status_code}]: {error_msg}")
                resp.raise_for_status()

            return resp.json() if resp.text else {}

        except requests.exceptions.Timeout:
            logger.error(f"Notion API 请求超时: {endpoint}")
            raise
        except requests.exceptions.ConnectionError:
            logger.error("无法连接到 Notion API，请检查网络")
            raise
