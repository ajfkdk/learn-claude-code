#!/usr/bin/env python3
"""
AgentCore.py - Import Aggregation Layer

    +------------------------------------------------------------------+
    |                        FULL AGENT                                 |
    |                                                                   |
    |  System prompt (s05 skills, task-first + optional todo nag)      |
    |                                                                   |
    |  Before each LLM call:                                            |
    |  +--------------------+  +------------------+  +--------------+  |
    |  | Microcompact (s06) |  | Drain bg (s08)   |  | Check inbox  |  |
    |  | Auto-compact (s06) |  | notifications    |  | (s09)        |  |
    |  +--------------------+  +------------------+  +--------------+  |
    |                                                                   |
    |  Tool dispatch (s02 pattern):                                     |
    |  +--------+----------+----------+---------+-----------+          |
    |  | bash   | read     | write    | edit    | TodoWrite |          |
    |  | task   | load_sk  | compress | bg_run  | bg_check  |          |
    |  | t_crt  | t_get    | t_upd    | t_list  | spawn_tm  |          |
    |  | list_tm| send_msg | rd_inbox | bcast   | shutdown  |          |
    |  | plan   | idle     | claim    |         |           |          |
    |  +--------+----------+----------+---------+-----------+          |
    |                                                                   |
    |  Subagent (s04):  spawn -> work -> return summary                 |
    |  Teammate (s09):  spawn -> work -> idle -> auto-claim (s11)      |
    |  Shutdown (s10):  request_id handshake                            |
    |  Plan gate (s10): submit -> approve/reject                        |
    +------------------------------------------------------------------+

    REPL commands: /compact /tasks /team /inbox
"""

from config import *
from tools import *
from managers import *
from config import SYSTEM
from typing import Optional, Protocol

# Explicit re-exports for backward compatibility
from managers import TODO, TASK_MGR, BG, BUS, TEAM, SKILLS


class EventSink(Protocol):
    def on_text(self, text: str) -> None:
        ...

    def on_event(self, text: str) -> None:
        ...


class StdoutSink:
    def on_text(self, text: str) -> None:
        print(text, end="", flush=True)

    def on_event(self, text: str) -> None:
        print(text)


def _resolve_sink(sink: Optional[EventSink]) -> EventSink:
    return sink or StdoutSink()

# === SECTION: subagent (s04) ===
# 独立子代理，用于隔离的探索或工作任务

def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    """
    生成一个独立的子代理来执行任务
    子代理使用基础工具集（TOOLS / TOOL_HANDLERS）
    """
    _ = agent_type
    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    # 最多30轮对话循环
    for _ in range(30):
        resp = client.messages.create(model=MODEL, messages=sub_msgs, tools=TOOLS, max_tokens=8000)
        sub_msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                h = TOOL_HANDLERS.get(b.name, lambda **kw: "Unknown tool")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(h(**b.input))[:50000]})
        sub_msgs.append({"role": "user", "content": results})
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"


# === SECTION: shutdown_protocol (s10) ===
# 关闭协议：请求ID握手机制

def handle_shutdown_request(teammate: str) -> str:
    """向指定成员发送关闭请求"""
    req_id = str(uuid.uuid4())[:8]
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    BUS.send("lead", teammate, "Please shut down.", "shutdown_request", {"request_id": req_id})
    return f"Shutdown request {req_id} sent to '{teammate}'"


# === SECTION: plan_approval (s10) ===
# 计划审批：lead审批成员的计划

def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    """审批或拒绝成员的计划"""
    req = plan_requests.get(request_id)
    if not req: return f"Error: Unknown plan request_id '{request_id}'"
    req["status"] = "approved" if approve else "rejected"
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    return f"Plan {req['status']} for '{req['from']}'"


# === SECTION: tool_dispatch (s02) ===
# 更新 tools.py 中的 manager 引用，连接实际的管理器实例

import json
import uuid

TOOL_HANDLERS.update({
    "task":             lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
    "task_create":      lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),
    "task_get":         lambda **kw: TASK_MGR.get(kw["task_id"]),
    "task_update":      lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("remove_blocked_by")),
    "task_list":        lambda **kw: TASK_MGR.list_all(),
    "compress":         lambda **kw: "Compressing...",
})

FULL_TOOL_HANDLERS.update(TOOL_HANDLERS)

