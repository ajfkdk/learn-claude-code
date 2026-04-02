# MyAgent Streaming Output 升级设计

## 目标

为 MyAgent 添加流式输出能力，AI 响应逐字/逐块显示，提升交互体验。

## 改动范围

**仅改动：**
- `MyAgent/AgentCore.py` 的 `agent_loop()` 函数中的 LLM 调用和输出部分

**不改动：**
- todos、tasks、team、skills 等其他模块
- REPL 命令结构（/compact /tasks /team /inbox 保持不变）

## 设计方案

### 现有逻辑

```python
response = client.messages.create(
    model=MODEL, system=SYSTEM, messages=messages,
    tools=TOOLS, max_tokens=8000,
)
# 同步等待完整响应后一次性打印
print(resp.content[0].text)
```

### 新逻辑

```python
# 使用 Anthropic SDK 的 streaming API
with client.messages.stream(
    model=MODEL, system=SYSTEM, messages=messages,
    tools=TOOLS, max_tokens=8000,
) as stream:
    for chunk in stream.text_stream:
        print(chunk, end="", flush=True)  # 逐块输出
```

### 工具执行结果

工具执行部分保持不变（串行执行，结果一次性追加到 messages），仅改造 AI 文本输出的展示方式。

## 实现步骤

1. 改造 `agent_loop()` 中的 `client.messages.create` 为 `client.messages.stream`
2. 遍历 `stream.text_stream` 逐块打印
3. 保留 `stop_reason` 判断和工具执行逻辑
4. 测试 streaming 正常工作

## 验证标准

- AI 响应以流式方式逐块显示，不再等待完整响应
- 工具执行结果正常追加到对话历史
- 压缩、任务、团队等其他功能不受影响
