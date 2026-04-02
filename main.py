"""
main.py - WeChat Daily 主入口

每日自动流程：
  1. 提取微信聊天记录
  2. 清洗处理消息
  3. AI 生成总结 & 提取任务
  4. 写入 Notion

用法：
  python main.py                  # 处理今天的聊天记录
  python main.py --date 2025-06-01  # 处理指定日期
  python main.py --setup            # 首次设置（创建 Notion 数据库）
  python main.py --test             # 测试模式（不调用 API）
  python main.py --backfill 7       # 补跑最近 7 天
"""

import argparse
import logging
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from auto_export import AutoExporter
from extractor import WeChatExtractor
from processor import MessageProcessor
from ai_analyzer import AIAnalyzer, AIAnalyzerMock
from notion_writer import NotionWriter
from memory_manager import MemoryManager

# ------------------------------------------------------------------
# 日志配置
# ------------------------------------------------------------------
def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    log_file = log_cfg.get("file", "logs/run.log")
    log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    # 确保日志目录存在
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 加载配置
# ------------------------------------------------------------------
def load_config(config_path: str = "config.yaml") -> dict:
    path = Path(config_path)
    if not path.exists():
        print(f"配置文件不存在: {path.absolute()}")
        print("请复制 config.yaml 并填写你的 API Keys")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------
# 核心流程
# ------------------------------------------------------------------
def run_daily(config: dict, target_date: datetime, test_mode: bool = False):
    """执行单日处理流程"""
    date_str = target_date.strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"开始处理 {date_str} 的聊天记录")
    logger.info("=" * 60)

    # 步骤 0: 自动导出当天聊天记录
    wechat_cfg = config.get("wechat", {})
    api_url = wechat_cfg.get("tool_api_url", "")
    if api_url:
        logger.info("[0/5] 自动导出聊天记录...")
        try:
            exporter = AutoExporter(api_url, wechat_cfg.get("export_dir", "export"))
            exporter.export_date(target_date)
        except ConnectionError:
            logger.warning("WeChatDataAnalysis 后端未运行，跳过自动导出，使用已有导出数据")
        except Exception as e:
            logger.warning(f"自动导出失败: {e}，尝试使用已有导出数据")
    else:
        logger.info("未配置 tool_api_url，跳过自动导出")

    # 步骤 1: 提取聊天记录
    logger.info("[1/5] 提取微信聊天记录...")
    extractor = WeChatExtractor(config)
    try:
        raw_data = extractor.extract_auto(target_date)
    except Exception as e:
        logger.error(f"提取失败: {e}")
        return False

    if not raw_data.get("chats"):
        logger.warning(f"{date_str} 没有找到聊天记录，跳过")
        return False

    # 步骤 2: 处理消息
    logger.info("[2/5] 清洗和处理消息...")
    processor = MessageProcessor(config)
    processed_data = processor.process(raw_data)

    if not processed_data.get("chats"):
        logger.warning(f"{date_str} 处理后没有有效对话，跳过")
        return False

    # 构建 AI 用的文本
    chat_text = processor.build_prompt_text(processed_data)
    logger.info(f"构建完成，文本长度: {len(chat_text)} 字符")

    # 加载记忆
    memory = MemoryManager()
    memory_text = memory.build_prompt_text()

    # 步骤 3: AI 分析
    logger.info("[3/5] AI 分析中...")
    if test_mode:
        analyzer = AIAnalyzerMock()
        logger.info("（测试模式，使用 Mock 数据）")
    else:
        analyzer = AIAnalyzer(config)

    # 生成每日总结
    summary = analyzer.generate_daily_summary(chat_text, date_str, memory_text)
    logger.info(f"总结状态: {summary.get('status')}, "
                f"标签: {summary.get('tags')}")

    # 提取任务
    tasks = analyzer.extract_tasks(chat_text, date_str, memory_text)
    logger.info(f"提取到 {len(tasks)} 个任务:")
    for t in tasks:
        logger.info(f"  - [{t.get('priority')}] {t['title']} "
                     f"(DDL: {t.get('deadline', '无')})")

    # 步骤 4: 写入 Notion
    logger.info("[4/5] 写入 Notion...")
    if test_mode:
        logger.info("（测试模式，跳过 Notion 写入）")
        logger.info("--- 每日总结预览 ---")
        logger.info(summary.get("summary", ""))
        logger.info("--- 任务列表预览 ---")
        for t in tasks:
            logger.info(f"  [{t['priority']}] {t['title']} → {t.get('deadline', '无DDL')}")
        return True

    writer = NotionWriter(config)

    # 写入总结
    try:
        summary_page_id = writer.write_daily_summary(summary)
        logger.info(f"每日总结已写入: {summary_page_id}")
    except Exception as e:
        logger.error(f"写入每日总结失败: {e}")

    # 写入任务
    try:
        task_ids = writer.write_tasks(tasks, date_str)
        logger.info(f"写入 {len(task_ids)} 个任务")
    except Exception as e:
        logger.error(f"写入任务失败: {e}")

    # 步骤 5: AI 反思 & 更新记忆
    if not test_mode:
        try:
            logger.info("[5/5] AI 反思，更新记忆...")
            reflection = analyzer.reflect(chat_text, summary, tasks, memory_text)
            memory.merge_reflection(reflection)
        except Exception as e:
            logger.warning(f"AI 反思失败（不影响主流程）: {e}")

    # 清理导出文件（ZIP + 解压目录）
    export_dir = Path(wechat_cfg.get("export_dir", "export"))
    try:
        import glob as _glob
        for zf in _glob.glob(str(export_dir / "wechat_chat_export_*.zip")):
            Path(zf).unlink()
            logger.info(f"已删除 ZIP: {zf}")
        for d in export_dir.iterdir():
            if d.is_dir() and d.name.startswith("wechat_chat_export_"):
                shutil.rmtree(d)
                logger.info(f"已删除导出目录: {d}")
    except Exception as e:
        logger.warning(f"清理导出文件失败: {e}")

    logger.info(f"✅ {date_str} 处理完成!")
    return True


