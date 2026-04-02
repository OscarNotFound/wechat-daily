# WeChat Daily

微信聊天记录自动总结 & 任务提取工具。

每天自动从微信 PC 端导出聊天记录，经 AI 分析后生成每日总结和待办任务，写入 Notion。

## 工作流程

```
WeChatDataAnalysis 后端 API
  → auto_export.py 自动导出当天聊天记录（JSON ZIP）
  → extractor.py 读取导出的 JSON
  → processor.py 清洗过滤（去系统消息、表情包、水群）
  → ai_analyzer.py 调用 DeepSeek 生成总结 + 提取任务（注入用户记忆）
  → notion_writer.py 写入 Notion（去重，重跑同一天会更新而非重复）
  → ai_analyzer.py 反思，自动更新 memory.yaml（人物、群聊、规律、修正）
  → 自动清理导出文件
```

## 前置条件

- Windows 10/11
- Python 3.9+
- 微信 PC 端 4.x（保持登录）
- WeChatDataAnalysis（安装方法见下）

### 安装 WeChatDataAnalysis

本项目依赖 [WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) 的后端 API 来自动导出聊天记录。只需安装 EXE，不需要下载源码。安装位置随意，不影响使用。

1. 打开 [Release 页面（最新版）](https://github.com/LifeArchiveProject/WeChatDataAnalysis/releases/latest)
2. 下载 `WeChatDataAnalysis.Setup.<version>.exe` 并运行安装
3. 安装完成后启动 WeChatDataAnalysis
4. 首次使用需要在 UI 中手动解密一次数据库（后续自动复用密钥）

> 如果 Windows 弹出"未知发布者"或"更多信息"提示，确认下载来源为上述 Release 页面后选择"仍要运行"即可。

## 快速开始

### 1. 安装依赖

```bash
cd wechat-daily
pip install -r requirements.txt
```

### 2. 配置

项目不包含 `config.yaml`，需要从模板创建：

```bash
cp config.yaml.example config.yaml
```

然后编辑 `config.yaml`，填入你的信息：

| 必填项 | 说明 | 获取方式 |
|--------|------|----------|
| `ai.api_key` | AI 平台的 API Key | [platform.deepseek.com](https://platform.deepseek.com) 注册获取 |
| `notion.api_key` | Notion Integration Secret | [notion.so/my-integrations](https://www.notion.so/my-integrations) 创建 |
| `wechat.my_nickname` | 你的微信昵称 | 微信个人信息页查看，必须完全一致 |

其他字段按注释说明修改即可，`daily_summary_db_id` 和 `todo_tasks_db_id` 在第 3 步 setup 后填入。

`memory.yaml` 不需要手动创建，首次运行后 AI 会自动生成。如果想预填个人信息加速冷启动，可以：

```bash
cp memory.yaml.example memory.yaml
```

然后填入学校、身份等基本信息。

### 3. 首次设置 Notion

1. 在 [notion.so/my-integrations](https://www.notion.so/my-integrations) 创建 Integration，复制 Secret
2. 在 Notion 中创建一个空白页面，连接该 Integration（页面右上角 `···` → `Connections`）
3. 运行：

```bash
python main.py --setup
```

4. 按提示输入页面 ID，将输出的数据库 ID 填入 `config.yaml`

### 4. 运行

确保 WeChatDataAnalysis 后端和微信 PC 端都在运行，然后：

```bash
python main.py
```

## 使用方法

```bash
python main.py                       # 处理今天（全自动：导出→分析→写入Notion）
python main.py --date 2026-04-01     # 处理指定日期
python main.py --backfill 7          # 补跑最近 7 天
python main.py --test                # 测试模式（不调用 AI 和 Notion）
python main.py --setup               # 首次设置 Notion 数据库

python auto_export.py                # 仅导出今天的聊天记录
python auto_export.py --date 2026-04-01  # 仅导出指定日期
```

## 定时任务

右键 `install_scheduler.bat` → **以管理员身份运行**，创建每天 23:00 自动执行的 Windows 定时任务。

需要确保定时触发时：
- WeChatDataAnalysis 后端在运行（可设为开机自启）
- 微信 PC 端保持登录

## 项目结构

```
wechat-daily/
├── config.yaml              # 配置（API Keys、过滤规则等）
├── memory.yaml              # 用户画像 & 持久记忆（AI 自动更新，可手动编辑）
├── main.py                  # 主入口（全自动流程）
├── auto_export.py           # 调用 WeChatDataAnalysis API 自动导出
├── extractor.py             # 从导出的 JSON 中读取聊天记录
├── processor.py             # 消息清洗、过滤、排序
├── ai_analyzer.py           # AI 调用（总结 + 任务提取 + 反思）
├── memory_manager.py        # 记忆管理（加载、注入 prompt、合并更新）
├── notion_writer.py         # Notion 写入（含去重和更新逻辑）
├── prompts/                 # AI Prompt 模板（可自定义）
│   ├── daily_summary.txt    # 每日总结 prompt
│   ├── task_extraction.txt  # 任务提取 prompt
│   └── reflection.txt       # 反思 prompt（用于更新记忆）
├── export/                  # 导出的聊天记录（处理后自动清理）
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
| `max_messages_per_chat` | 每个对话最多取多少条消息（默认 200） |
| `min_messages_threshold` | 消息少于此数的对话会被跳过（默认 3） |
| `ignored_msg_types` | 跳过的消息类型（默认 system/voip/emoji） |

### AI Prompt 模板（prompts/）

- `daily_summary.txt` — 控制每日总结的格式、侧重点、字数
- `task_extraction.txt` — 控制任务提取规则：只提取"我"的任务，自动合并同类项，包含时间和地点

### 任务提取规则

当前 prompt 的核心逻辑：
- 只提取**我（config 中的 my_nickname）** 需要做的事
- 排除：别人的任务、向别人报备的事、已完成的动作、琐碎小事
- 同一件事的多条消息会合并为一个任务
- 每个任务包含：标题、详情、截止日期、时间、地点、优先级、来源

### 记忆系统（memory.yaml）

AI 会自动积累对你的了解，用得越久越准确。

**自动记录的内容：**
- 常联系的人和关系（同学、同事、队友...）
- 群聊的性质（足球队群、同学群、工作群...）
- 周期性事件（每周训练、定期开会...）
- 行为规律（作息、习惯）
- 任务提取的修正规则（从错误中学习）

**工作原理：**
每次运行后，AI 会额外做一次"反思"：对比聊天内容和生成的结果，提取新的人物关系、规律等，自动合并到 `memory.yaml`。下次运行时，这些记忆会注入到 prompt 中。

**手动编辑：**
你可以直接编辑 `memory.yaml` 来修正或补充信息：

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

**自动导出失败？**
确保 WeChatDataAnalysis 后端在运行（默认 `http://127.0.0.1:10392`），微信 PC 端在线，数据库已解密。如果后端没启动，程序会跳过导出步骤，使用 `export/` 下已有数据。

**语音消息能处理吗？**
当前只处理文字、链接、引用等文本类消息。语音会显示为 `[语音 Xs]`。

**API 费用？**
使用 DeepSeek（deepseek-chat），每天约 0.01-0.02 元人民币，一个月不到 1 元。

**隐私安全？**
数据提取和处理全在本地。只有经过过滤的聊天文本会发给 AI API。处理完成后导出文件自动删除。

**国内网络问题？**
DeepSeek 和通义千问国内直连，无需代理。Notion API 可能需要代理：
```bash
set HTTPS_PROXY=http://127.0.0.1:7890
python main.py
```

## 技术栈

- Python 3.11 / OpenAI SDK（兼容 DeepSeek、通义千问）
- WeChatDataAnalysis（微信 4.x 数据解密和导出）
- Notion API
- Windows 任务计划程序
