from textual.app import App, ComposeResult
from textual.events import Key
from textual.widgets import Header, TextArea, Log, Footer, Static
from textual.binding import Binding
import subprocess
import tempfile
import os
import sys
import io
import locale
import asyncio
import threading
import time


THINKING_WORDS = ["思考中", "构思中", "分析中", "推理中", "处理中", "加载中"]
THINKING_COLORS = ["#1E90FF", "#00CED1", "#32CD32"]  # 蓝→青→绿
THINKING_FRAMES = ["-", "\\", "|", "/"]


def should_reduce_tui_motion() -> bool:
    if os.environ.get("MYAGENT_TUI_NO_ANIMATION") == "1":
        return True
    if os.environ.get("PYCHARM_HOSTED") == "1":
        return True
    terminal_emulator = os.environ.get("TERMINAL_EMULATOR", "").lower()
    if "jetbrains" in terminal_emulator or "jediterm" in terminal_emulator:
        return True
    return False


class PrintCapture(io.TextIOBase):
    """捕获 print 输出到缓冲区"""

    def __init__(self, on_chunk=None):
        super().__init__()
        self.buffer = ""
        self.on_chunk = on_chunk

    def write(self, text: str) -> int:
        if not text:
            return 0
        self.buffer += text
        if self.on_chunk:
            self.on_chunk(text)
        return len(text)

    def flush(self) -> None:
        pass

    def get_output(self) -> str:
        return self.buffer


class ThinkingAnimator:
    """思考中动画：单行状态刷新（不刷屏）。"""

    def __init__(self, app, reduced_motion: bool = False):
        self.app = app
        self.reduced_motion = reduced_motion
        self.running = False
        self.word_index = 0
        self.color_index = 0
        self.frame_index = 0
        self.cursor_visible = True
        self._tick_count = 0
        self._timer = None

    def _render(self) -> str:
        if self.reduced_motion:
            return "思考中..."
        word = THINKING_WORDS[self.word_index]
        frame = THINKING_FRAMES[self.frame_index]
        cursor = "█" if self.cursor_visible else " "
        return f"{frame} {word}{cursor}"

    def _draw(self):
        status_widget = self.app.query_one("#thinking-status", Static)
        if self.running:
            status_widget.styles.color = THINKING_COLORS[self.color_index]
            status_widget.update(self._render())
        else:
            status_widget.update("")

    def start(self):
        self.running = True
        self.word_index = 0
        self.color_index = 0
        self.frame_index = 0
        self.cursor_visible = True
        self._tick_count = 0
        self._draw()
        if not self.reduced_motion:
            self._timer = self.app.set_interval(0.5, self._update)

    def stop(self):
        self.running = False
        self._draw()
        if self._timer:
            self._timer.stop()
            self._timer = None

    def _update(self):
        if not self.running:
            return
        self._tick_count += 1
        self.cursor_visible = not self.cursor_visible
        self.frame_index = (self.frame_index + 1) % len(THINKING_FRAMES)
        if self._tick_count % 2 == 0:
            self.color_index = (self.color_index + 1) % len(THINKING_COLORS)
            self.word_index = (self.word_index + 1) % len(THINKING_WORDS)
        self._draw()


class InputTextArea(TextArea):
    """输入框按键行为：Esc 提交，Enter 保持默认换行。"""

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            event.prevent_default()
            event.stop()
            self.app.action_submit_input()


class MyAgentApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #message-log {
        height: 1fr;
    }
    #input-area {
        height: 6;
        dock: bottom;
    }
    #thinking-status {
        height: 1;
        dock: top;
        padding-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "submit_input", "Submit", show=True),
        Binding("ctrl+g", "open_editor", "Edit", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.history = []
        self._orig_stdout = None
        self._capture = None
        self._thinking_animator = None
        self._thinking_line = ""
        self._thinking_started_at = None
        self._pending_output = None
        self._reduced_tui_motion = should_reduce_tui_motion()
        self._rendered_output_len = 0
        self._stream_started = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="thinking-status")
        yield Log(id="message-log")
        yield InputTextArea(id="input-area")
        yield Footer()

    def action_newline(self) -> None:
        """Shift+Enter: 插入换行符"""
        input_widget = self.query_one("#input-area", TextArea)
        input_widget.text += "\n"
        # 移动光标到末尾
        input_widget.cursor_position = len(input_widget.text)

    def action_open_editor(self) -> None:
        """Ctrl+G: 打开 notepad.exe 编辑器"""
        input_widget = self.query_one("#input-area", TextArea)
        current_text = input_widget.text

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8-sig') as f:
            f.write(current_text)
            temp_path = f.name

        subprocess.run(["notepad.exe", temp_path], check=True)

        raw = b""
        with open(temp_path, 'rb') as f:
            raw = f.read()

        encodings = [
            'utf-8-sig',
            'utf-16',
            'utf-16-le',
            'utf-16-be',
            locale.getpreferredencoding(False),
            'gbk',
        ]
        new_text = None
        for enc in encodings:
            try:
                new_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if new_text is None:
            new_text = raw.decode('utf-8', errors='replace')
        os.unlink(temp_path)

        input_widget.text = new_text

    def action_submit_input(self) -> None:
        """Enter: 提交输入"""
        input_widget = self.query_one("#input-area", TextArea)
        query = input_widget.text.strip()
        input_widget.text = ""

        if not query:
            return

        # 处理 REPL 命令
        if query.startswith("/"):
            self.handle_command(query)
            return

        # 加入历史
        self.history.append({"role": "user", "content": query})

        # 启动思考动画
        self._start_thinking()

        # 在后台线程运行 agent_loop
        def run_agent():
            self._start_capture()
            try:
                from AgentCore import agent_loop
                agent_loop(self.history)
            finally:
                output = self._stop_capture()
                # 回到主线程输出
                self.call_later(lambda: self._on_agent_done(output))

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

    def _start_thinking(self):
        """启动思考动画"""
        self._thinking_started_at = time.monotonic()
        self._thinking_animator = ThinkingAnimator(self, reduced_motion=self._reduced_tui_motion)
        self._thinking_animator.start()

    def _on_agent_done(self, output: str = None):
        """Agent 执行完毕，清除动画并显示结果"""
        min_visible_seconds = 0.8
        if self._thinking_started_at is not None and self._thinking_animator is not None:
            elapsed = time.monotonic() - self._thinking_started_at
            if elapsed < min_visible_seconds:
                self._pending_output = output
                self.set_timer(min_visible_seconds - elapsed, self._flush_agent_done)
                return

        self._finalize_agent_done(output)

    def _flush_agent_done(self):
        output = self._pending_output
        self._pending_output = None
        self._finalize_agent_done(output)

    def _finalize_agent_done(self, output: str = None):
        # 停止动画
        if self._thinking_animator:
            self._thinking_animator.stop()
            self._thinking_animator = None
        self._thinking_started_at = None

        # 输出兜底：若存在未流式渲染的尾部内容，补写到日志
        if output:
            tail = output[self._rendered_output_len:]
            if tail:
                log_widget = self.query_one("#message-log", Log)
                log_widget.write(tail)
        self._rendered_output_len = 0
        self._stream_started = False

    def _on_capture_chunk(self, text: str) -> None:
        self.call_from_thread(self._append_stream_chunk, text)

    def _append_stream_chunk(self, text: str) -> None:
        if not self._stream_started:
            if self._thinking_animator:
                self._thinking_animator.stop()
                self._thinking_animator = None
            self._stream_started = True
        log_widget = self.query_one("#message-log", Log)
        log_widget.write(text)
        self._rendered_output_len += len(text)

    def _start_capture(self):
        """开始捕获 print 输出"""
        self._orig_stdout = sys.stdout
        self._rendered_output_len = 0
        self._stream_started = False
        self._capture = PrintCapture(on_chunk=self._on_capture_chunk)
        sys.stdout = self._capture

    def _stop_capture(self) -> str:
        """停止捕获，恢复原始 stdout，返回捕获的输出"""
        if self._orig_stdout:
            sys.stdout = self._orig_stdout
            output = self._capture.get_output()
            self._orig_stdout = None
            self._capture = None
            return output
        return ""

    async def on_mount(self) -> None:
        self.query_one("#input-area", TextArea).focus()

    def handle_command(self, query: str) -> None:
        """处理 / 命令"""
        if query == "/compact":
            from AgentCore import auto_compact
            auto_compact(self.history)
            print("[manual compact]")
        elif query == "/tasks":
            from AgentCore import TASK_MGR
            print(TASK_MGR.list_all())
        elif query == "/team":
            from AgentCore import TEAM
            print(TEAM.list_all())
        elif query == "/inbox":
            from AgentCore import BUS
            import json
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
        else:
            print(f"Unknown command: {query}")
