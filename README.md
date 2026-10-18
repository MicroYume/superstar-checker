# Superstar Checker

<p align="center">
  <strong>基于纯 HTTP 架构的超星学习通 MCP 服务端与命令行工具</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Protocol-MCP%201.0-brightgreen" alt="MCP Protocol">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platform">
</p>

---

**Superstar Checker** 是一个轻量级、高性能的超星学习通（泛雅平台）MCP（Model Context Protocol）工具集。无须运行重量级的无头浏览器（Puppeteer/Selenium），完全基于纯 HTTP 逆向协议实现，可无缝接入 **Cursor**、**Claude Desktop**、**Antigravity** 等各类支持 MCP 的 AI 客户端与编程助手。

---

## 🌟 核心特性

- ⚡ **纯 HTTP 驱动**：摒弃浏览器自动化框架，内存占用低（通常 < 50MB）、极速启动、资源消耗小。
- 🤖 **原生 MCP 支持**：遵循标准 FastMCP 规范，与 AI 助手无缝联动，支持自然语言查询课程、章节与答题辅助。
- 📦 **提供开箱即用便携版**：Windows 预编译便携包**内置独立 Python 运行环境与全部依赖**，免安装 Python、免配置虚拟环境，解压即用。
- 🔐 **安全与隐私优先**：
  - **双模式登录**：支持密码登录与扫码登录。终端密码输入支持逐字符 `*` 掩码遮罩，**凭据绝不流向 AI 上下文或日志**。
  - **多形态扫码**：支持浏览器扫码、Win32 原生像素窗口扫码（无字体缩放失真）、终端半块字符（Unicode Half-block）与纯 ASCII 模式。
  - **多账号确认**：检测到已有登录会话时强制确认切换，防止误操作覆盖会话。
- 📚 **全生命周期课程管理**：
  - 一键列出全部课程、班级及 `cpi` 标识。
  - 递归解析章节层级树，精准统计任务点（文档、视频、测验）完成进度。
- ⏱️ **合规视频学习引擎**：
  - 遵循学习通平台反作弊机制，视频严格按 **1 倍速真实时长**推进心跳并上报进度，杜绝秒过封号风险。
  - 文档/阅读任务秒级打卡。
  - 支持后台线程播放、进度断点续传与主动中断（`study-start` / `study-stop`）。
- 📝 **作业与随堂测验解析**：
  - **自动解字体反爬（`cxsecret`）**：内置字形轮廓哈希解码器，自动还原被混淆生僻字替换的题干与选项。
  - 题型全面支持：单选题、多选题、判断题、填空题（多空）、简答题等。
  - **Human-in-the-Loop 安全暂存**：仅提供答案暂存接口（`save_homework`），**坚决不执行自动提交**。用户必须在官方客户端核对后手动提交，保障学术诚信与答题安全。
- 📥 **通知与课件直链**：
  - 结构化提取收件箱通知（自动关联作业/考试截止时间）。
  - 提取章节内 PPT、PDF、视频等教学资料的直接下载直链。

---

## 🚀 两种版本与安装方式

本项目提供两种使用方式，你可以根据需求选择：

| 比较维度 | 🟢 开箱即用便携版（推荐普通用户） | 🔵 源码开发版（推荐开发者 / 跨平台用户） |
| :--- | :--- | :--- |
| **适用人群** | 不需要/不熟悉 Python 开发环境的普通用户 | 开发者、二次开发人员、macOS / Linux 用户 |
| **Python 依赖** | **完全不需要安装 Python**（包内已内置 Python 3.12 独立运行时） | 需要本地安装 Python 3.10+ |
| **环境配置** | **零配置**，解压即可直接运行 | 需要创建虚拟环境并执行 `pip install -e .` |
| **下载方式** | 前往 GitHub [Releases](../../releases) 下载 `Superstar_Checker_portable_*.zip` | 使用 `git clone` 克隆本仓库源码 |

---

### 方案 A：Windows 开箱即用便携版（无需安装 Python）

如果你使用的是 64 位 Windows 系统，并且**不想在电脑上安装 Python、pip 或配置环境变量**，请使用便携版：

