# MyAgent Textual REPL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 REPL 迁移到 Textual TUI，支持 Ctrl+G 打开 notepad.exe 编辑器。

**Architecture:** 新建 `tui.py`，Textual App 封装 REPL 逻辑，agent_loop 保持不变。消息通过 `MessageService` 共享状态。

**Tech Stack:** Textual, Python 标准库

---

## File Map

- **Create:** `MyAgent/tui.py` — Textual REPL 应用
- **Modify:** `MyAgent/AgentCore.py` — REPL 入口改为启动 Textual App

---

## Task 1: 创建 Textual REPL 应用骨架

**Files:**
- Create: `MyAgent/tui.py`

- [ ] **Step 1: 安装 Textual**

```bash
pip install textual
```

- [ ] **Step 2: 创建 tui.py 骨架**

```python
from textual.app import App, ComposeResult
from textual.widgets import Header, TextArea

class MyAgentApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    # input-area {
        height: 3;
        dock: bottom;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield TextArea(id="input-area")

    def on_mount(self) -> None:
        pass
```

- [ ] **Step 3: 验证骨架运行**

```bash
cd MyAgent && python -c "from tui import MyAgentApp; app = MyAgentApp(); app.run()"
```

预期：Textual 窗口打开，底部有输入框

- [ ] **Step 4: 提交**

```bash
git add MyAgent/tui.py
git commit -m "feat: scaffold Textual REPL app"
```

---

## Task 2: 实现 Ctrl+G 打开 notepad.exe

**Files:**
- Modify: `MyAgent/tui.py`

- [ ] **Step 1: 添加 Ctrl+G 处理器**

在 `MyAgentApp` 中添加：

```python
from textual.binding import Binding
from textual.widgets import TextArea
import subprocess
import tempfile
import os

BINDINGS = [
    Binding("ctrl+g", "open_editor", "Edit", show=True),
]

class MyAgentApp(App):
    BINDINGS = BINDINGS

    def action_open_editor(self) -> None:
        """Ctrl+G: 打开 notepad.exe 编辑器"""
        input_widget = self.query_one("#input-area", TextArea)
        current_text = input_widget.text

        # 写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(current_text)
            temp_path = f.name

        # 调用 notepad.exe
        subprocess.run(["notepad.exe", temp_path], check=True)

        # 读取内容返回输入框
        with open(temp_path, 'r') as f:
            new_text = f.read()
        os.unlink(temp_path)

        input_widget.text = new_text
```

- [ ] **Step 2: 测试 Ctrl+G**

运行 app，输入一些文字，按 Ctrl+G，确认 notepad.exe 打开，关闭后内容回到输入框。

- [ ] **Step 3: 提交**

```bash
git add MyAgent/tui.py
git commit -m "feat: Ctrl+G opens notepad.exe editor"
```

---

## Task 3: 集成 agent_loop 和消息展示

**Files:**
- Modify: `MyAgent/tui.py`
- Modify: `MyAgent/AgentCore.py`

- [ ] **Step 1: 在 Textual 中运行 agent_loop**

改造 `tui.py`，让 Textual 的输入循环调用 `agent_loop`：

```python
from textual.events import Key
import asyncio

class MyAgentApp(App):
    def __init__(self):
        super().__init__()
        self.history = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield TextArea(id="input-area")

    async def on_key(self, event: Key) -> None:
        if event.key == "enter":
            input_widget = self.query_one("#input-area", TextArea)
            query = input_widget.text.strip()
            if not query:
                return

            # 处理 REPL 命令
            if query.startswith("/"):
                self.handle_command(query)
                input_widget.text = ""
                return

            # 加入历史
            self.history.append({"role": "user", "content": query})
            input_widget.text = ""

            # 调用 agent_loop
            from AgentCore import agent_loop
            agent_loop(self.history)

            # 打印结果
            last = self.history[-1]["content"]
            if isinstance(last, list):
                for block in last:
                    if hasattr(block, "text"):
                        print(block.text)
```

- [ ] **Step 2: 修改 AgentCore.py 入口**

将 `AgentCore.py` 底部 REPL 入口改为：

```python
if __name__ == "__main__":
    from tui import MyAgentApp
    app = MyAgentApp()
    app.run()
```

- [ ] **Step 3: 验证完整流程**

```bash
cd MyAgent && python AgentCore.py
```

1. 输入 `say hello`，验证 streaming 输出
2. 输入多行内容，按 Ctrl+G，验证 notepad 打开
3. `/tasks` 命令验证
4. Ctrl+C 中断

- [ ] **Step 4: 提交**

```bash
git add MyAgent/AgentCore.py MyAgent/tui.py
git commit -m "feat: integrate Textual REPL with agent_loop"
```

---

## Task 4: 添加消息历史展示（可选，增强体验）

**Files:**
- Modify: `MyAgent/tui.py`

- [ ] **Step 1: 添加 ScrollableLog 展示历史**

```python
from textual.widgets import ScrollableLog

class MyAgentApp(App):
    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableLog(id="message-log")
        yield TextArea(id="input-area")
```

- [ ] **Step 2: agent_loop 内部消息打印重定向到 ScrollableLog**

（此任务为可选增强，如时间不够可跳过）

---

## 验证清单

- [ ] Textual app 正常启动
- [ ] Ctrl+G 打开 notepad.exe
- [ ] 关闭 notepad 后内容返回输入框
- [ ] Streaming AI 响应正常显示
- [ ] `/compact /tasks /team /inbox` 命令正常
- [ ] Ctrl+C 中断当前输入
