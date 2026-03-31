#!/usr/bin/env python3
# 测试框架：协议 —— 模型间的结构化握手通信
"""
s10_team_protocols.py - 团队协议模块

在 s09 团队消息基础上新增两套协议：
  1. 关闭协议（Shutdown Protocol）
  2. 计划审批协议（Plan Approval Protocol）
两者均采用相同的 request_id 关联模式。

    关闭协议 FSM：pending -> approved | rejected

    负责人（Lead）                     团队成员（Teammate）
    +---------------------+           +---------------------+
    | shutdown_request     |           |                     |
    | {                    | --------> | 收到请求            |
    |   request_id: abc    |           | 决定：是否同意关闭？ |
    | }                    |           |                     |
    +---------------------+           +---------------------+
                                              |
    +---------------------+           +-------v-------------+
    | shutdown_response    | <-------- | shutdown_response   |
    | {                    |           | {                   |
    |   request_id: abc    |           |   request_id: abc   |
    |   approve: true      |           |   approve: true     |
    | }                    |           | }                   |
    +---------------------+           +---------------------+
            |
            v
    status -> "shutdown"，线程退出

    计划审批 FSM：pending -> approved | rejected

    团队成员（Teammate）               负责人（Lead）
    +---------------------+           +---------------------+
    | plan_approval        |           |                     |
    | 提交: {plan:"..."}   | --------> | 审阅计划文本        |
    +---------------------+           | 批准或拒绝？        |
                                      +---------------------+
                                              |
    +---------------------+           +-------v-------------+
    | plan_approval_resp   | <-------- | plan_approval       |
    | {approve: true}      |           | 审阅: {req_id,      |
    +---------------------+           |   approve: true}    |
                                      +---------------------+

    追踪器：{request_id: {"target|from": name, "status": "pending|..."}}

核心思想："相同的 request_id 关联模式，应用于两个不同的业务域。"
"""

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

# 如果设置了自定义 API 地址，则移除可能冲突的认证 Token 环境变量
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path(r'D:\develop\CPP\learn-claude-code')
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"

# 系统提示：告知模型扮演团队负责人，并负责管理关闭与计划审批协议
SYSTEM = f"You are a team lead at {WORKDIR}. Manage teammates with shutdown and plan approval protocols."

# 合法的消息类型集合，用于校验
VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}

# ── 请求追踪器：通过 request_id 关联请求与响应 ──
# shutdown_requests: {request_id -> {"target": name, "status": "pending|approved|rejected"}}
# plan_requests:     {request_id -> {"from": name, "plan": text, "status": "pending|..."}}
shutdown_requests: dict = {}
plan_requests: dict = {}
_tracker_lock = threading.Lock()  # 保护两个追踪器字典的并发访问


