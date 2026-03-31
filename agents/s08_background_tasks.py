#!/usr/bin/env python3
# 测试框架：后台执行 —— 模型思考时，主线程无需阻塞等待
"""
s08_background_tasks.py - 后台任务模块

在后台线程中执行命令，任务完成后通过通知队列将结果注入对话。
每次调用 LLM 前，先清空通知队列，将后台结果交付给模型。

    主线程                          后台线程
    +-----------------+        +-----------------+
    | 智能体主循环     |        | 任务正在执行    |
    | ...             |        | ...             |
    | [调用 LLM] <---+------- | enqueue(结果)  |
    |  ^ 清空队列     |        +-----------------+
    +-----------------+

    时间轴示意：
    Agent ----[启动任务A]----[启动任务B]----[做其他事情]----
                  |               |
                  v               v
              [A 在跑]        [B 在跑]        （并行执行）
                  |               |
                  +--- 通知队列 --> [结果被注入对话上下文]

核心思想："发射后不管 —— 命令在后台跑，智能体不需要傻等。"
"""

import os
import subprocess
import threading
import uuid
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

# 如果设置了自定义 API 地址，则移除可能冲突的认证 Token 环境变量
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 工作目录：默认为当前目录
WORKDIR = Path(r'D:\develop\CPP\learn-claude-code')
# 创建 Anthropic 客户端（支持自定义 base_url）
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# 使用的模型 ID，从环境变量读取
MODEL = os.environ["MODEL_ID"]

# 系统提示：告知模型角色，并建议对耗时命令使用 background_run
SYSTEM = f"You are a coding agent at {WORKDIR}. Use background_run for long-running commands."


# ── 后台任务管理器：多线程执行 + 通知队列 ──
class BackgroundManager:
    def __init__(self):
        self.tasks = {}               # task_id -> {status, result, command} 任务状态表
        self._notification_queue = [] # 已完成任务的通知列表，待注入对话
        self._lock = threading.Lock() # 保护通知队列的线程锁

    def run(self, command: str) -> str:
        """在后台线程中启动命令，立即返回 task_id，不阻塞主线程。"""
        task_id = str(uuid.uuid4())[:8]  # 生成 8 位短 ID
        self.tasks[task_id] = {"status": "running", "result": None, "command": command}
        thread = threading.Thread(
            target=self._execute, args=(task_id, command), daemon=True
        )
        thread.start()
        return f"Background task {task_id} started: {command[:80]}"

    def _execute(self, task_id: str, command: str):
        """后台线程的执行体：运行子进程，捕获输出，完成后推入通知队列。"""
        try:
            r = subprocess.run(
                command, shell=True, cwd=WORKDIR,
                capture_output=True, text=True, timeout=300
            )
            output = (r.stdout + r.stderr).strip()[:50000]
            status = "completed"
        except subprocess.TimeoutExpired:
            output = "Error: Timeout (300s)"
            status = "timeout"
        except Exception as e:
            output = f"Error: {e}"
            status = "error"

        # 更新任务状态
        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["result"] = output or "(no output)"

        # 线程安全地将完成通知推入队列
        with self._lock:
            self._notification_queue.append({
                "task_id": task_id,
                "status": status,
                "command": command[:80],
                "result": (output or "(no output)")[:500],  # 通知中结果截断为 500 字符
            })

    def check(self, task_id: str = None) -> str:
        """查询单个任务状态；不传 task_id 则列出所有任务。"""
        if task_id:
            t = self.tasks.get(task_id)
            if not t:
                return f"Error: Unknown task {task_id}"
            return f"[{t['status']}] {t['command'][:60]}\n{t.get('result') or '(running)'}"
        lines = []
        for tid, t in self.tasks.items():
            lines.append(f"{tid}: [{t['status']}] {t['command'][:60]}")
        return "\n".join(lines) if lines else "No background tasks."

    def drain_notifications(self) -> list:
        """取出并清空所有待处理的完成通知，返回通知列表。"""
        with self._lock:
            notifs = list(self._notification_queue)
            self._notification_queue.clear()
        return notifs


# 全局后台任务管理器实例
BG = BackgroundManager()


# ── 工具实现 ──

def safe_path(p: str) -> Path:
    """将相对路径解析为绝对路径，并确保不会越出工作目录。"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"路径越出工作区范围：{p}")
    return path

def run_bash(command: str) -> str:
    """同步执行 Shell 命令（阻塞），屏蔽危险命令，超时 120 秒。"""
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
    """读取文件内容，可选限制返回行数。"""
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    """将内容写入文件，自动创建不存在的父目录。"""
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    """精确替换文件中的指定文本（只替换第一处匹配）。"""
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# 工具名称 -> 处理函数 的映射表
TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"]),
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "background_run":   lambda **kw: BG.run(kw["command"]),    # 异步启动后台任务
    "check_background": lambda **kw: BG.check(kw.get("task_id")),  # 查询后台任务状态
}

# 提供给 LLM 的工具定义列表（符合 Anthropic API 格式）
TOOLS = [
    {"name": "bash", "description": "Run a shell command (blocking).",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "background_run", "description": "Run command in background thread. Returns task_id immediately.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status. Omit task_id to list all.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
]


def agent_loop(messages: list):
    """
    智能体主循环：每次调用 LLM 前先注入后台任务通知，
    然后持续执行工具调用，直到模型停止发出工具请求。
    """
    while True:
        # 每轮开始前清空后台通知队列，将已完成的任务结果注入对话上下文
        notifs = BG.drain_notifications()
        if notifs and messages:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
            )
            # 以特殊标签包裹，让模型明确感知这是后台任务的异步结果
            messages.append({"role": "user", "content": f"<background-results>\n{notif_text}\n</background-results>"})

        # 调用 LLM，获取模型回复
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 将模型回复追加到消息历史
        messages.append({"role": "assistant", "content": response.content})

        # 如果模型不再调用工具，说明本轮任务完成，退出循环
        if response.stop_reason != "tool_use":
            return

        # 遍历模型回复中的所有工具调用块，依次执行
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                # 打印工具调用名称和输出（截断显示）
                print(f"> {block.name}:")
                print(str(output)[:200])
                # 收集工具调用结果，用于返回给模型
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})

        # 将本轮所有工具结果追加到消息历史
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []  # 维护跨轮次的对话历史
    while True:
        try:
            # 显示彩色提示符，等待用户输入
            query = input("\033[36ms08 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            # 用户按 Ctrl+C 或 Ctrl+D 时退出
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        # 将用户输入追加到历史并进入智能体循环
        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印模型最终的文本回复
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()