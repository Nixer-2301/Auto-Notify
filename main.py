# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox
import json
import shutil
import threading
import time
import os
import sys
import subprocess
import winsound
import ctypes
from datetime import datetime, timedelta

import pystray
from PIL import Image, ImageDraw

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#  Data storage location — fixed, hardcoded directory so notifications
#  never depend on where the exe/script happens to be launched from.
#  Change AUTO_NOTIFY_DIR below directly if a different location is
#  ever needed; no runtime UI for this since alarm data is tiny.
# ============================================================

AUTO_NOTIFY_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local")),
    "Auto-Notify",
)
os.makedirs(AUTO_NOTIFY_DIR, exist_ok=True)

AUTO_NOTIFY_FILE = os.path.join(AUTO_NOTIFY_DIR, "notifications.json")
CONFIG_FILE = os.path.join(AUTO_NOTIFY_DIR, "config.json")

# One-time migration of legacy data from older versions of this app that
# stored files next to the executable / script, or under %APPDATA%.
for _legacy_dir in (APP_DIR, os.path.join(os.environ.get("APPDATA", APP_DIR), "AutoNotify")):
    try:
        _legacy_data = os.path.join(_legacy_dir, "notifications.json")
        _legacy_cfg = os.path.join(_legacy_dir, "config.json")
        if os.path.exists(_legacy_data) and not os.path.exists(AUTO_NOTIFY_FILE):
            shutil.copy2(_legacy_data, AUTO_NOTIFY_FILE)
        if os.path.exists(_legacy_cfg) and not os.path.exists(CONFIG_FILE):
            shutil.copy2(_legacy_cfg, CONFIG_FILE)
    except OSError:
        pass

# ============================================================
#  Single instance lock (named mutex)
# ============================================================

_MUTEX_NAME = "Global\\AutoNotify_SingleInstance_Mutex"
_mutex_handle = None


def acquire_single_instance():
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False
    return True


# ============================================================
#  Config persistence (window geometry etc.)
# ============================================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except IOError:
        pass

# ============================================================
#  Notification system
# ============================================================

def _play_notification_sound():
    """Play a notification sound. Uses custom sound.wav if present, else system default."""
    custom = os.path.join(AUTO_NOTIFY_DIR, "sound.wav")
    if os.path.exists(custom):
        winsound.PlaySound(custom, winsound.SND_FILENAME | winsound.SND_ASYNC)
    else:
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)


try:
    from winotify import Notification

    def show_notification(title, message):
        toast = Notification(app_id="AutoNotify.App", title=title, msg=message, duration="short")
        toast.build()
        toast.show()
        _play_notification_sound()

except ImportError:
    def show_notification(title, message):
        ps = (
            '[Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")|Out-Null;'
            '[Reflection.Assembly]::LoadWithPartialName("System.Drawing")|Out-Null;'
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            '$n.Icon=[System.Drawing.SystemIcons]::Information;'
            '$n.BalloonTipIcon="Info";'
            f'$n.BalloonTipTitle="{title}";'
            f'$n.BalloonTipText="{message}";'
            "$n.Visible=$true;"
            "$n.ShowBalloonTip(5000);"
            "Start-Sleep 6;"
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        )
        _play_notification_sound()

# ============================================================
#  Data persistence
# ============================================================