1. 前往 GitHub **[Releases](../../releases)** 页面，下载最新的便携包：`Superstar_Checker_portable_YYYY-MM-DD.zip`。
2. 将压缩包**完整解压**到任意目录（例如 `D:\Tools\Superstar_Checker_portable\`），目录结构如下：
   ```text
   Superstar_Checker_portable/
   ├── runtime/                    # 内置的 64 位 Python 独立环境与预装依赖
   ├── superstar-checker.cmd       # 便携版命令行入口
   ├── superstar-checker-mcp.cmd   # 便携版 MCP Server 启动入口
   ├── 登录超星.bat                 # 双击交互式密码登录
   ├── 扫码登录.bat                 # 双击浏览器扫码登录
   ├── 扫码登录命令行.bat            # 双击原生无失真像素窗口扫码
   ├── 启动刷课.bat                 # 启动后台学习任务
   ├── 停止刷课.bat                 # 发送停止信号并保留已学进度
   └── 退出登录.bat                 # 清除本机登录会话
   ```
3. **日常使用**：直接双击对应的 `.bat` 批处理文件即可（例如双击 `登录超星.bat` 或 `扫码登录.bat`）。
4. **接入 MCP**：在 AI 客户端配置中直接指向解压后的 `runtime\python.exe`（详见下文配置）。

---

### 方案 B：源码版安装（面向开发者 / 跨平台）

如果你希望修改源码、参与开发，或者在 **macOS / Linux** 系统上使用：

1. **环境准备**：需要本地安装 **Python 3.10 或更新版本**。
2. **克隆代码与安装依赖**：

```bash
# 克隆代码仓库
git clone https://github.com/MicroYume/Superstar_Checker.git
cd Superstar_Checker

# 创建并激活虚拟环境 (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 安装项目依赖（以可编辑模式安装）
pip install -e .
```

> **macOS / Linux 用户提示**：
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -e .
> ```

---

## 🤖 接入 MCP 客户端

将本项目作为标准 stdio MCP Server 接入你的 AI 客户端（如 **Claude Desktop**、**Cursor**、**Antigravity**、**VS Code** 等）。

在客户端的 MCP 配置文件中添加配置项：

### 1. 便携版配置（Windows 免装 Python）

在 `claude_desktop_config.json` 或 `mcp.json` 中，将路径替换为你的实际解压目录：

```json
{
  "mcpServers": {
    "superstar-checker": {
      "command": "C:\\path\\to\\Superstar_Checker_portable\\runtime\\python.exe",
      "args": ["-m", "superstar_checker.server"]
    }
  }
}
```

> 也可直接调用包内附带的启动脚本：
> ```json
> {
>   "mcpServers": {
>     "superstar-checker": {
>       "command": "C:\\path\\to\\Superstar_Checker_portable\\superstar-checker-mcp.cmd"
>     }
>   }
> }
> ```

### 2. 源码版配置

#### Windows 源码版：
```json
{
  "mcpServers": {
    "superstar-checker": {
      "command": "C:\\path\\to\\Superstar_Checker\\.venv\\Scripts\\superstar-checker-mcp.exe"
    }
  }
}
```

#### macOS / Linux 源码版：
```json
{
  "mcpServers": {
    "superstar-checker": {
      "command": "/path/to/Superstar_Checker/.venv/bin/python",
      "args": ["-m", "superstar_checker.server"]
    }
  }
}
```

---

## 💻 使用说明

### 方式一：命令行（CLI）快速操作

在便携版解压目录或源码虚拟环境中，均可直接运行命令行指令（便携版使用 `superstar-checker.cmd`，源码版使用 `superstar-checker`）：

#### 1. 登录与身份管理
```bash
# 密码交互登录（终端密码星号掩码遮罩，回车确认）
superstar-checker login

# 浏览器扫码登录
superstar-checker login --qr

# 独立原生窗口扫码（推荐 Windows，不受终端排版与字体影响）
superstar-checker login --qr --terminal

# 检查当前登录状态
superstar-checker status

# 清除本机会话
superstar-checker logout
```

#### 2. 课程与章节查询
```bash
# 查看所有已修课程（输出 course_id、class_id、cpi）
superstar-checker courses

# 查询指定课程的章节树与任务点完成情况
superstar-checker chapters <course_id> <class_id> <cpi>
```

#### 3. 任务学习与视频刷课
```bash
# 仅完成章节文档/阅读打卡任务（不播放视频）
superstar-checker study <course_id> <class_id> <cpi>

# 启动后台视频学习引擎（真实 1 倍速播放并自动续播）
superstar-checker study-start <course_id> <class_id> <cpi>

# 查看当前后台视频播放进度
superstar-checker study-status

# 停止后台视频学习（进度已自动保存至服务端）
superstar-checker study-stop
```

