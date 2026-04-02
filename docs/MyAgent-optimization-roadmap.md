# MyAgent 优化路线图

> 整理日期：2026-04-02
> 参照项目：ClaudeCode

## 已完成优化

| 序号 | 优化项 | 状态 | 说明 |
|------|--------|------|------|
| 1 | Streaming 输出 | ✅ | AI 响应逐字显示 |
| 2 | Textual REPL | ✅ | TUI 界面，支持 Ctrl+G 编辑器 |
| 3 | 思考动画 | ✅ | 蓝→青→绿颜色循环 + 光标闪烁 |
| 4 | Footer 快捷键 | ✅ | Escape 提交、Ctrl+G、Ctrl+Q |
| 5 | 思考最短可见时长 | ✅ | 0.8s 保证动画可见性 |

---

## 待优化清单（按优先级）

### Phase 1：交互体验（用户侧感知最强）

| 序号 | 优化项 | 难度 | 说明 |
|------|--------|------|------|
| 1.1 | **消息彩色分层** | 低 | user/assistant/system 不同颜色，当前全白 |
| 1.2 | **Enter 提交 + Shift+Enter 换行** | 低 | 当前 Enter 是换行，Esc 提交太反人性 |
| 1.3 | **工具执行进度展示** | 中 | ClaudeCode 显示 `using tool: bash` 等，当前直接出结果 |
| 1.4 | **错误消息红色高亮** | 低 | 错误输出用红色区分 |

### Phase 2：工具系统

| 序号 | 优化项 | 难度 | 说明 |
|------|--------|------|------|
| 2.1 | **MCP 协议支持** | 高 | 连接 MCP 服务器，扩展工具能力 |
| 2.2 | **文件历史快照** | 中 | ClaudeCode 在修改前创建快照，支持 undo |
| 2.3 | **Glob/Grep 增强** | 低 | 当前只有基础 bash，可封装高级搜索 |

### Phase 3：上下文与压缩

| 序号 | 优化项 | 难度 | 说明 |
|------|--------|------|------|
| 3.1 | **Session Memory Compact** | 高 | 不调用 LLM 的压缩，token 节省大 |
| 3.2 | **CLAUDE.md 动态加载** | 中 | 根据项目结构自动加载 CLAUDE.md |
| 3.3 | **CompactBoundary 消息标记** | 中 | 压缩边界标记，支持会话恢复 |
| 3.4 | **工具对完整性保护** | 中 | 压缩时保证 tool_use/tool_result 成对 |

### Phase 4：子 Agent 与团队

| 序号 | 优化项 | 难度 | 说明 |
|------|--------|------|------|
| 4.1 | **Worktree 隔离** | 高 | 子 Agent 在独立 git worktree 工作 |
| 4.2 | **异步子 Agent** | 高 | 后台运行长时间任务，不阻塞主循环 |
| 4.3 | **shutdown 协议增强** | 中 | request_id 握手机制 |
| 4.4 | **Plan Approval 审批流** | 中 | 子 Agent 提交计划 → 人工审批 |

### Phase 5：架构升级

| 序号 | 优化项 | 难度 | 说明 |
|------|--------|------|------|
| 5.1 | **5 层架构分离** | 高 | 交互/编排/核心循环/工具/通信 分层 |
| 5.2 | **Feature Flag 系统** | 中 | ClaudeCode 的 feature() 控制可选功能 |
| 5.3 | **Provider 路由** | 中 | 支持 Bedrock/Vertex/Azure 多后端 |
| 5.4 | **Transcript 持久化** | 中 | 对话记录序列化，支持 --resume |

---

## 推荐实施顺序

```
1.1 消息彩色分层  →  立即可做，用户感知明显
       ↓
1.2 Enter提交  →  当前 Esc 提交反人性
       ↓
1.3 工具执行展示  →  让用户知道 Agent 在做什么
       ↓
2.2 文件快照  →  保护用户数据
       ↓
3.2 CLAUDE.md加载  →  上下文增强
       ↓
3.1 Session Memory Compact  →  token优化大招
       ↓
4.1 Worktree隔离  →  高级委派
```

---
补充信息：
1.借鉴claudecode的报错处理机制：

prompt too long 预阻断（不发请求，直接返回）
ClaudeCode/src/query.ts:629

prompt too long 运行中恢复失败后的最终报错（collapse/reacive compact 后仍失败）
ClaudeCode/src/query.ts:1068、ClaudeCode/src/query.ts:1171

媒体尺寸错误（图片/PDF 过大）
ClaudeCode/src/query.ts:972、ClaudeCode/src/query.ts:1085

max_output_tokens（先升级上限、再恢复重试、最后才暴露错误）
ClaudeCode/src/query.ts:1188

模型 fallback 触发（FallbackTriggeredError，切换模型重试）
ClaudeCode/src/query.ts:896

用户中断/abort（补齐 tool_result 后安全终止）
ClaudeCode/src/query.ts:1018

通用模型/运行时错误（outer catch，返回 model_error）
ClaudeCode/src/query.ts:958

stop hook 阻断/禁止继续（结构化中止，不是异常吞掉）
ClaudeCode/src/query.ts:1270

自动压缩连续失败熔断（避免无限重试）
ClaudeCode/src/services/compact/autoCompact.ts:257


上下文压缩机制
 Tool result budget
 History snip
 Microcompact
 Context collaps
 Autocompact

 prompt借鉴
 如何组装一个个prompt
 