# ── 消息总线：每位成员一个 JSONL 收件箱文件 ──
class MessageBus:
    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        """向指定成员的收件箱追加一条消息，支持附加字段（如 request_id）。"""
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {VALID_MSG_TYPES}"
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)  # 将协议字段（request_id、approve 等）合并进消息体
        inbox_path = self.dir / f"{to}.jsonl"
        with open(inbox_path, "a") as f:
            f.write(json.dumps(msg) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        """读取并清空指定成员的收件箱，返回消息列表（drain 语义）。"""
        inbox_path = self.dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return []
        messages = []
        for line in inbox_path.read_text().strip().splitlines():
            if line:
                messages.append(json.loads(line))
        inbox_path.write_text("")  # 清空文件，实现 drain
        return messages

    def broadcast(self, sender: str, content: str, teammates: list) -> str:
        """向所有成员（排除发送者自身）广播一条消息。"""
        count = 0
        for name in teammates:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


# 全局消息总线实例
BUS = MessageBus(INBOX_DIR)


# ── 团队成员管理器（新增关闭协议 + 计划审批协议）──
class TeammateManager:
    def __init__(self, team_dir: Path):
        self.dir = team_dir
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}  # name -> Thread

    def _load_config(self) -> dict:
        """从磁盘加载团队配置，若不存在则返回默认配置。"""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self):
        """将当前配置持久化到磁盘。"""
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find_member(self, name: str) -> dict:
        """按名称查找团队成员配置，未找到返回 None。"""
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def spawn(self, name: str, role: str, prompt: str) -> str:
        """
        生成（或重启）一个具名团队成员。
        若成员已存在且处于 idle/shutdown 状态，则重新激活；
        否则新建成员记录，并在独立守护线程中启动其智能体循环。
        """
        member = self._find_member(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save_config()
        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"Spawned '{name}' (role: {role})"

    def _teammate_loop(self, name: str, role: str, prompt: str):
        """
        成员智能体的主循环（运行于独立线程）。
        常驻运行，空闲时轮询邮箱等待新任务。
        新增：
          - 执行重要工作前须通过 plan_approval 提交计划
          - 收到 shutdown_request 后，通过 shutdown_response 工具响应
          - 一旦成员批准关闭（approve=True），循环退出
        """
        sys_prompt = (
            f"You are '{name}', role: {role}, at {WORKDIR}. "
            f"Submit plans via plan_approval before major work. "
            f"Respond to shutdown_request with shutdown_response. "
            f"When idle, wait for new messages in your inbox."
        )
        messages = [{"role": "user", "content": prompt}]
        tools = self._teammate_tools()
        should_exit = False

        while not should_exit:
            # 读取收件箱
            inbox = BUS.read_inbox(name)

            # 如果没有新消息且上一轮模型已停止工具调用，进入空闲等待
            if not inbox and messages[-1]["role"] == "assistant":
                member = self._find_member(name)
                if member and member["status"] != "idle":
                    member["status"] = "idle"
                    self._save_config()
                time.sleep(2)  # 轮询间隔
                continue

            # 注入新消息
            for msg in inbox:
                messages.append({"role": "user", "content": json.dumps(msg)})

            # 如果有新消息，更新状态为 working
            if inbox:
                member = self._find_member(name)
                if member and member["status"] == "idle":
                    member["status"] = "working"
                    self._save_config()

            try:
                response = client.messages.create(
                    model=MODEL,
                    system=sys_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=8000,
                )
            except Exception:
                break

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                continue  # 模型停止工具调用，继续等待新消息

            # 执行工具调用
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    output = self._exec(name, block.name, block.input)
                    print(f"  [{name}] {block.name}: {str(output)[:120]}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })
                    if block.name == "shutdown_response" and block.input.get("approve"):
                        should_exit = True
            messages.append({"role": "user", "content": results})

        # 退出循环，标记为 shutdown
        member = self._find_member(name)
        if member:
            member["status"] = "shutdown"
            self._save_config()

    def _exec(self, sender: str, tool_name: str, args: dict) -> str:
        """
        成员工具调度：将工具名映射到对应实现。
        在 s09 基础工具之上，新增两个协议工具：
          - shutdown_response：回应负责人的关闭请求
          - plan_approval：向负责人提交计划，等待审批
        """
        # 以下基础工具与 s02 保持一致，不做修改
        if tool_name == "bash":
            return _run_bash(args["command"])
        if tool_name == "read_file":
            return _run_read(args["path"])
        if tool_name == "write_file":
            return _run_write(args["path"], args["content"])
        if tool_name == "edit_file":
            return _run_edit(args["path"], args["old_text"], args["new_text"])
        if tool_name == "send_message":
            return BUS.send(sender, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "read_inbox":
            return json.dumps(BUS.read_inbox(sender), indent=2)

        # ── 新增：关闭协议响应工具 ──
        if tool_name == "shutdown_response":
            req_id = args["request_id"]
            approve = args["approve"]
            # 更新追踪器中该请求的状态
            with _tracker_lock:
                if req_id in shutdown_requests:
                    shutdown_requests[req_id]["status"] = "approved" if approve else "rejected"
            # 将响应消息发回负责人收件箱，携带 request_id 以便关联
            BUS.send(
                sender, "lead", args.get("reason", ""),
                "shutdown_response", {"request_id": req_id, "approve": approve},
            )
            return f"Shutdown {'approved' if approve else 'rejected'}"

        # ── 新增：计划审批提交工具 ──
        if tool_name == "plan_approval":
            plan_text = args.get("plan", "")
            req_id = str(uuid.uuid4())[:8]  # 生成短 UUID 作为关联 ID
            # 注册到追踪器，状态初始为 pending
            with _tracker_lock:
                plan_requests[req_id] = {"from": sender, "plan": plan_text, "status": "pending"}
            # 将计划发送至负责人收件箱，类型为 plan_approval_response（触发负责人审阅）
            BUS.send(
                sender, "lead", plan_text, "plan_approval_response",
                {"request_id": req_id, "plan": plan_text},
            )
            return f"Plan submitted (request_id={req_id}). Waiting for lead approval."

        return f"Unknown tool: {tool_name}"

    def _teammate_tools(self) -> list:
        """返回团队成员可用的工具定义列表（基础工具 + 通信工具 + 协议工具）。"""
        # 以下基础工具与 s02 保持一致，不做修改
        return [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file contents.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write content to file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Replace exact text in file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message to a teammate.",
             "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
            {"name": "read_inbox", "description": "Read and drain your inbox.",
             "input_schema": {"type": "object", "properties": {}}},
            # ── 新增：关闭协议响应工具（成员侧）──
            {"name": "shutdown_response",
             "description": "Respond to a shutdown request. Approve to shut down, reject to keep working.",
             "input_schema": {"type": "object", "properties": {
                 "request_id": {"type": "string"},   # 与 shutdown_request 中的 request_id 对应
                 "approve": {"type": "boolean"},      # True=同意关闭，False=拒绝
                 "reason": {"type": "string"},        # 可选：说明原因
             }, "required": ["request_id", "approve"]}},
            # ── 新增：计划审批提交工具（成员侧）──
            {"name": "plan_approval",
             "description": "Submit a plan for lead approval. Provide plan text.",
             "input_schema": {"type": "object", "properties": {
                 "plan": {"type": "string"},          # 计划文本，由负责人审阅
             }, "required": ["plan"]}},
        ]

    def list_all(self) -> str:
        """返回当前团队所有成员的名称、角色和状态。"""
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        """返回所有成员的名称列表，用于广播等场景。"""
        return [m["name"] for m in self.config["members"]]


# 全局团队管理器实例
TEAM = TeammateManager(TEAM_DIR)


# ── 基础工具实现（与 s02 保持一致，不做修改）──

def _safe_path(p: str) -> Path:
    """将相对路径解析为绝对路径，并校验不会越出工作目录。"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def _run_bash(command: str) -> str:
    """同步执行 Shell 命令，屏蔽危险命令，超时 120 秒。"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def _run_read(path: str, limit: int = None) -> str:
    """读取文件内容，可选限制返回行数。"""
    try:
        lines = _safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def _run_write(path: str, content: str) -> str:
    """将内容写入文件，自动创建不存在的父目录。"""
    try:
        fp = _safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def _run_edit(path: str, old_text: str, new_text: str) -> str:
    """精确替换文件中指定文本的第一处匹配。"""
    try:
        fp = _safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# ── 负责人侧协议处理函数 ──

def handle_shutdown_request(teammate: str) -> str:
    """
    向指定成员发送关闭请求：
      1. 生成唯一 request_id（短 UUID）
      2. 在 shutdown_requests 追踪器中注册，初始状态为 pending
      3. 通过消息总线发送 shutdown_request 消息给目标成员
    返回请求 ID 和当前状态，供负责人后续查询。
    """
    req_id = str(uuid.uuid4())[:8]
    with _tracker_lock:
        shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    BUS.send(
        "lead", teammate, "Please shut down gracefully.",
        "shutdown_request", {"request_id": req_id},
    )
    return f"Shutdown request {req_id} sent to '{teammate}' (status: pending)"


def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    """
    负责人审阅并响应成员提交的计划：
      1. 通过 request_id 在 plan_requests 追踪器中查找对应计划
      2. 更新追踪器状态为 approved 或 rejected
      3. 将审阅结果（含可选反馈）通过消息总线发回提交者
    """
    with _tracker_lock:
        req = plan_requests.get(request_id)
    if not req:
        return f"Error: Unknown plan request_id '{request_id}'"
    with _tracker_lock:
        req["status"] = "approved" if approve else "rejected"
    # 将审阅结果发回计划提交者，携带 request_id 和审批决定
    BUS.send(
        "lead", req["from"], feedback, "plan_approval_response",
        {"request_id": request_id, "approve": approve, "feedback": feedback},
    )
    return f"Plan {req['status']} for '{req['from']}'"


def _check_shutdown_status(request_id: str) -> str:
    """
    查询指定 request_id 的关闭请求当前状态。
    在负责人工具中充当 shutdown_response 的实现：
    负责人通过此工具轮询成员的关闭响应结果。
    """
    with _tracker_lock:
        return json.dumps(shutdown_requests.get(request_id, {"error": "not found"}))


# ── 负责人工具调度（共 12 个工具）──
# 在 s09 的 9 个工具基础上，新增关闭协议和计划审批协议相关工具
TOOL_HANDLERS = {
    # 基础工具（与 s02 一致）
    "bash":              lambda **kw: _run_bash(kw["command"]),
    "read_file":         lambda **kw: _run_read(kw["path"], kw.get("limit")),
    "write_file":        lambda **kw: _run_write(kw["path"], kw["content"]),
    "edit_file":         lambda **kw: _run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    # 团队管理工具（与 s09 一致）
    "spawn_teammate":    lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates":    lambda **kw: TEAM.list_all(),
    "send_message":      lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox":        lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast":         lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
    # ── 新增：关闭协议工具（负责人侧）──
    # shutdown_request：向成员发送关闭请求，返回 request_id
    "shutdown_request":  lambda **kw: handle_shutdown_request(kw["teammate"]),
    # shutdown_response：查询关闭请求的当前状态（负责人用于轮询结果）
    "shutdown_response": lambda **kw: _check_shutdown_status(kw.get("request_id", "")),
    # ── 新增：计划审批工具（负责人侧）──
    # plan_approval：审阅并批准/拒绝成员提交的计划
    "plan_approval":     lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
}

# 提供给负责人 LLM 的工具定义列表（基础工具与 s02 保持一致，不做修改）
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "spawn_teammate", "description": "Spawn a persistent teammate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send a message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    # ── 新增：关闭协议工具定义（负责人侧）──
    {"name": "shutdown_request",
     "description": "Request a teammate to shut down gracefully. Returns a request_id for tracking.",
     "input_schema": {"type": "object", "properties": {
         "teammate": {"type": "string"},  # 目标成员名称
     }, "required": ["teammate"]}},
    {"name": "shutdown_response",
     "description": "Check the status of a shutdown request by request_id.",
     "input_schema": {"type": "object", "properties": {
         "request_id": {"type": "string"},  # 由 shutdown_request 返回的关联 ID
     }, "required": ["request_id"]}},
    # ── 新增：计划审批工具定义（负责人侧）──
    {"name": "plan_approval",
     "description": "Approve or reject a teammate's plan. Provide request_id + approve + optional feedback.",
     "input_schema": {"type": "object", "properties": {
         "request_id": {"type": "string"},   # 由成员 plan_approval 工具生成的关联 ID
         "approve": {"type": "boolean"},      # True=批准，False=拒绝
         "feedback": {"type": "string"},      # 可选：审阅意见
     }, "required": ["request_id", "approve"]}},
]
    

def agent_loop(messages: list):
    """
    负责人智能体主循环：每轮先将收件箱新消息注入对话上下文，
    然后调用 LLM，持续执行工具调用直到模型停止发出工具请求。
    收件箱中可能包含来自成员的 shutdown_response 或 plan_approval_response，
    负责人模型会据此决定后续操作（更新追踪状态、继续审阅等）。
    """
    while True:
        # 每轮调用 LLM 前，先读取负责人收件箱中的新消息并注入对话
        inbox = BUS.read_inbox("lead")
        if inbox:
            messages.append({
                "role": "user",
                "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>",
            })

        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        # 模型不再调用工具，本轮任务完成，退出循环
        if response.stop_reason != "tool_use":
            return

        # 遍历并执行所有工具调用块
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                print(f"> {block.name}:")
                print(str(output)[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                })
        # 将本轮工具结果追加到消息历史
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []  # 维护跨轮次的对话历史

    while True:
        try:
            # 显示彩色提示符，等待用户输入
            query = input("\033[36ms10 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        # 内置调试命令：直接打印团队状态，不进入智能体循环
        if query.strip() == "/team":
            print(TEAM.list_all())
            continue
        # 内置调试命令：直接打印负责人收件箱内容
        if query.strip() == "/inbox":
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
            continue

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