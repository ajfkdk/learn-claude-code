# MyAgent 架构最小化重构设计

> 日期：2026-04-02
> 状态：已批准

## 背景

AgentCore.py 共 852 行，混合了 18 个功能块（配置、工具、Manager、LLM调用、主循环、团队协作等）。代码本身运行良好，重构目的是**物理分离**而非逻辑重写。

## 目标

按功能自然分组，切分为 4 个有清晰边界的模块，消除单文件膨胀，不引入额外抽象。

## 文件结构

```
MyAgent/
├── config.py      # 所有硬编码配置值
├── tools.py       # 纯函数 + TOOL_HANDLERS + TOOLS 定义
├── managers.py    # 所有 Manager 类 + 全局实例
└── agentcore.py   # agent_loop 入口 + repl 入口（汇总导出）
```

## 各文件职责

### config.py

```python
# 配置值抽出来，集中管理
WORKDIR = Path(r'/')
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

TOKEN_THRESHOLD = 100000
POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"

VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}
```

不包含任何逻辑，只有数据和路径常量。

### tools.py

```python
# 纯函数，无状态
def safe_path(p: str) -> Path: ...
def run_bash(command: str) -> str: ...
def run_read(path: str, limit: int = None) -> str: ...
def run_write(path: str, content: str) -> str: ...
def run_edit(path: str, old_text: str, new_text: str) -> str: ...

# 工具分发映射（引用 managers 中的实例）
TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"]),
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit")),
    ...
}

# 供 LLM 使用的工具定义列表
TOOLS = [...]
```

### managers.py

```python
# 所有 Manager 类
class TodoManager: ...
class TaskManager: ...
class BackgroundManager: ...
class MessageBus: ...
class SkillLoader: ...
class TeammateManager: ...

# shutdown/plan 全局状态
shutdown_requests = {}
plan_requests = {}

# 全局单例实例
TODO = TodoManager()
SKILLS = SkillLoader(SKILLS_DIR)
TASK_MGR = TaskManager()
BG = BackgroundManager()
BUS = MessageBus()
TEAM = TeammateManager(BUS, TASK_MGR)
```

注意：`SKILLS` 依赖 `SKILLS_DIR`（在 config.py 中），所以 managers.py 需要 `from config import SKILLS_DIR, TEAM_DIR, TASKS_DIR, INBOX_DIR`。

### agentcore.py

保留的内容（最少改动）：

```python
from config import *
from tools import *
from managers import *

# agent_loop - 核心逻辑不动，只改 import 来源
def agent_loop(messages: list):
    ...

# repl 入口
if __name__ == "__main__":
    from tui import MyAgentApp
    app = MyAgentApp()
    app.run()
```

## 向后兼容

- `from AgentCore import agent_loop, TODO, TASK_MGR, BG, BUS, TEAM, SKILLS` 等现有 import 全部继续有效
- tui.py 中的 `from AgentCore import agent_loop` 不需要修改

## 重构顺序

1. 创建 `config.py`，迁出所有配置常量
2. 创建 `tools.py`，迁出 base_tools + TOOL_HANDLERS + TOOLS
3. 创建 `managers.py`，迁出所有类 + 全局实例
4. 修改 `agentcore.py`，改为 import 汇总，验证功能不变
5. 不改动 `tui.py`

## 约束

- **不改逻辑** — 只搬动代码，不重写任何函数体
- **保持 import 兼容** — agentcore.py 重新导出所有公共符号
- **不引入新依赖** — 不增加新的 pip 包
- **不拆分 Manager** — 全部 Manager 类放 managers.py，不单独成文件（当前体量不需要）
