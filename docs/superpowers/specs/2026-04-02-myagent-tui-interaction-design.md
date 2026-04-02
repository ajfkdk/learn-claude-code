# MyAgent TUI 交互优化设计 - Phase 1

## 目标

优化 MyAgent Textual REPL 的人机交互体验：快捷键提示、Enter 提交、发送反馈动画。

## 改动范围

**修改：**
- `MyAgent/tui.py` — Footer、输入拦截、思考动画

**不改动：**
- `AgentCore.py` 核心逻辑
- 其他模块

---

## 设计方案

### 1. Footer 快捷键提示

Textual 的 `Footer` 组件会根据 `BINDINGS` 自动渲染快捷键菜单。

```python
BINDINGS = [
    Binding("enter", "submit_input", "Submit", show=True),
    Binding("shift+enter", "newline", "Newline", show=True),
    Binding("ctrl+g", "open_editor", "Edit", show=True),
    Binding("ctrl+q", "quit", "Quit", show=True),
]
```

Footer 自动显示：
```
[Enter] Submit  [Shift+Enter] Newline  [Ctrl+G] Edit  [Ctrl+Q] Quit
```

### 2. Enter 提交 + Shift+Enter 换行

TextArea 默认 Enter 是换行。需要拦截 `enter` 用于提交，`shift+enter` 保持换行。

实现方式：
- 拦截 TextArea 的 `Key` 事件
- `enter` → `action_submit_input()`
- `shift+enter` → 保持 TextArea 默认换行行为

### 3. 发送反馈动画

提交后显示思考中动画：

```python
THINKING_WORDS = ["思考中", "构思中", "分析中", "推理中", "处理中", "加载中"]
THINKING_COLORS = ["blue", "cyan", "green"]  # 冷暖渐变
```

行为：
1. 用户按 Enter 提交，输入框清空
2. Log 显示 `思考中█`（蓝色）
3. 光标 `█` 每 500ms 闪烁一次
4. 每秒切换下一个词语和颜色（蓝→青→绿 循环）
5. 收到回复后，清除思考行，显示 AI 响应

### 4. 颜色渐变方案

| 序号 | 颜色 | ANSI |
|------|------|------|
| 0 | 蓝色 | `#1E90FF` |
| 1 | 青色 | `#00CED1` |
| 2 | 绿色 | `#32CD32` |

---

## 验证标准

- [ ] Footer 显示 4 个快捷键提示
- [x] Enter 提交输入
- [ ] Shift+Enter 换行
- [ ] Ctrl+G 打开编辑器
- [ ] Ctrl+Q 退出
- [ ] 提交后显示思考中动画
- [ ] 动画颜色蓝→青→绿循环变化
- [ ] 光标闪烁
- [ ] 收到回复后动画消失

## 测试反馈（2026-04-02）

- 问题：实测点击 Enter 时仍是换行，未触发提交。
- 根因：`TextArea` 默认先处理了 Enter，App 级 `Binding("enter", "submit_input")` 未生效。
- 修复：在 `MyAgent/tui.py` 中引入 `InputTextArea`，于组件层拦截 Enter；`Enter` 调用 `action_submit_input()`，`Shift+Enter` 保持默认换行。
- 问题：思考动画在 `Log` 中逐行写入，造成刷屏。
- 修复：改为 `#thinking-status` 单行状态区刷新（类似 ClaudeCode 的独立 spinner 行），动画不再污染消息日志。
- 问题：工具很快返回时，`思考中` 不明显或瞬间消失。
- 修复：TUI 增加思考状态最短可见时长（0.8s），保证用户能看到反馈。
- 问题：执行工具时只有结果，缺少“正在使用哪个工具”的明确提示。
- 修复：`AgentCore` 在每次 `tool_use` 前输出 `[tool] using <name>[: preview]`，再输出 `> <name>:` 结果块。