def load_data():
    if os.path.exists(AUTO_NOTIFY_FILE):
        try:
            with open(AUTO_NOTIFY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_data(data):
    with open(AUTO_NOTIFY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================
#  Background scheduler
# ============================================================

class Scheduler:
    def __init__(self, on_fire, on_changed=None):
        self.on_fire = on_fire
        self.on_changed = on_changed
        self._running = False
        self._thread = None
        self._fired_today = {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def reset(self, nid=None):
        if nid is None:
            self._fired_today.clear()
        else:
            self._fired_today.pop(nid, None)

    @property
    def running(self):
        return self._running

    def tick(self, data):
        now = datetime.now()
        current = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")
        changed = False

        for item in data:
            if not item.get("enabled", True):
                continue

            ntype = item.get("type", "time")

            if ntype == "interval":
                trigger_date = item.get("trigger_date", "")
                trigger_time = item.get("trigger_time", "09:00")
                if today != trigger_date or current != trigger_time:
                    continue
            else:
                if item.get("time", "") != current:
                    continue

            nid = item["id"]
            if self._fired_today.get(nid) == today:
                continue
            self._fired_today[nid] = today

            self.on_fire(item["message"])

            if ntype == "interval":
                item["enabled"] = False
                changed = True

        if changed:
            save_data(data)
            if self.on_changed:
                self.on_changed()

    def _loop(self):
        while self._running:
            try:
                data = load_data()
                self.tick(data)
            except Exception:
                pass
            self._sleep(30)

    @staticmethod
    def _sleep(seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(min(0.5, deadline - time.time()))

# ============================================================
#  Unified dialog
# ============================================================

class EditDialog:
    def __init__(self, parent, title="添加通知", data=None):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.grab_set()

        main = ttk.Frame(self.top, padding=12)
        main.pack()

        # ---- type selector ----
        ttk.Label(main, text="类  型").grid(row=0, column=0, sticky=tk.W, pady=(0, 4))

        existing_type = data.get("type", "time") if data else "time"
        self.notify_type = tk.StringVar(value=existing_type)

        type_row = ttk.Frame(main)
        type_row.grid(row=1, column=0, sticky=tk.W, pady=(0, 12))

        self.rb_daily = ttk.Radiobutton(type_row, text="每日定时", variable=self.notify_type, value="time",
                                         command=self._on_type_changed)
        self.rb_daily.pack(side=tk.LEFT, padx=(0, 16))

        self.rb_interval = ttk.Radiobutton(type_row, text="按日期提醒", variable=self.notify_type, value="interval",
                                            command=self._on_type_changed)
        self.rb_interval.pack(side=tk.LEFT)

        # ---- daily: time picker ----
        self.daily_frame = ttk.Frame(main)
        self.daily_frame.grid(row=2, column=0, sticky=tk.W)

        ttk.Label(self.daily_frame, text="时  间").pack(anchor=tk.W, pady=(0, 4))
        time_row = ttk.Frame(self.daily_frame)
        time_row.pack(anchor=tk.W)
        default_h = data["time"].split(":")[0] if data and data.get("type") != "interval" else "09"
        default_m = data["time"].split(":")[1] if data and data.get("type") != "interval" else "00"
        self.hour_var = tk.StringVar(value=default_h)
        self.min_var = tk.StringVar(value=default_m)
        tk.Spinbox(time_row, from_=0, to=23, textvariable=self.hour_var, width=3,
                   font=("Consolas", 11), justify="center", buttonbackground="#e0e0e0").pack(side=tk.LEFT)
        ttk.Label(time_row, text=" : ", font=("", 11, "bold")).pack(side=tk.LEFT)
        tk.Spinbox(time_row, from_=0, to=59, textvariable=self.min_var, width=3,
                   font=("Consolas", 11), justify="center", buttonbackground="#e0e0e0").pack(side=tk.LEFT)
        ttk.Label(time_row, text="  (24小时制，每天重复)", foreground="gray").pack(side=tk.LEFT, padx=(4, 0))

        # ---- interval: days picker ----
        self.interval_frame = ttk.Frame(main)
        self.interval_frame.grid(row=2, column=0, sticky=tk.W)
        self.interval_frame.grid_remove()

        ttk.Label(self.interval_frame, text="选择日期").pack(anchor=tk.W, pady=(0, 4))
        date_row = ttk.Frame(self.interval_frame)
        date_row.pack(anchor=tk.W)

        today = datetime.now().date()
        if data and data.get("type") == "interval" and data.get("trigger_date"):
            try:
                existing_date = datetime.strptime(data["trigger_date"], "%Y-%m-%d").date()
            except ValueError:
                existing_date = today
        else:
            existing_date = today

        self.year_var = tk.StringVar(value=str(existing_date.year))
        self.month_var = tk.StringVar(value=f"{existing_date.month:02d}")
        self.day_var = tk.StringVar(value=f"{existing_date.day:02d}")

        tk.Spinbox(date_row, from_=today.year, to=today.year + 10, textvariable=self.year_var, width=5,
                   font=("Consolas", 11), justify="center", buttonbackground="#e0e0e0").pack(side=tk.LEFT)
        ttk.Label(date_row, text=" 年 ").pack(side=tk.LEFT)
        tk.Spinbox(date_row, from_=1, to=12, textvariable=self.month_var, width=3, format="%02.0f",
                   font=("Consolas", 11), justify="center", buttonbackground="#e0e0e0").pack(side=tk.LEFT)
        ttk.Label(date_row, text=" 月 ").pack(side=tk.LEFT)
        tk.Spinbox(date_row, from_=1, to=31, textvariable=self.day_var, width=3, format="%02.0f",
                   font=("Consolas", 11), justify="center", buttonbackground="#e0e0e0").pack(side=tk.LEFT)
        ttk.Label(date_row, text=" 日").pack(side=tk.LEFT)
        ttk.Label(self.interval_frame, text="  (不可早于今天，触发后自动停用)", foreground="gray").pack(anchor=tk.W, pady=(2, 0))

        ttk.Label(self.interval_frame, text="通知时间（可选）").pack(anchor=tk.W, pady=(8, 4))
        itime_row = ttk.Frame(self.interval_frame)
        itime_row.pack(anchor=tk.W)
        ih_default = data.get("trigger_time", "09:00").split(":") if data else ["09", "00"]
        self.ih_var = tk.StringVar(value=ih_default[0])
        self.im_var = tk.StringVar(value=ih_default[1])
        tk.Spinbox(itime_row, from_=0, to=23, textvariable=self.ih_var, width=3,
                   font=("Consolas", 11), justify="center").pack(side=tk.LEFT)
        ttk.Label(itime_row, text=" : ", font=("", 11, "bold")).pack(side=tk.LEFT)
        tk.Spinbox(itime_row, from_=0, to=59, textvariable=self.im_var, width=3,
                   font=("Consolas", 11), justify="center").pack(side=tk.LEFT)

        # ---- message ----
        msg_start_row = 3
        ttk.Label(main, text="通知内容").grid(row=msg_start_row, column=0, sticky=tk.W, pady=(12, 4))
        self.msg_var = tk.StringVar(value=data["message"] if data else "")
        entry = ttk.Entry(main, textvariable=self.msg_var, width=50, font=("", 10))
        entry.grid(row=msg_start_row + 1, column=0, sticky=tk.EW, pady=(0, 12))
        entry.focus_set()

        # ---- buttons ----
        btn_row = ttk.Frame(main)
        btn_row.grid(row=msg_start_row + 2, column=0, pady=(0, 4))
        ttk.Button(btn_row, text="确定", width=10, command=self._ok).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="取消", width=10, command=self.top.destroy).pack(side=tk.LEFT, padx=2)

        self.top.bind("<Return>", lambda e: self._ok())
        self.top.bind("<Escape>", lambda e: self.top.destroy())

        self._on_type_changed()
        self._centre(parent)

    def _on_type_changed(self):
        if self.notify_type.get() == "interval":
            self.daily_frame.grid_remove()
            self.interval_frame.grid()
        else:
            self.interval_frame.grid_remove()
            self.daily_frame.grid()

    def _centre(self, parent):
        self.top.update_idletasks()
        w = self.top.winfo_reqwidth()
        h = self.top.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.top.geometry(f"+{x}+{y}")

    def _ok(self):
        msg = self.msg_var.get().strip()
        if not msg:
            messagebox.showwarning("提示", "请输入通知内容", parent=self.top)
            return

        if self.notify_type.get() == "interval":
            try:
                y = int(self.year_var.get())
                mo = int(self.month_var.get())
                d = int(self.day_var.get())
                target_date = datetime(y, mo, d).date()
            except (ValueError, TypeError):
                messagebox.showwarning("提示", "请输入有效的日期", parent=self.top)
                return
            today = datetime.now().date()
            if target_date < today:
                messagebox.showwarning("提示", "不能选择过去的日期", parent=self.top)
                return
            try:
                h = int(self.ih_var.get())
                m = int(self.im_var.get())
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except ValueError:
                messagebox.showwarning("提示", "请输入有效的时间", parent=self.top)
                return
            from_date = today.strftime("%Y-%m-%d")
            trigger_date = target_date.strftime("%Y-%m-%d")
            self.result = {
                "type": "interval",
                "interval_days": (target_date - today).days,
                "from_date": from_date,
                "trigger_date": trigger_date,
                "trigger_time": f"{h:02d}:{m:02d}",
                "message": msg,
            }
        else:
            try:
                h = int(self.hour_var.get())
                m = int(self.min_var.get())
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except ValueError:
                messagebox.showwarning("提示", "请输入有效的时间 (00:00 - 23:59)", parent=self.top)
                return
            self.result = {"type": "time", "time": f"{h:02d}:{m:02d}", "message": msg}

        self.top.destroy()

# ============================================================
#  Main application
# ============================================================

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Auto Notify")
        self.root.minsize(520, 360)

        icon_png_path = os.path.join(APP_DIR, "icon.png")
        if not os.path.exists(icon_png_path) and getattr(sys, "frozen", False):
            icon_png_path = os.path.join(sys._MEIPASS, "icon.png")
        if os.path.exists(icon_png_path):
            try:
                img = tk.PhotoImage(file=icon_png_path)
                self.root.iconphoto(True, img)
            except Exception:
                pass

        self._set_app_id()
        self.scheduler = Scheduler(
            on_fire=lambda msg: show_notification("⏰ 定时提醒", msg),
            on_changed=lambda: self.root.after(0, self._refresh_list),
        )
        self._tray = None
        self._tray_setup()

        self._build_ui()
        self._refresh_list()

        self.scheduler.start()
        self._sync_status()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._centre_window()

        self.root.mainloop()

    def _set_app_id(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoNotify.App")
        except Exception:
            pass

    def _tray_setup(self):
        icon_path = os.path.join(APP_DIR, "icon.png")
        if not os.path.exists(icon_path) and getattr(sys, "frozen", False):
            icon_path = os.path.join(sys._MEIPASS, "icon.png")

        def _tray_image():
            if os.path.exists(icon_path):
                try:
                    return Image.open(icon_path)
                except Exception:
                    pass
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse([2, 2, 62, 62], fill="#4A90D9")
            d.ellipse([14, 14, 50, 50], outline="white", width=2)
            d.line([(32, 32), (32, 20)], fill="white", width=2)
            d.line([(32, 32), (24, 32)], fill="white", width=2)
            d.ellipse([30, 30, 34, 34], fill="white")
            return img

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", self._tray_quit),
        )
        self._tray = pystray.Icon("AutoNotify", _tray_image(), "Auto Notify - 定时通知助手", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_show(self, icon, item=None):
        self.root.after(0, self.root.deiconify)

    def _tray_quit(self, icon, item=None):
        try:
            cfg = load_config()
            cfg["geometry"] = self.root.geometry()
            save_config(cfg)
        except Exception:
            pass
        self.scheduler.stop()
        self._tray.stop()
        self.root.after(0, self.root.destroy)

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(fill=tk.X)
        ttk.Label(header, text="⏰ Auto Notify", font=("Microsoft YaHei UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="定时通知助手", foreground="gray",
                  font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10)

        tree_frame = ttk.Frame(self.root, padding=(10, 6, 10, 4))
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("#1", "#2", "#3", "#4")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("#1", text="类型")
        self.tree.heading("#2", text="触发时间")
        self.tree.heading("#3", text="通知内容")
        self.tree.heading("#4", text="状态")
        self.tree.column("#1", width=55, anchor=tk.CENTER, stretch=False)
        self.tree.column("#2", width=200, anchor=tk.CENTER, stretch=False)
        self.tree.column("#3", width=250)
        self.tree.column("#4", width=70, anchor=tk.CENTER, stretch=False)

        self.tree.tag_configure("disabled", foreground="#999999")

        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self._edit())
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Right-click context menu
        self._ctx_menu = tk.Menu(self.root, tearoff=0)
        self._ctx_menu.add_command(label="编辑", command=self._edit)
        self._ctx_menu.add_command(label="切换状态", command=self._toggle)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="删除", command=self._delete)

        actions = ttk.Frame(self.root, padding=(10, 0))
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="+ 添加", command=self._add).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="编辑", command=self._edit).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="删除", command=self._delete).pack(side=tk.LEFT, padx=2)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=(6, 0))
        ctrl = ttk.Frame(self.root, padding=(10, 4, 10, 6))
        ctrl.pack(fill=tk.X)

        self.btn_start = ttk.Button(ctrl, text="▶ 启动", command=self._start)
        self.btn_start.pack(side=tk.LEFT, padx=2)
        self.btn_stop = ttk.Button(ctrl, text="⏹ 停止", command=self._stop)
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        ttk.Button(ctrl, text="测试通知", command=self._test).pack(side=tk.LEFT, padx=2)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(6, 3)).pack(fill=tk.X, side=tk.BOTTOM)

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if col == "#4":  # 状态列：点击直接切换
            self.tree.selection_set(row)
            idx = int(row)
            data = load_data()
            if idx >= len(data):
                return
            data[idx]["enabled"] = not data[idx].get("enabled", True)
            if not data[idx]["enabled"]:
                self.scheduler.reset(data[idx]["id"])
            save_data(data)
            self._refresh_list()
            return "break"

    def _show_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self._ctx_menu.post(event.x_root, event.y_root)

    def _centre_window(self):
        cfg = load_config()
        geo = cfg.get("geometry")
        if geo:
            try:
                self.root.geometry(geo)
                return
            except Exception:
                pass
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _refresh_list(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        data = load_data()

        # Sort: enabled first, then by trigger time
        today = datetime.now().strftime("%Y-%m-%d")

        def sort_key(d):
            enabled = d.get("enabled", True)
            ntype = d.get("type", "time")
            if ntype == "interval":
                t = d.get("trigger_date", "9999-99-99") + " " + d.get("trigger_time", "09:00")
            else:
                t = today + " " + d.get("time", "99:99")
            return (0 if enabled else 1, t)

        data.sort(key=sort_key)
        save_data(data)

        for i, d in enumerate(data):
            ntype = d.get("type", "time")
            enabled = d.get("enabled", True)

            if ntype == "interval":
                type_str = "按日期"
                trigger_date = d.get("trigger_date", "")
                trigger_time = d.get("trigger_time", "09:00")
                # Calculate remaining days
                try:
                    td = datetime.strptime(trigger_date, "%Y-%m-%d").date()
                    delta = (td - datetime.now().date()).days
                    if delta > 0:
                        remaining = f"{trigger_date} {trigger_time} (还剩{delta}天)"
                    elif delta == 0:
                        remaining = f"今天 {trigger_time}"
                    else:
                        remaining = f"{trigger_date} {trigger_time} (已过期)"
                except (ValueError, TypeError):
                    remaining = trigger_date
            else:
                type_str = "每日"
                remaining = d.get("time", "?")

            status_str = "☑ 启用" if enabled else "☐ 停用"
            tags = ("disabled",) if not enabled else ()

            self.tree.insert("", tk.END, iid=str(i),
                             values=(type_str, remaining, d["message"], status_str),
                             tags=tags)

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选择一项通知")
            return None
        return int(sel[0])

    def _add(self):
        dlg = EditDialog(self.root, "添加通知")
        self.root.wait_window(dlg.top)
        if dlg.result:
            data = load_data()
            r = dlg.result
            if r["type"] == "interval":
                data.append({
                    "id": str(int(time.time() * 1000)),
                    "type": "interval",
                    "interval_days": r["interval_days"],
                    "from_date": r["from_date"],
                    "trigger_date": r["trigger_date"],
                    "trigger_time": r["trigger_time"],
                    "message": r["message"],
                    "enabled": True,
                })
            else:
                data.append({
                    "id": str(int(time.time() * 1000)),
                    "type": "time",
                    "time": r["time"],
                    "message": r["message"],
                    "enabled": True,
                })
            save_data(data)
            self._refresh_list()

    def _edit(self):
        idx = self._selected_index()
        if idx is None:
            return
        data = load_data()
        item = data[idx]

        dlg = EditDialog(self.root, "编辑通知", item)
        self.root.wait_window(dlg.top)
        if dlg.result:
            r = dlg.result
            if r["type"] == "time":
                item["type"] = "time"
                item["time"] = r["time"]
                item["message"] = r["message"]
                item.pop("interval_days", None)
                item.pop("from_date", None)
                item.pop("trigger_date", None)
                item.pop("trigger_time", None)
            else:
                item["type"] = "interval"
                item["interval_days"] = r["interval_days"]
                item["from_date"] = r["from_date"]
                item["trigger_date"] = r["trigger_date"]
                item["trigger_time"] = r["trigger_time"]
                item["message"] = r["message"]
            save_data(data)
            self.scheduler.reset(item["id"])
            self._refresh_list()

    def _delete(self):
        idx = self._selected_index()
        if idx is None:
            return
        data = load_data()
        item = data[idx]
        text = item["message"][:30] + ("..." if len(item["message"]) > 30 else "")
        if messagebox.askyesno("确认删除", f'确定删除 \"{text}\" 吗？'):
            self.scheduler.reset(item["id"])
            del data[idx]
            save_data(data)
            self._refresh_list()

    def _toggle(self):
        idx = self._selected_index()
        if idx is None:
            return
        data = load_data()
        data[idx]["enabled"] = not data[idx].get("enabled", True)
        if not data[idx]["enabled"]:
            self.scheduler.reset(data[idx]["id"])
        save_data(data)
        self._refresh_list()

    def _start(self):
        self.scheduler.start()
        self._sync_status()

    def _stop(self):
        self.scheduler.stop()
        self._sync_status()

    def _test(self):
        show_notification("🧪 Auto Notify 测试", "如果你能看到这条消息，通知系统工作正常！")

    def _sync_status(self):
        if self.scheduler.running:
            self.status_var.set("● 调度器运行中")
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
        else:
            self.status_var.set("○ 调度器已停止")
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_stop.configure(state=tk.DISABLED)

    def _on_close(self):
        # Save window geometry before hiding
        try:
            cfg = load_config()
            cfg["geometry"] = self.root.geometry()
            save_config(cfg)
        except Exception:
            pass
        self.root.withdraw()


if __name__ == "__main__":
    if not acquire_single_instance():
        # Another instance is already running
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Auto Notify", "程序已在运行中，请查看系统托盘。")
            root.destroy()
        except Exception:
            pass
        sys.exit(0)
    App()
