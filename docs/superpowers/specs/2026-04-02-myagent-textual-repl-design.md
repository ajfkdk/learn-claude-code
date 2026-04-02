# MyAgent Textual REPL 升级设计

## 目标

将 MyAgent REPL 迁移到 Textual TUI，支持 Ctrl+G 打开外部编辑器（notepad.exe），内容自动返回输入框。

## 改动范围

**新增：**
- `MyAgent/tui.py` — Textual REPL 应用

**修改：**
- `MyAgent/AgentCore.py` — REPL 入口改为启动 Textual App

**不改动：**
- `agent_loop()` 核心逻辑
- todos、tasks、team、skills 等其他模块

## 设计方案

### REPL 结构

```
App (MyAgentApp)
└── REPLScreen
    ├── Header          # 标题栏
    ├── MessageList     # 历史消息展示（只读）
    └── InputArea       # 输入区（TextArea，支持 Ctrl+G）
```

### Ctrl+G 流程

1. `InputArea` 绑定 `Ctrl+G` 快捷键
2. 将当前输入内容写入临时文件 `temp_editor_input.txt`
3. 调用 `subprocess.run(["notepad.exe", path])` 打开记事本
4. 用户关闭记事本后，读取文件内容
5. 将内容填充回 `InputArea`

### REPL 命令（保持不变）

- `/compact` — 手动压缩
- `/tasks` — 列出任务
- `/team` — 列出团队
- `/inbox` — 读取收件箱

### 消息展示

- `user` 消息：蓝色
- `assistant` 消息：白色/默认
- `tool_result`：绿色前缀 `> tool_name:`
- 错误：红色

### 入口改造

原入口：
```python
if __name__ == "__main__":
    history = []
    while True:
        query = input(...)
        agent_loop(history)
```

新入口：
```python
if __name__ == "__main__":
    from tui import MyAgentApp
    app = MyAgentApp()
    app.run()
```

## 验证标准

- Ctrl+G 能打开 notepad.exe
- 关闭记事本后内容返回输入框
- `/compact /tasks /team /inbox` 命令正常工作
- Streaming 输出正常显示
- Ctrl+C 中断当前输入
