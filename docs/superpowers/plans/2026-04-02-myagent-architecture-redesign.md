# MyAgent 架构最小化重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 将 852 行 AgentCore.py 拆分为 4 个模块（config/tools/managers/agentcore），消除单文件膨胀，不重写逻辑。

**Architecture：** 按功能自然分组：配置常量层、纯函数工具层、Manager 实例层、入口粘合层。保持全局单例模式，线性调用依赖，向后兼容所有现有 import。

**Tech Stack：** Python 3, 无新增依赖

---

## 文件结构

```
MyAgent/
├── config.py      # 新建：所有硬编码配置值
├── tools.py       # 新建：base_tools + TOOL_HANDLERS + TOOLS
├── managers.py    # 新建：所有 Manager 类 + 全局实例
└── agentcore.py   # 修改：保留 agent_loop + 汇总 import
```

---

## 重构顺序

4 个任务，每个任务是"整体创建/改写一个文件"而非 TDD 循环（重构无新逻辑，无需测试覆盖）。

---

### Task 1: 创建 config.py

**文件：**
- 创建: `MyAgent/config.py`

- [ ] **Step 1: 创建 config.py**

从 AgentCore.py 迁出所有配置常量（行 49-76），保持完全一致的赋值语句。

```python
import os
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path(r'/')
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"

TOKEN_THRESHOLD = 100000
POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}
```

- [ ] **Step 2: 提交**

```bash
git add MyAgent/config.py
git commit -m "refactor: 迁出配置常量到 config.py"
```

---

### Task 2: 创建 tools.py

**文件：**
- 创建: `MyAgent/tools.py`

- [ ] **Step 1: 创建 tools.py**

从 AgentCore.py 迁出 base_tools 函数（行 78-131）、TOOL_HANDLERS（行 682-706）、TOOLS 定义列表（行 709-756）。

注意：`safe_path` 依赖 `WORKDIR`，需 `from config import WORKDIR`。

```python
from config import WORKDIR
import subprocess
from pathlib import Path

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
```

TOOL_HANDLERS 和 TOOLS 列表从 AgentCore.py 原样迁出（内容过长，平移不做修改）。

- [ ] **Step 2: 提交**

```bash
git add MyAgent/tools.py
git commit -m "refactor: 迁出 tools 到 tools.py"
```

---

### Task 3: 创建 managers.py

**文件：**
- 创建: `MyAgent/managers.py`

- [ ] **Step 1: 创建 managers.py**

从 AgentCore.py 迁出所有 Manager 类和全局实例：

- `TodoManager`（行 137-173）
- `SkillLoader`（行 227-255）
- `microcompact` + `estimate_tokens` + `auto_compact`（行 261-302）
- `TaskManager`（行 308-380）
- `BackgroundManager`（行 386-423）
- `MessageBus`（行 429-458）
- `shutdown_requests` + `plan_requests`（行 463-464）
- `TeammateManager`（行 470-634）
- 全局实例初始化（行 640-645）

注意：`SkillLoader.__init__` 依赖 `SKILLS_DIR`（from config），`TeammateManager._loop` 中用到 `client`（from config）。

```python
from config import SKILLS_DIR, TEAM_DIR, TASKS_DIR, INBOX_DIR, WORKDIR, client, MODEL, TOKEN_THRESHOLD, POLL_INTERVAL, IDLE_TIMEOUT, VALID_MSG_TYPES
# ... 所有 import
```

全局实例（末尾）：
```python
TODO = TodoManager()
SKILLS = SkillLoader(SKILLS_DIR)
TASK_MGR = TaskManager()
BG = BackgroundManager()
BUS = MessageBus()
TEAM = TeammateManager(BUS, TASK_MGR)
```

- [ ] **Step 2: 提交**

```bash
git add MyAgent/managers.py
git commit -m "refactor: 迁出 managers 到 managers.py"
```

---

### Task 4: 重构 agentcore.py

**文件：**
- 修改: `MyAgent/agentcore.py`

- [ ] **Step 1: 重写 agentcore.py**

保留：
- 文件头注释（行 1-37）
- `SYSTEM` 提示词（行 650-653）
- `agent_loop` 函数（行 762-843）
- `handle_shutdown_request`（行 659-664）
- `handle_plan_review`（行 669-676）
- `__main__` 入口（行 849-852）

替换为 import 汇总（开头）：

```python
from config import *
from tools import *
from managers import *
from config import SYSTEM  # SYSTEM 在 config.py 末尾追加
```

替换文件头注释的模块说明图（更新为新的文件结构）。

- [ ] **Step 2: 运行验证**

```bash
cd MyAgent && python -c "from agentcore import agent_loop, TODO, TASK_MGR, BG, BUS, TEAM, SKILLS; print('import ok')"
```

预期：输出 `import ok`，无报错。

- [ ] **Step 3: 提交**

```bash
git add MyAgent/agentcore.py
git commit -m "refactor: agentcore.py 改为 import 汇总层"
```

---

## 验证步骤

全部完成后，运行以下验证：

```bash
# 1. import 兼容性
cd MyAgent && python -c "from agentcore import agent_loop, TODO, TASK_MGR, BG, BUS, TEAM, SKILLS; print('backward compat ok')"

# 2. TUI 启动
cd MyAgent && timeout 3 python -c "from tui import MyAgentApp; app = MyAgentApp()" 2>&1 || true
# 预期：TUI 正常启动，3秒后超时退出
```

---

## 风险与回滚

- **风险：** `from config import *` 可能污染命名空间。应对：显式 import 需要的符号。
- **回滚：** `git reset --hard HEAD~4` 可回退到重构前状态（4个 commit）。
