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

# === SECTION: subagent (s04) ===
# 独立子代理，用于隔离的探索或工作任务

def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    """
    生成一个独立的子代理来执行任务
    - Explore: 只读权限（bash + read_file）
    - general-purpose: 完整权限（bash + read/write/edit）
    """
    sub_tools = [
        {"name": "bash", "description": "Run command.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "read_file", "description": "Read file.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    ]
    # 非Explore类型的代理获得写权限
    if agent_type != "Explore":
        sub_tools += [
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        ]
    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"]),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }
    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    # 最多30轮对话循环
    for _ in range(30):
        resp = client.messages.create(model=MODEL, messages=sub_msgs, tools=sub_tools, max_tokens=8000)
        sub_msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(h(**b.input))[:50000]})
        sub_msgs.append({"role": "user", "content": results})
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"


# === SECTION: system_prompt ===
# 系统提示词，定义代理的角色和能力

SYSTEM = f"""You are a coding agent at {WORKDIR}. Use tools to solve tasks.
Prefer task_create/task_update/task_list for multi-step work. Use TodoWrite for short checklists.
Use task for subagent delegation. Use load_skill for specialized knowledge.
Skills: {SKILLS.descriptions()}"""


# === SECTION: shutdown_protocol (s10) ===
# 关闭协议：请求ID握手机制

shutdown_requests = {}
plan_requests = {}


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
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),
    "task":             lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
    "task_create":      lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),
    "task_get":         lambda **kw: TASK_MGR.get(kw["task_id"]),
    "task_update":      lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("remove_blocked_by")),
    "task_list":        lambda **kw: TASK_MGR.list_all(),
    "spawn_teammate":   lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates":   lambda **kw: TEAM.list_all(),
    "send_message":     lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox":       lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast":        lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),
    "plan_approval":    lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
    "claim_task":       lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),
    "compress":         lambda **kw: "Compressing...",
})


# === SECTION: agent_loop ===
# 主代理循环：压缩 -> 后台通知 -> 收件箱 -> LLM调用 -> 工具执行

def agent_loop(messages: list):
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
    while True:
        # s06: 压缩管道
        microcompact(messages)
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
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
            tools=TOOLS, max_tokens=8000,
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
                full_content.append(chunk)
            message = stream.get_final_message()
            stop_reason = message.stop_reason
        print()  # 换行
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
                print(f"[tool] using {block.name}" + (f": {tool_input_preview}" if tool_input_preview else ""))
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                print(f"> {block.name}:")
                print(str(output)[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                if block.name == "TodoWrite":
                    used_todo = True
        # s03: 待办提醒（仅当待办工作流活跃时）
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
        messages.append({"role": "user", "content": results})
        # s06: 手动压缩
        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages)
            return


# === SECTION: repl ===
# 交互式REPL循环（已迁移到 Textual TUI）

if __name__ == "__main__":
    from tui import MyAgentApp
    app = MyAgentApp()
    app.run()
