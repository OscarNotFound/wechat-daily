# WeChat Daily

微信聊天记录自动总结 & 任务提取工具。

每天定时从微信 PC 端读取聊天记录，经 AI 分析后生成每日总结和待办任务，写入 Notion。

## 工作流程

```
wechat_core 直接读取微信加密数据库（优先）
  ↓ 若不可用，回退到 WeChatDataAnalysis 后端 API 导出
processor.py 清洗过滤（去系统消息、表情包、水群）
  ↓
ai_analyzer.py 调用 DeepSeek 生成总结 + 提取任务（注入用户记忆）
  ↓
notion_writer.py 写入 Notion（去重，重跑同一天会更新而非重复）
  ↓
ai_analyzer.py 反思，自动更新 memory.yaml
  ↓
自动清理临时文件
```

## 前置条件

- Windows 10/11
- Python 3.9+
- 微信 PC 端 4.x（保持登录）
- **首次使用**需要 [WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) 解密一次数据库，之后密钥自动保存，无需再打开

### 首次解密（只需做一次）

1. 打开 [WeChatDataAnalysis Release 页面](https://github.com/LifeArchiveProject/WeChatDataAnalysis/releases/latest)，下载并安装 EXE
2. 启动 WeChatDataAnalysis，登录微信后在 UI 中点击解密数据库
3. 密钥会自动保存，此后 WeChat Daily 直接读取数据库，**不再需要 WeChatDataAnalysis 运行**

## 快速开始

### 1. 安装依赖

```bash
cd wechat-daily
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，填入：

| 必填项 | 说明 | 获取方式 |
|--------|------|----------|
| `ai.api_key` | AI 平台的 API Key | [platform.deepseek.com](https://platform.deepseek.com) |
| `notion.api_key` | Notion Integration Secret | [notion.so/my-integrations](https://www.notion.so/my-integrations) |
| `wechat.my_nickname` | 你的微信昵称 | 微信个人信息页，必须完全一致 |

`daily_summary_db_id` 和 `todo_tasks_db_id` 在第 3 步 setup 后填入。

`memory.yaml` 不需要手动创建，首次运行后 AI 会自动生成。想预填个人信息可以：

```bash
cp memory.yaml.example memory.yaml
# 然后填入昵称、学校等基本信息
```

### 3. 首次设置 Notion

1. 在 [notion.so/my-integrations](https://www.notion.so/my-integrations) 创建 Integration，复制 Secret
2. 在 Notion 中创建空白页面，连接该 Integration（页面右上角 `···` → `Connections`）
3. 运行：

```bash
python main.py --setup
```

4. 按提示输入页面 ID，将输出的两个数据库 ID 填入 `config.yaml`

### 4. 运行今天的内容

确保微信 PC 端保持登录，然后：

```bash
python main.py
```

程序会自动找到微信数据库、解密、分析并写入 Notion。

## 使用方法

```bash
python main.py                       # 处理今天
python main.py --date 2026-04-01     # 处理指定日期
python main.py --backfill 7          # 补跑最近 7 天
python main.py --test                # 测试模式（不调用 AI 和 Notion）
python main.py --setup               # 首次设置 Notion 数据库
```

## 定时任务

右键 `install_scheduler.bat` → **以管理员身份运行**，创建每天 23:00 自动执行的 Windows 定时任务。

- 只需微信 PC 端保持登录即可（一般默认开机自启）
- **不需要** WeChatDataAnalysis 在运行
- 重启电脑后定时任务仍然有效，无需重新安装

### 管理定时任务

```bash
schtasks /query /tn "WeChatDaily"              # 查看任务状态
schtasks /run /tn "WeChatDaily"                # 手动触发一次
schtasks /change /tn "WeChatDaily" /st 22:00   # 改为 22:00 执行
schtasks /delete /tn "WeChatDaily" /f          # 删除任务（停止自动运行）
```

### 自动补跑

如果某天没跑成（电脑关机等），下次成功运行时会**自动补跑**最近遗漏的日期。

通过 `config.yaml` 中的 `auto_catchup_days` 控制回溯范围（默认 7 天，设为 0 关闭）。

## 项目结构

```
wechat-daily/
├── config.yaml              # 配置（API Keys、过滤规则等）
├── memory.yaml              # 用户画像 & 持久记忆（AI 自动更新，可手动编辑）
├── main.py                  # 主入口（全自动流程）
├── wechat_core/             # 直接读取微信数据库（核心模块）
│   ├── decrypt.py           # SQLCipher 4.0 解密（纯 Python）
│   ├── detection.py         # 查找微信数据目录和进程
│   ├── key_manager.py       # 密钥管理（本地存储 / 进程提取）
│   ├── reader.py            # 从解密后的 SQLite 读取消息
│   └── pipeline.py          # 完整流水线：定位→解密→读取
├── auto_export.py           # WeChatDataAnalysis API 导出（回退方案）
├── extractor.py             # 从导出 JSON 读取聊天记录（回退方案）
├── processor.py             # 消息清洗、过滤、排序
├── ai_analyzer.py           # AI 调用（总结 + 任务提取 + 反思）
├── memory_manager.py        # 记忆管理（加载、注入 prompt、合并更新）
├── notion_writer.py         # Notion 写入（含去重和更新逻辑）
├── prompts/                 # AI Prompt 模板（可自定义）
│   ├── daily_summary.txt    # 每日总结 prompt
│   ├── task_extraction.txt  # 任务提取 prompt
│   └── reflection.txt       # 反思 prompt（用于更新记忆）
├── logs/run.log             # 运行日志
├── install_scheduler.bat    # Windows 定时任务安装脚本
└── requirements.txt         # Python 依赖
```

## 自定义

### 过滤规则（config.yaml）

| 配置项 | 说明 |
|--------|------|
| `excluded_groups` | 完全排除的群聊列表 |
| `important_groups` | 重要群聊（总结时优先展示） |
| `max_messages_per_chat` | 每个对话最多取多少条消息（默认 400） |
| `min_messages_threshold` | 消息少于此数的对话会被跳过（默认 1） |
| `ignored_msg_types` | 跳过的消息类型（默认 system/voip/emoji） |
| `auto_catchup_days` | 自动补跑最近 N 天内遗漏的日期（默认 7，设为 0 关闭） |

### AI Prompt 模板（prompts/）

- `daily_summary.txt` — 控制每日总结的格式、侧重点、字数
- `task_extraction.txt` — 控制任务提取规则：只提取"我"的任务，自动合并同类项，包含时间和地点

### 任务提取规则

核心逻辑：
- 只提取**我（config 中的 my_nickname）**需要做的事
- 排除：别人的任务、向别人报备/告知的事、已完成的动作、接龙报名等琐碎小事
- 同一件事的多条消息合并为一个任务
- 每个任务包含：标题、详情、截止日期、时间、地点、优先级、来源

### 记忆系统（memory.yaml）

AI 会自动积累对你的了解，用得越久越准确。

**自动记录：** 常联系的人和关系、群聊的性质、任务提取的修正规则

**手动编辑：**

```yaml
identity:
  nickname: "你的昵称"
  school: "XX大学"

people:
  - name: "张三"
    relation: "同学"
    context: "经常一起讨论作业"

corrections:
  - "接龙报名已完成的不算待办"
```

## Notion 数据库结构

### 每日总结

| 属性 | 类型 | 说明 |
|------|------|------|
| 标题 | Title | "📅 2026-04-01 每日总结" |
| 日期 | Date | 聊天日期 |
| 状态 | Select | 忙碌 / 正常 / 轻松 |
| 标签 | Multi-select | 工作、社交、学习... |

### 待办任务

| 属性 | 类型 | 说明 |
|------|------|------|
| 任务 | Title | 任务描述 |
| 状态 | Select | 📋待处理 / 🔄进行中 / ✅已完成 / ❌已取消 |
| 优先级 | Select | 🔴高 / 🟡中 / 🟢低 |
| 分类 | Select | 💼工作 / 🏠生活 / 👥社交 / 📎其他 |
| 截止日期 | Date | DDL |
| 创建日期 | Date | 提取日期 |
| 来源 | Text | 来源对话名称 |

重跑同一天时：总结会原地更新，旧任务会被删除后重新写入。

## 常见问题

**第一次运行失败，提示找不到密钥？**
用 WeChatDataAnalysis 解密一次数据库（见"首次解密"步骤）。密钥保存后不再需要。

**直接读取失败，自动回退到 API 导出？**
正常的回退行为。排查原因查看 `logs/run.log`。

**语音消息能处理吗？**
只处理文字、链接、引用等文本类消息，语音显示为 `[语音]`。

**API 费用？**
使用 DeepSeek（deepseek-chat），每天约 0.01–0.02 元，一个月不到 1 元。

**隐私安全？**
数据提取和处理全在本地。只有过滤后的聊天文本发给 AI API，处理完成后临时文件自动删除。

**国内网络问题？**
DeepSeek 和通义千问国内直连，无需代理。Notion API 可能需要代理：
```bash
set HTTPS_PROXY=http://127.0.0.1:7890
python main.py
```

## 技术栈

- Python 3.9+ / OpenAI SDK（兼容 DeepSeek、通义千问、OpenAI）
- SQLCipher 4.0 解密（cryptography 库，纯 Python 实现）
- Notion API
- Windows 任务计划程序

## 致谢

- [WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) — 微信 4.x 数据解密工具，本项目的数据库读取模块参考了其实现，首次使用需借助它解密一次
- [Claude Code](https://claude.ai/code) (Anthropic) — 项目代码由 Claude Opus 4.6 协助开发