FULL_TOOL_HANDLERS.update({
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),
    "spawn_teammate":   lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates":   lambda **kw: TEAM.list_all(),
    "send_message":     lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox":       lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast":        lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),
    "plan_approval":    lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
    "claim_task":       lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),
})


# === SECTION: agent_loop ===
# 主代理循环：压缩 -> 后台通知 -> 收件箱 -> LLM调用 -> 工具执行

def agent_loop(messages: list, sink: Optional[EventSink] = None):
    """
    主循环流程：
    1. 微压缩：清除旧工具结果
    2. 自动压缩：token超过阈值时总结对话
    3. 后台通知：处理后台任务完成通知
    4. 收件箱：处理来自团队的消息
    5. LLM调用：获取下一步指令
    6. 工具执行：执行LLM请求的工具
    7. 待办提醒：如果待办项未更新则提醒
    """
    rounds_without_todo = 0
    out = _resolve_sink(sink)
    while True:
        # s06: 压缩管道
        microcompact(messages)
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            out.on_event("[auto-compact triggered]")
            messages[:] = auto_compact(messages)
        # s08: 处理后台任务通知
        notifs = BG.drain()
        if notifs:
            txt = "\n".join(f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs)
            messages.append({"role": "user", "content": f"<background-results>\n{txt}\n</background-results>"})
        # s10: 检查lead收件箱
        inbox = BUS.read_inbox("lead")
        if inbox:
            messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"})
        # LLM调用（流式输出）
        full_content = []
        with client.messages.stream(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=FULL_TOOLS, max_tokens=8000,
        ) as stream:
            for chunk in stream.text_stream:
                out.on_text(chunk)
                full_content.append(chunk)
            message = stream.get_final_message()
            stop_reason = message.stop_reason
        out.on_text("\n")
        # 组装完整响应用于工具执行
        content_text = "".join(full_content)
        from anthropic.types import TextBlock
        response_content = [TextBlock(type="text", text=content_text)]
        messages.append({"role": "assistant", "content": response_content})
        if stop_reason != "tool_use":
            return
        # 获取完整响应对象
        response = stream.get_final_message()
        # 工具执行
        results = []
        used_todo = False
        manual_compress = False
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compress":
                    manual_compress = True
                tool_input_preview = ""
                if block.name == "bash" and isinstance(block.input, dict):
                    tool_input_preview = block.input.get("command", "")
                elif block.name in ("read_file", "write_file", "edit_file") and isinstance(block.input, dict):
                    tool_input_preview = block.input.get("path", "")
                out.on_event(f"[tool] using {block.name}" + (f": {tool_input_preview}" if tool_input_preview else ""))
                handler = FULL_TOOL_HANDLERS.get(block.name)
                try:
                    tool_output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    tool_output = f"Error: {e}"
                out.on_event(f"> {block.name}:")
                out.on_event(str(tool_output)[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(tool_output)})
                if block.name == "TodoWrite":
                    used_todo = True
        # s03: 待办提醒（仅当待办工作流活跃时）
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
        messages.append({"role": "user", "content": results})
        # s06: 手动压缩
        if manual_compress:
            out.on_event("[manual compact]")
            messages[:] = auto_compact(messages)
            return


def execute_repl_command(query: str, history: list, sink: Optional[EventSink] = None) -> bool:
    out = _resolve_sink(sink)
    cmd = query.strip()

    if cmd in ("/exit", "/quit"):
        return False

    if cmd == "/help":
        out.on_event("Commands: /help /compact /tasks /team /inbox /exit")
    elif cmd == "/compact":
        history[:] = auto_compact(history)
        out.on_event("[manual compact]")
    elif cmd == "/tasks":
        out.on_event(TASK_MGR.list_all())
    elif cmd == "/team":
        out.on_event(TEAM.list_all())
    elif cmd == "/inbox":
        out.on_event(json.dumps(BUS.read_inbox("lead"), indent=2))
    else:
        out.on_event(f"Unknown command: {cmd}")
    return True


def submit_turn(history: list, query: str, sink: Optional[EventSink] = None) -> bool:
    text = query.strip()
    if not text:
        return True
    if text.startswith("/"):
        return execute_repl_command(text, history, sink=sink)
    history.append({"role": "user", "content": text})
    agent_loop(history, sink=sink)
    return True


# === SECTION: repl ===
# 交互式REPL循环（已迁移到 Textual TUI）

if __name__ == "__main__":
    from tui import MyAgentApp
    app = MyAgentApp()
    app.run()
