#!/usr/bin/env python3
# 测试框架：上下文压缩 —— 清理内存以支持无限会话
"""
s06_context_compact.py - 上下文压缩模块

三层压缩流水线，让智能体可以永久运行而不受上下文长度限制：

    每轮对话：
    +------------------+
    |   工具调用结果    |
    +------------------+
            |
            v
    [第一层：micro_compact（微压缩）]        （每轮静默执行）
      将最近 3 次以外的旧工具调用结果
      替换为 "[Previous: used {tool_name}]" 占位符
            |
            v
    [检查：token 数量是否超过 50000？]
       |               |
      否               是
       |               |
       v               v
    继续运行    [第二层：auto_compact（自动压缩）]
                  将完整对话记录保存到 .transcripts/ 目录
                  调用 LLM 对对话内容进行摘要
                  用摘要替换全部消息历史
                        |
                        v
                [第三层：compact 工具（手动压缩）]
                  模型主动调用 compact 工具 -> 立即触发摘要压缩
                  逻辑与自动压缩相同，但由模型手动触发

核心思想："智能体可以有策略地遗忘，从而永久持续工作。"
"""

import json
import os
import subprocess
import time
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

# 如果设置了自定义 API 地址，则移除可能冲突的认证 Token 环境变量
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 工作目录：所有文件操作都限制在此目录下
WORKDIR = Path(r'D:\develop\CPP\learn-claude-code')
# 创建 Anthropic 客户端（支持自定义 base_url）
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# 使用的模型 ID，从环境变量读取
MODEL = os.environ["MODEL_ID"]

# 系统提示：告知模型当前工作目录和角色
SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks."

# 触发自动压缩的 token 阈值（超过此值则执行第二层压缩）
THRESHOLD = 50000
# 对话记录的保存目录
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
# 微压缩保留最近几条工具调用结果不压缩
KEEP_RECENT = 3
# 这些工具的结果不会被微压缩删除（因为是重要参考内容）
PRESERVE_RESULT_TOOLS = {"read_file"}


def estimate_tokens(messages: list) -> int:
    """粗略估算 token 数量：按每 4 个字符约等于 1 个 token 计算。"""
    return len(str(messages)) // 4


# ── 第一层：micro_compact（微压缩）──
# 将旧的工具调用结果替换为简短占位符，节省上下文空间
def micro_compact(messages: list) -> list:
    # 收集所有 tool_result 条目的位置信息：(消息索引, 内容块索引, 结果字典)
    tool_results = []
    for msg_idx, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part_idx, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append((msg_idx, part_idx, part))

    # 如果结果数量未超过保留上限，无需压缩
    if len(tool_results) <= KEEP_RECENT:
        return messages

    # 构建 tool_use_id -> 工具名称 的映射表
    # 通过遍历 assistant 消息中的 tool_use 块来匹配
    tool_name_map = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name_map[block.id] = block.name

    # 对超出保留范围的旧结果进行压缩（保留最近 KEEP_RECENT 条）
    # 注意：read_file 的结果不压缩，因为它是文件内容参考，压缩后需要重新读取
    to_clear = tool_results[:-KEEP_RECENT]
    for _, _, result in to_clear:
        # 跳过内容很短的结果（已经很精简，无需压缩）
        if not isinstance(result.get("content"), str) or len(result["content"]) <= 100:
            continue
        tool_id = result.get("tool_use_id", "")
        tool_name = tool_name_map.get(tool_id, "unknown")
        # 跳过需要保留的工具（如 read_file）
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        # 用简短占位符替换原始结果内容
        result["content"] = f"[Previous: used {tool_name}]"

    return messages


# ── 第二层：auto_compact（自动压缩）──
# 保存完整对话记录，用 LLM 生成摘要，并替换消息历史
def auto_compact(messages: list) -> list:
    # 将完整对话保存为 JSONL 文件，便于后续回溯
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    print(f"[对话记录已保存：{transcript_path}]")

    # 取最近 80000 个字符的对话内容，调用 LLM 生成摘要
    conversation_text = json.dumps(messages, default=str)[-80000:]
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity. Include: "
            "1) What was accomplished, 2) Current state, 3) Key decisions made. "
            "Be concise but preserve critical details.\n\n" + conversation_text}],
        max_tokens=2000,
    )
    summary = response.content[0].text

    # 用摘要替换全部消息历史，大幅压缩上下文长度
    return [
        {"role": "user", "content": f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}"},
    ]


# ── 工具实现 ──

def safe_path(p: str) -> Path:
    """将相对路径解析为绝对路径，并确保不会越出工作目录。"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"路径越出工作区范围：{p}")
    return path

def run_bash(command: str) -> str:
    """在工作目录下执行 Shell 命令，屏蔽危险命令，超时 120 秒。"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        # 限制输出长度，避免撑爆上下文
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
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# 工具名称 -> 处理函数 的映射表
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "compact":    lambda **kw: "Manual compression requested.",  # 手动压缩，返回值仅作占位
}

# 提供给 LLM 的工具定义列表（符合 Anthropic API 格式）
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "compact", "description": "Trigger manual conversation compression.",
     "input_schema": {"type": "object", "properties": {"focus": {"type": "string", "description": "What to preserve in the summary"}}}},
]


def agent_loop(messages: list):
    """
    智能体主循环：持续调用 LLM，执行工具，直到模型停止发出工具调用为止。
    每轮都会执行压缩检查（第一层和第二层），第三层由模型主动触发。
    """
    while True:
        # 每轮调用前执行第一层微压缩，清理旧工具结果
        micro_compact(messages)

        # 若 token 估算超过阈值，触发第二层自动压缩
        if estimate_tokens(messages) > THRESHOLD:
            print("[触发自动压缩]")
            messages[:] = auto_compact(messages)

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
        manual_compact = False  # 标记是否触发了手动压缩
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compact":
                    # 模型请求手动压缩，稍后处理
                    manual_compact = True
                    output = "Compressing..."
                else:
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

        # 第三层：如果模型主动调用了 compact 工具，立即执行手动压缩
        if manual_compact:
            print("[手动压缩触发]")
            messages[:] = auto_compact(messages)
            return  # 压缩后结束本轮循环，等待用户下一次输入


if __name__ == "__main__":
    history = []  # 维护跨轮次的对话历史
    while True:
        try:
            # 显示彩色提示符，等待用户输入
            query = input("\033[36ms06 >> \033[0m")
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