使用Superpower skill帮我完成这项任务
原型项目文件夹：
重构项目文件夹：
我使用了一个低能AI来帮我重构，但是没想到重构的项目是一坨shit
我需要你帮我重新重构这个项目
项目的结构：MyAgent/
├── config.py      # 新建：所有硬编码配置值
├── tools.py       # 新建：base_tools + TOOL_HANDLERS + TOOLS
├── managers.py    # 新建：所有 Manager 类 + 全局实例
└── agentcore.py   # 修改：保留 agent_loop + 汇总 import

整体项目要干净整洁，不要什么TOOL定义这边定义一坨，那边定义一坨，明明有tools.py公共脚本
重构的项目有几个要点我花心思改造：
Streaming 输出
TUI 交互
REPL 交互
我希望你重新重构的时候可以继续保持这几个功能