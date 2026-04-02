from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Static


class MyApp(App):
    BINDINGS = [
        Binding("m", "toggle_mode", "切换编辑模式"),
        Binding("s", "save", "保存"),
        Binding("d", "delete", "删除"),
        Binding("q", "quit", "退出"),
    ]

    edit_mode = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._status_text(), id="status")
        yield Footer()

    def _status_text(self) -> str:
        mode = "编辑模式" if self.edit_mode else "浏览模式"
        return f"Hello, Textual! 当前是 {mode}（按 m 切换）"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"save", "delete"}:
            return self.edit_mode
        return True

    def action_toggle_mode(self) -> None:
        self.edit_mode = not self.edit_mode
        self.query_one("#status", Static).update(self._status_text())
        self.refresh_bindings()

    def action_save(self) -> None:
        self.notify("已保存")

    def action_delete(self) -> None:
        self.notify("已删除")

if __name__ == "__main__":
    MyApp().run()