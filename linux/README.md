# Linux 托盘面板

GTK 3 + AppIndicator 前端，复用项目现有的 Python 检测核心，不修改模型指纹判定。

Ubuntu/Debian 依赖：`python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 libnotify-bin`。
也兼容旧的 `gir1.2-appindicator3-0.1`。GNOME 需要启用 AppIndicator 扩展；Ubuntu 默认提供。

先按项目说明安装并信任 Codex 插件，再执行：

```sh
./linux/install.sh
~/.local/bin/nerfed-tray --show
```

点击顶栏表情图标，在菜单中选择“打开检测面板”。面板展示过去 48 小时近期会话及最近结果；选择会话可查看指纹、最近五次有效检测和证据。可以手动检测选中会话、新会话，以及修改自动检测频率。过往账户的结果标记为未验证，失败尝试单独提示。

每 8 秒读取 snapshot，每分钟调用 tick，沿用核心的活跃会话、频率、auto/nudge、暂停和并发规则。新会话检测沿用 fresh_frequency 设置，默认仅手动。插件 hooks 负责对话中的自动检测，托盘进程补充静默期间的调度；不会一次性检测全部历史对话。每次探测消耗账户额度。

检测结果通过 notify-send 通知，遵守 notify 和 notify_on_ok 配置；启动时不重发历史结果。关闭窗口不退出，菜单“退出”停止托盘调度，但已运行的检测和 Codex hooks 不会被终止。

安装文件：`~/.local/share/is-gpt-nerfed-linux/`；启动器：`~/.local/bin/nerfed-tray`；应用入口和登录自启动：`~/.local/share/applications/is-gpt-nerfed.desktop`、`~/.config/autostart/is-gpt-nerfed.desktop`。升级后重新执行安装脚本并重启托盘。日志位于 `~/.local/state/is-gpt-nerfed/`。

验证：`/usr/bin/python3 linux/nerfed-tray.py --demo --smoke-test`（只使用合成数据，不发起检测）。`--smoke-test` 不加 `--demo` 则读取真实会话并运行正常调度。一次只运行一个实例，后续启动会唤起已有窗口。

卸载：退出托盘，删除上述安装目录、启动器和两个 desktop 文件。不会移除 Codex 插件或检测记录。