#### 4. 作业与测验
```bash
# 获取课程作业列表
superstar-checker homeworks <course_id> <class_id> <cpi>

# 获取指定作业题目（题干、选项、题型，已自动还原字体反爬）
superstar-checker homework <work_id>

# 扫描课程章节测验
superstar-checker quizzes <course_id> <class_id> <cpi>
```

#### 5. 资料与收件箱通知
```bash
# 获取章节附件直链（PPT、PDF 等）
superstar-checker materials <course_id> <class_id> <cpi>

# 查看收件箱通知（作业截止、开课提醒等）
superstar-checker inbox --limit 20
```

---

### 方式二：Windows 快捷批处理脚本（双击即用）

根目录下提供了一系列常用的快捷批处理工具，双击即可执行：

| 脚本文件 | 功能说明 |
| :--- | :--- |
| `登录超星.bat` | 终端交互式密码登录（星号掩码防窥） |
| `扫码登录.bat` | 打开默认浏览器扫码登录 |
| `扫码登录命令行.bat` | Win32 原生像素窗口扫码（适应任何终端字体） |
| `启动刷课.bat` | 交互式输入或传参启动课程学习引擎 |
| `停止刷课.bat` | 发送终止信号暂停刷课（保留已学进度） |
| `退出登录.bat` | 清除本机登录缓存与持久化会话 |

---

## 🛠️ MCP 工具清单

接入 MCP 后，AI 助手可直接调用以下 14 项工具：

| 工具名称 | 分类 | 说明 |
| :--- | :--- | :--- |
| `login` / `login_status` / `logout` | 认证 | 登录状态检查、引导登录与登出 |
| `get_courses` | 课程 | 获取账号下所有课程、班级及 `cpi` 参数 |
| `get_chapters` | 课程 | 查询章节层级树、任务点清单与完成状态 |
| `study_chapter` | 学习 | 同步完成小节内的文档/阅读打卡 |
| `start_studying` | 学习 | 后台非阻塞启动全课程视频实时学习（1x 倍速安全播放） |
| `study_status` / `stop_studying` | 学习 | 查询后台播放实时进度与安全暂停 |
| `get_homeworks` | 作业 | 获取作业列表、状态与截止时间 |
| `get_homework` | 作业 | 解析作业全部题目（自动解除字体反爬） |
| `get_quizzes` / `get_quiz_questions` | 作业 | 发现并读取章节内随堂测验题目 |
| `save_homework` | 作业 | 暂存题目答案（**仅暂存，绝不自动提交**） |
| `get_homework_score` | 作业 | 查询已批阅作业的得分与官方解析 |
| `get_materials` | 资源 | 批量提取章节讲义、课件（PPT/PDF）直链 |
| `get_inbox_folders` / `get_inbox_notices` | 通知 | 收件箱通知结构化提取与关键字过滤 |

---

## 🧪 测试与验证

源码版提供完善的离线单元测试与 MCP 接口冒烟测试，不依赖外部网络与真实凭据：

```bash
# 执行核心逻辑单元测试（包含加密、字体反爬、二维码排版、多域 Cookie 等）
python tests/unit_test.py

# 执行 MCP stdio 接口通讯冒烟测试
python tests/smoke_test.py
```

---

## 🔒 数据与安全说明

1. **会话凭据存储**：登录成功的 Cookie 与用户 ID 仅保存在本机用户主目录下的 `.superstar-checker/session.json`。请勿将此文件分享或上传至公开代码仓库。
2. **密码安全性**：终端密码输入直接经由本机终端驱动，不经过任何外部服务器或 AI 模型上下文。
3. **作业防作弊与学术诚信**：为避免产生未确认的提交事故，**本工具不提供任何自动提交作业功能**。所有暂存答案务必在官方学习通 App 或网页中人工核验后手动提交。

---

## ⚖️ 免责声明

1. 本项目仅供编程学习、接口逆向工程与自动化办公辅助研究，不得用于任何违反超星学习通服务条款或学校规定的商业及恶意作弊场景。
2. 用户应当知悉平台使用规范，因不当使用造成的一切后果由用户自行承担，开发者不承担任何法律责任。
3. 学习通平台接口或加密机制变更可能导致部分功能失效，欢迎社区反馈与贡献 PR。

---

## 📄 许可证与第三方致谢

- 本项目采用 **[MIT License](LICENSE)** 开源许可证，版权署名：`MicroYume`。
- **字体解密模块致谢**：字体轮廓哈希逆向算法及 `font_map_table.json` 参考并衍生自 [SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy)（遵循 Apache-2.0 / MIT 许可证）。详细版权声明请参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