# ------------------------------------------------------------------
# 首次设置
# ------------------------------------------------------------------
def setup(config: dict):
    """首次设置：创建 Notion 数据库"""
    print("\n" + "=" * 50)
    print("WeChat Daily - 首次设置")
    print("=" * 50)
    print("\n这个步骤会在你的 Notion 中创建两个数据库：")
    print("  1. 📅 每日总结 - 记录每天的活动总结")
    print("  2. ✅ 待办任务 - 从聊天中提取的任务和DDL")

    print("\n准备工作：")
    print("  1. 在 notion.so/my-integrations 创建一个 Integration")
    print("  2. 复制 Integration 的 API Key 到 config.yaml")
    print("  3. 在 Notion 中创建一个空白页面")
    print("  4. 将 Integration 连接到该页面（页面右上角 ··· → Connections）")

    parent_page_id = input("\n请输入 Notion 父页面的 ID: ").strip()

    if not parent_page_id:
        print("页面 ID 不能为空")
        return

    # 去除可能的 URL 前缀
    if "/" in parent_page_id:
        parent_page_id = parent_page_id.split("/")[-1]
    if "-" in parent_page_id:
        parent_page_id = parent_page_id.split("-")[-1]
    if "?" in parent_page_id:
        parent_page_id = parent_page_id.split("?")[0]

    writer = NotionWriter(config)

    try:
        result = writer.setup_databases(parent_page_id)
        print("\n✅ 数据库创建成功!")
        print(f"\n请将以下 ID 填入 config.yaml:")
        print(f"  daily_summary_db_id: \"{result['summary_db_id']}\"")
        print(f"  todo_tasks_db_id: \"{result['todo_db_id']}\"")
    except Exception as e:
        print(f"\n❌ 创建失败: {e}")
        print("请检查 API Key 和页面 ID 是否正确，以及 Integration 是否已连接到页面")


# ------------------------------------------------------------------
# CLI 入口
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="WeChat Daily - 微信聊天记录自动总结 & 任务提取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                     # 处理今天
  python main.py --date 2025-06-01   # 处理指定日期
  python main.py --setup             # 首次设置 Notion
  python main.py --test              # 测试模式
  python main.py --backfill 7        # 补跑最近7天
        """,
    )
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)")
    parser.add_argument("--setup", action="store_true", help="首次设置 Notion 数据库")
    parser.add_argument("--test", action="store_true", help="测试模式（不调用真实 API）")
    parser.add_argument("--backfill", type=int, help="补跑最近 N 天")

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    setup_logging(config)

    # 首次设置
    if args.setup:
        setup(config)
        return

    # 确定日期
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"日期格式错误: {args.date}，请使用 YYYY-MM-DD 格式")
            sys.exit(1)
    else:
        target_date = datetime.now()

    # 补跑模式
    if args.backfill:
        logger.info(f"补跑模式: 最近 {args.backfill} 天")
        success_count = 0
        for i in range(args.backfill, 0, -1):
            date = datetime.now() - timedelta(days=i)
            try:
                if run_daily(config, date, test_mode=args.test):
                    success_count += 1
            except Exception as e:
                logger.error(f"{date.strftime('%Y-%m-%d')} 处理失败: {e}")
        logger.info(f"补跑完成: {success_count}/{args.backfill} 天成功")
        return

    # 单日模式
    try:
        run_daily(config, target_date, test_mode=args.test)
    except Exception as e:
        logger.error(f"处理失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
