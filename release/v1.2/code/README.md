# Auto Notify v1.2 - 源码

轻量级 Windows 定时通知工具，支持每日定时提醒和按日期一次性提醒。

## 功能特性

- 每日定时通知（每天重复）
- 按日期提醒（直接选择 年/月/日 + 时间，一次性通知，触发后自动停用；不可选择过去日期）
- 倒计时显示（还剩X天）
- Windows Toast 通知 + 自定义提示音
- 状态列直接点击切换启用/停用（无需额外按钮）
- 系统托盘常驻，关闭窗口 = 最小化到托盘，双击托盘图标恢复窗口
- 单实例锁，防止重复启动
- 列表按状态+时间自动排序
- 右键菜单快捷操作（编辑/切换状态/删除）
- 窗口位置/大小记忆
- 数据固定保存在系统目录，不随程序运行位置变化而分散

## 文件说明

```
code/
├── main.py           # 主程序（单文件）
├── requirements.txt  # Python 依赖
├── icon.png          # 应用图标（PNG）
├── icon.ico          # 应用图标（ICO，打包用）
└── README.md         # 本文件
```

## 环境要求

- Python 3.10+
- Windows 10/11

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 打包为 exe

需要安装 Nuitka：

```bash
pip install nuitka zstandard ordered-set
```

打包命令：

```bash
python -m nuitka --onefile --windows-console-mode=disable --enable-plugin=tk-inter --windows-icon-from-ico=icon.ico --include-data-files=icon.png=icon.png --output-dir=dist --assume-yes-for-downloads --zig main.py
```

输出 `dist/main.exe`，重命名为 `AutoNotify.exe` 即可。

> 若打包时报 `ZstdError: Unable to compress ... not enough memory`，为 Nuitka 默认压缩等级（22）在内存受限环境下的已知问题，加上 `--low-memory` 参数即可（压缩等级降为3，产物体积会略增，约20MB）。

## 自定义提示音

将自定义 WAV 文件命名为 `sound.wav`，放在数据目录下（见下）即可替换默认系统提示音。

## 数据存储

数据目录固定为：

```
C:\Users\{当前用户名}\AppData\Local\Auto-Notify\
├── notifications.json  # 通知数据
└── config.json          # 窗口位置等配置
```

该目录由 `main.py` 中的 `AUTO_NOTIFY_DIR` / `AUTO_NOTIFY_FILE` 常量硬编码，与程序（exe 或脚本）实际运行的位置无关，避免多处运行导致数据分散。如需更改保存位置，直接修改 `main.py` 顶部的 `AUTO_NOTIFY_DIR` 常量即可（无需运行时界面，因数据量极小）。首次运行会自动一次性迁移旧版本遗留在程序目录或 `%APPDATA%\AutoNotify` 下的数据。

## 许可

MIT License
