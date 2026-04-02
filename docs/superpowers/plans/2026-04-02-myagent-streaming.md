# MyAgent Streaming Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `client.messages.create` 改为 `client.messages.stream`，实现 AI 响应流式输出。

**Architecture:** 保持 agent_loop 结构不变，仅改造 LLM 调用和输出逻辑。streaming 返回的 `text_stream` 用于逐块打印，最终组装完整消息用于后续工具执行。

**Tech Stack:** Anthropic Python SDK (streaming API), Python 标准库

---

## File Map

- **Modify:** `MyAgent/AgentCore.py` — `agent_loop()` 函数（约 10 行改动）

---

## Task 1: 改造 agent_loop 的 LLM 调用为流式

**Files:**
- Modify: `MyAgent/AgentCore.py:789-796`

- [ ] **Step 1: 确认现有代码位置**

查看 `MyAgent/AgentCore.py` 第 789-796 行：

```python
        # LLM调用
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
```

- [ ] **Step 2: 替换为 streaming 版本**

将第 789-793 行替换为：

```python
        # LLM调用（流式输出）
        full_content = []
        stop_reason = None
        with client.messages.stream(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
                full_content.append(chunk)
            stop_reason = stream.stop_reason
        print()  # 换行
        # 组装完整响应用于工具执行
        content_text = "".join(full_content)
        # 重新创建 content blocks 用于工具执行（streaming 返回纯文本）
        # 如果需要 text 输出，创建 TextBlock
        from anthropic.types import TextBlock
        response_content = [TextBlock(type="text", text=content_text)]
        messages.append({"role": "assistant", "content": response_content})
        if stop_reason != "tool_use":
            return
```

- [ ] **Step 3: 验证 import**

确认文件顶部已有 `from anthropic import Anthropic`。如有需要添加 `TextBlock` import。

- [ ] **Step 4: 运行测试**

```bash
cd MyAgent && python AgentCore.py
```

输入一条简单问题，验证：
1. AI 响应逐字/逐块显示，不再等待完整响应
2. 工具调用（bash、read_file 等）正常工作
3. `/tasks`、`/team` 等 REPL 命令正常

- [ ] **Step 5: 提交**

```bash
git add MyAgent/AgentCore.py
git commit -m "feat: streaming output for AI responses"
```
