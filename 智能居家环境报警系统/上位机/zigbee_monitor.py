#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能家居室内环境警告系统  —— ZigBee 串口监控上位机
================================================================================
配套下位机工程：ZigBee_NewApp（CC2530F256 + Z-Stack 2.5.1a）
                 1 个协调器网关 + 1 个温湿度终端 + 1 个火焰终端

识别并展示的串口内容（协调器 UART0，9600-8-N-1）：

    [APP] ZigBee_NewApp gateway started                 -> 网关应用程序已启动
    [APP] ZigBee Coord Network ID: 918D                 -> 记录 PANID
    [APP] network formed, gateway ready                 -> 建网成功，网关就绪
    [GW] T1 addr=0xAF10 cnt=1 T=25.7C H=60.2%RH         -> 左侧温湿度卡片（只留最新一条）
    [GW] T2 addr=0x4C24 cnt=1 FLAME=FIRE / FLAME=SAFE   -> 右侧火焰卡片（只留最新一条）

说明：T1 / T2 是网关按终端短地址首次出现的顺序自动编号的，
      因此解析时按"帧内容"判断类型（含 FLAME= 的是火焰，含 T=..C H=..%RH 的是温湿度），
      不依赖编号，避免上电顺序不同导致左右卡片错位。

安装依赖：  pip install pyserial
运行方式：  python zigbee_monitor.py
调试参数：  python zigbee_monitor.py --demo        # 无硬件，跑模拟数据
            python zigbee_monitor.py --port COM3   # 指定串口直接连接
            python zigbee_monitor.py --selftest    # 只跑解析自检
================================================================================
"""

from __future__ import annotations

import argparse
import math
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import serial
    from serial.tools import list_ports
    HAS_PYSERIAL = True
except Exception:                                    # pragma: no cover
    HAS_PYSERIAL = False


# ==============================================================================
# 一、外观常量
# ==============================================================================
APP_TITLE = "智能家居室内环境警告系统"

BG        = "#F1F4F9"      # 窗口底色
CARD      = "#FFFFFF"      # 卡片底色
BORDER    = "#E1E7F0"      # 卡片描边
TXT       = "#1B2534"      # 主文字
MUTED     = "#8493A8"      # 次要文字
FAINT     = "#B0BACB"      # 更淡的说明文字
BLUE      = "#2F6BFF"      # 温度主题色
CYAN      = "#0EA5A5"      # 湿度主题色
RED       = "#E5484D"      # 火警
GREEN     = "#16A34A"      # 安全
AMBER     = "#F59E0B"      # 进行中
GREY      = "#94A3B8"      # 等待

FONT_UI   = "Microsoft YaHei UI"
FONT_MONO = "Consolas"


# ==============================================================================
# 二、颜色工具（Canvas 没有透明度，用混色模拟发光 / 淡出）
# ==============================================================================
def _rgb(color: str):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def blend(c1: str, c2: str, t: float) -> str:
    """按比例 t（0~1）把颜色 c1 混向 c2。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return "#%02X%02X%02X" % (
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


# ==============================================================================
# 三、串口日志解析规则
# ==============================================================================
RE_NETID  = re.compile(r"Network ID:\s*([0-9A-Fa-f]{2,4})")
RE_TERM   = re.compile(r"\bT(\d+)\b")
RE_ADDR   = re.compile(r"addr=0x([0-9A-Fa-f]{2,8})")
RE_CNT    = re.compile(r"cnt=(\d+)")
RE_TEMP   = re.compile(r"T=(-?\d+(?:\.\d+)?)C")
RE_HUM    = re.compile(r"H=(\d+(?:\.\d+)?)%RH")
RE_FLAME  = re.compile(r"FLAME=(FIRE|SAFE)")


def parse_line(line: str) -> dict:
    """把一行串口文本解析成字典；不含有效字段时返回空字典。"""
    info = {}

    m = RE_FLAME.search(line)
    if m:
        info["kind"]  = "flame"
        info["flame"] = (m.group(1) == "FIRE")

    m = RE_TEMP.search(line)
    if m:
        info["kind"] = "sensor"
        info["temp"] = float(m.group(1))

    m = RE_HUM.search(line)
    if m:
        info["kind"] = "sensor"
        info["hum"]  = float(m.group(1))

    m = RE_ADDR.search(line)
    if m:
        info["addr"] = m.group(1).upper()

    m = RE_CNT.search(line)
    if m:
        info["cnt"] = int(m.group(1))

    m = RE_TERM.search(line)
    if m:
        info["term"] = int(m.group(1))

    return info


# ==============================================================================
# 三、串口读取线程
# ==============================================================================
class SerialWorker(threading.Thread):
    """后台按行读取串口，结果通过队列交给主线程（tkinter 不能跨线程操作控件）。"""

    def __init__(self, port: str, baud: int, out_q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self.port     = port
        self.baud     = baud
        self.out_q    = out_q
        self.stop_evt = stop_evt
        self.ser      = None

    def run(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
        except Exception as exc:
            self.out_q.put(("error", f"打开串口 {self.port} 失败：{exc}"))
            return

        self.out_q.put(("info", f"串口 {self.port} 已打开（{self.baud}-8-N-1）"))

        buf = b""
        while not self.stop_evt.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                if not self.stop_evt.is_set():
                    self.out_q.put(("error", f"读取串口出错：{exc}"))
                break

            if not chunk:
                continue

            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip("\r\n\t \x00")
                if text:
                    self.out_q.put(("line", text))

        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.out_q.put(("info", "串口已关闭"))

    def close(self) -> None:
        self.stop_evt.set()


# ==============================================================================
# 四、演示线程（没有硬件时验证界面）
# ==============================================================================
class DemoWorker(threading.Thread):
    """按真实固件的输出顺序灌入样例数据，用于离线演示。"""

    def __init__(self, out_q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self.out_q    = out_q
        self.stop_evt = stop_evt

    def run(self) -> None:
        opening = [
            (0.3, "[APP] ZigBee_NewApp gateway started"),
            (0.8, "[APP] ZigBee Coord Network ID: 918D"),
            (0.5, "[APP] network formed, gateway ready"),
            (0.9, "[APP] joined network"),
            (0.5, "[GW] T2 addr=0x4C24 cnt=1 FLAME=SAFE"),
            (0.6, "[APP] joined network"),
            (0.5, "[GW] T1 addr=0xAF10 cnt=1 T=25.7C H=60.2%RH"),
        ]
        for gap, line in opening:
            if self.stop_evt.wait(gap):
                return
            self.out_q.put(("line", line))

        cnt1, cnt2 = 1, 1
        temp, hum  = 25.7, 60.2
        tick = 0

        while not self.stop_evt.is_set():
            if self.stop_evt.wait(1.4):
                return

            cnt1 += 1
            temp = round(min(28.0, max(23.5, temp + random.uniform(-0.4, 0.5))), 1)
            hum  = round(min(66.0, max(52.0, hum + random.uniform(-0.6, 0.6))), 1)
            self.out_q.put(("line",
                            f"[GW] T1 addr=0xAF10 cnt={cnt1} T={temp}C H={hum}%RH"))
            tick += 1

            if tick % 2 == 0:
                cnt2 += 1
                state = "FIRE" if (tick // 2) % 4 == 3 else "SAFE"
                self.out_q.put(("line",
                                f"[GW] T2 addr=0x4C24 cnt={cnt2} FLAME={state}"))


# ==============================================================================
# 五、圆角状态徽标（Canvas 绘制）
# ==============================================================================
def _round_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class StatusBadge(tk.Canvas):
    """火焰状态大徽标：等待 / 安全 / 火警 三种外观。"""

    def __init__(self, master, width=320, height=118, **kw):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bg=CARD, **kw)
        # 注意：不能用 self._w / self._h，那是 tkinter.Misc 内部的控件路径名
        self._cw, self._ch = width, height
        self._main = self.create_text(width / 2, height * 0.42, text="等待数据",
                                      font=(FONT_UI, 21, "bold"),
                                      fill=MUTED, justify="center")
        self._sub  = self.create_text(width / 2, height * 0.74, text="尚未收到火焰上报",
                                      font=(FONT_UI, 9),
                                      fill=MUTED, justify="center")
        self._shape = None
        self._paint(None)

    def _paint(self, color):
        if self._shape is not None:
            self.delete(self._shape)
            self._shape = None
        if color:
            pts = _round_points(4, 4, self._cw - 4, self._ch - 4, 20)
            self._shape = self.create_polygon(pts, smooth=True, splinesteps=24,
                                              fill=color, outline="")
            self.tag_lower(self._shape)
        self.tag_raise(self._sub)
        self.tag_raise(self._main)

    def set_state(self, kind: str, main: str, sub: str) -> None:
        if kind == "fire":
            self._paint(RED)
            self.itemconfig(self._main, text=main, fill="#FFFFFF")
            self.itemconfig(self._sub,  text=sub,  fill="#FFE4E4")
        elif kind == "safe":
            self._paint(GREEN)
            self.itemconfig(self._main, text=main, fill="#FFFFFF")
            self.itemconfig(self._sub,  text=sub,  fill="#DCFCE7")
        else:
            self._paint(None)
            self.itemconfig(self._main, text=main, fill=MUTED)
            self.itemconfig(self._sub,  text=sub,  fill=FAINT)


# ==============================================================================
# 六、右下角「实时场景」小图
# ==============================================================================
class MiniScene(tk.Canvas):
    """艺术化小屋剪影：火警时窗口窜出跳动火苗，安全时暖灯 + 烟囱冒烟。

    温度还会实时影响窗口灯光的冷暖（冷蓝 -> 暖橙），作为温湿度的象形映射。
    """

    W, H = 236, 154

    def __init__(self, master, **kw):
        super().__init__(master, width=self.W, height=self.H,
                         bg=CARD, highlightthickness=0, **kw)
        self._cw, self._ch = self.W, self.H
        self.fire = None            # None 无数据 / True 有火 / False 安全
        self.temp = None            # ℃，用于窗口灯光冷暖
        self.phase = 0.0
        self._alive = True
        self.redraw()
        self._tick()

    # ---------------------------------------------------------------- 外部接口
    def set_state(self, fire=None, temp=None) -> None:
        dirty = False
        if fire is not None and fire != self.fire:
            self.fire = fire
            dirty = True
        if temp is not None and temp != self.temp:
            self.temp = temp
            dirty = True
        if dirty:
            self.redraw()

    # ---------------------------------------------------------------- 动画
    def _tick(self) -> None:
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
            if self.fire is not None:
                self.phase += 0.42
                self.redraw()
            self.after(120, self._tick)
        except tk.TclError:
            return

    def destroy(self) -> None:
        self._alive = False
        super().destroy()

    # ---------------------------------------------------------------- 绘制
    def redraw(self) -> None:
        self.delete("all")
        W, H = self._cw, self._ch
        gy = 128                             # 地面基准线
        fire = self.fire is True

        self.create_polygon(_round_points(1, 1, W - 1, H - 1, 14), smooth=True,
                            splinesteps=24, fill="#FBFCFE", outline=BORDER)

        # 火光光晕：多层同心圆做柔和渐变
        if fire:
            steps = 12
            for i in range(steps):
                f = i / (steps - 1)
                r = int(78 - 48 * f)
                self.create_oval(98 - r, 86 - r, 98 + r, 86 + r,
                                 fill=blend("#FBFCFE", "#FF7A3D", 0.02 + 0.26 * (f ** 1.7)),
                                 outline="")

        # 烟囱（先画，被屋顶压住一部分）
        self.create_rectangle(138, 36, 152, 64, fill="#E4E9F2", outline="#CBD5E3")
        # 屋顶
        self.create_polygon(62, 64, 118, 28, 174, 64,
                            fill="#DDE4EF", outline="#C3CEDE")
        # 屋身
        self.create_rectangle(72, 64, 164, gy, fill="#F3F6FB", outline="#C3CEDE")
        # 门
        self.create_rectangle(128, 100, 152, gy, fill="#D9E0EC", outline="#C3CEDE")
        self.create_oval(144, 112, 147, 115, fill="#B9C4D6", outline="")
        # 地面
        self.create_line(16, gy, W - 16, gy, fill="#DCE3EE", width=2)

        # 窗口 84..112 / 74..102
        wx1, wy1, wx2, wy2 = 84, 74, 112, 102
        if fire:
            self.create_rectangle(wx1, wy1, wx2, wy2, fill="#FFB84D", outline="#DE8A2E")
            mullion = "#E9B44C"
        elif self.fire is False:
            self.create_rectangle(wx1, wy1, wx2, wy2, fill=self._lamp_color(),
                                  outline="#C3CEDE")
            mullion = "#D8BE93"
        else:
            self.create_rectangle(wx1, wy1, wx2, wy2, fill="#E9EFF8", outline="#C3CEDE")
            mullion = "#C3CEDE"

        # 窗棂
        self.create_line((wx1 + wx2) // 2, wy1, (wx1 + wx2) // 2, wy2,
                         fill=mullion, width=1)
        self.create_line(wx1, (wy1 + wy2) // 2, wx2, (wy1 + wy2) // 2,
                         fill=mullion, width=1)

        # 亮着的窗户：左上角一块玻璃更亮
        if self.fire is False:
            self.create_rectangle(wx1 + 2, wy1 + 2, (wx1 + wx2) // 2 - 1,
                                  (wy1 + wy2) // 2 - 1,
                                  fill="#FFF6DF", outline="")

        # 火焰（盖住窗棂）
        if fire:
            self._draw_flames(98, wy2, burning=True)

        # 安全时烟囱冒烟
        if self.fire is False:
            self._draw_smoke(145, 32)

    def _lamp_color(self) -> str:
        t = self.temp
        if t is None:
            return "#E8EEF7"
        if t <= 18:
            return blend("#BFD6FF", "#E8EEF7", 0.15)     # 偏冷
        if t >= 30:
            return blend("#FFC98A", "#FFE7C2", 0.30)     # 偏暖
        return "#FFE3AE"

    def _draw_flames(self, cx, base_y, burning):
        if not burning:
            return
        layers = ((34, 38, "#FF6B2C", 0.0), (24, 29, "#FF9E1B", 1.2),
                  (14, 18, "#FFD84D", 2.4))
        for w, h, color, ph in layers:
            hh = h * (0.94 + 0.08 * math.sin(self.phase * 1.6 + ph))
            tilt = math.sin(self.phase + ph) * 1.8
            # 火苗 = 下方椭圆 + 上方三角，比样条曲线更像火
            self.create_oval(cx - w / 2, base_y - hh * 0.74,
                             cx + w / 2, base_y, fill=color, outline="")
            self.create_polygon(cx + tilt, base_y - hh,
                                cx + w * 0.32, base_y - hh * 0.56,
                                cx - w * 0.32, base_y - hh * 0.56,
                                fill=color, outline="")

    def _draw_smoke(self, x, y):
        for i in range(3):
            f = ((self.phase * 0.05) + i / 3.0) % 1.0
            px = x + f * 12
            py = y - f * 26
            r = 2.5 + f * 4.5
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill=blend("#FBFCFE", "#BFCBDB", 0.55 - 0.35 * f),
                             outline="")


# ==============================================================================
# 七、主界面
# ==============================================================================
class MonitorApp:

    def __init__(self, root: tk.Tk, auto_port: str = "", demo: bool = False):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker = None                 # SerialWorker / DemoWorker
        self.stop_evt: threading.Event | None = None
        self.line_count = 0
        self.panid = ""
        self.alarm_visible = False
        self.demo_requested = demo
        self._closing = False
        self._after_id = None

        self._init_fonts()
        self._init_window()
        self._init_style()
        self._build_ui()

        self.refresh_ports()
        if auto_port:
            self.port_var.set(auto_port)
            self.root.after(300, self.toggle_conn)
        elif demo:
            self.root.after(300, self.toggle_demo)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pump()

    # ---------------------------------------------------------------- 初始化
    def _init_fonts(self):
        global FONT_UI
        try:
            families = set(tkfont.families(self.root))
            for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
                if name in families:
                    FONT_UI = name
                    break
        except Exception:
            pass

    def _init_window(self):
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        w, h = 1120, 740
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.root.minsize(980, 660)

    def _init_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("temp.Horizontal.TProgressbar",
                        troughcolor="#EEF1F5", bordercolor="#EEF1F5",
                        background=BLUE, lightcolor=BLUE, darkcolor=BLUE, thickness=9)
        style.configure("hum.Horizontal.TProgressbar",
                        troughcolor="#EEF1F5", bordercolor="#EEF1F5",
                        background=CYAN, lightcolor=CYAN, darkcolor=CYAN, thickness=9)
        style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF")

    # ---------------------------------------------------------------- 界面搭建
    def _build_ui(self):
        self._build_header()

        # 火警横幅（默认隐藏）
        self.alarm = tk.Label(self.root, text="火警！火焰终端检测到明火，请立即检查",
                              font=(FONT_UI, 11, "bold"), bg=RED, fg="#FFFFFF",
                              padx=18, pady=8, anchor="w")

        body = tk.Frame(self.root, bg=BG)
        self.body = body
        body.pack(fill="x", padx=22, pady=(6, 0))
        body.columnconfigure(0, weight=1, uniform="card")
        body.columnconfigure(1, weight=1, uniform="card")

        self.card_l = self._make_card(body)
        self.card_l.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.card_r = self._make_card(body)
        self.card_r.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_sensor_card(self.card_l)
        self._build_flame_card(self.card_r)

        self._build_gateway_bar()
        self._build_bottom()
        self._fit_window()

    def _fit_window(self):
        """按内容实际需求调整窗口尺寸，避免高 DPI 下控件被截断。"""
        try:
            self.root.update_idletasks()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w = max(1120, self.root.winfo_reqwidth() + 6)
            h = max(740, self.root.winfo_reqheight() + 6)
            w = min(w, sw - 40)
            h = min(h, sh - 80)
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.root.minsize(min(980, w), min(660, h))
        except Exception:
            pass

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=22, pady=(16, 6))

        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, font=(FONT_UI, 18, "bold"),
                 bg=BG, fg=TXT).pack(anchor="w")
        tk.Label(left, text="ZigBee 室内环境监测  ·  1 协调器网关 + 温湿度终端 + 火焰终端",
                 font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(3, 0))

        ctrl = tk.Frame(head, bg=BG)
        ctrl.pack(side="right")

        tk.Label(ctrl, text="串口", font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(ctrl, textvariable=self.port_var, width=9,
                                    state="readonly", values=[])
        self.port_cb.pack(side="left", padx=(5, 6))

        self._button(ctrl, "刷新", self.refresh_ports,
                     "#E7ECF5", TXT, width=5).pack(side="left", padx=(0, 10))

        tk.Label(ctrl, text="波特率", font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(side="left")
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(ctrl, textvariable=self.baud_var, width=7, state="readonly",
                     values=["9600", "38400", "57600", "115200"]).pack(side="left", padx=(5, 10))

        self.btn_conn = self._button(ctrl, "连接", self.toggle_conn, BLUE, "#FFFFFF", width=7)
        self.btn_conn.pack(side="left", padx=(0, 8))
        self._button(ctrl, "演示模式", self.toggle_demo,
                     "#E7ECF5", TXT, width=9).pack(side="left", padx=(0, 12))

        self.dot = tk.Canvas(ctrl, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 10, 10, fill=GREY, outline="")
        self.dot.pack(side="left")
        self.conn_var = tk.StringVar(value="未连接")
        tk.Label(ctrl, textvariable=self.conn_var, font=(FONT_UI, 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(5, 0))

    def _make_card(self, parent):
        return tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)

    def _button(self, parent, text, cmd, bg, fg, width=6):
        btn = tk.Button(parent, text=text, command=cmd, width=width,
                        font=(FONT_UI, 9), bg=bg, fg=fg,
                        activebackground=bg, activeforeground=fg,
                        relief="flat", bd=0, cursor="hand2",
                        padx=8, pady=5, highlightthickness=0)
        return btn

    # ---------------------------------------------------------------- 左卡片
    def _build_sensor_card(self, card):
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(head, text="温湿度监测", font=(FONT_UI, 12, "bold"),
                 bg=CARD, fg=TXT).pack(side="left")
        self.sensor_tag = tk.Label(head, text="等待数据", font=(FONT_UI, 9),
                                   bg="#EEF2F9", fg=MUTED, padx=10, pady=3)
        self.sensor_tag.pack(side="right")

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="both", expand=True, padx=20, pady=(16, 0))
        row.columnconfigure(0, weight=1, uniform="m")
        row.columnconfigure(1, weight=1, uniform="m")

        # 温度
        tcol = tk.Frame(row, bg=CARD)
        tcol.grid(row=0, column=0, sticky="new", padx=(0, 6))
        tk.Label(tcol, text="温度", font=(FONT_UI, 10), bg=CARD, fg=MUTED).pack(anchor="w")
        tval = tk.Frame(tcol, bg=CARD)
        tval.pack(anchor="w")
        self.temp_var = tk.StringVar(value="--.-")
        tk.Label(tval, textvariable=self.temp_var, font=(FONT_UI, 42, "bold"),
                 bg=CARD, fg=BLUE).pack(side="left")
        tk.Label(tval, text="℃", font=(FONT_UI, 14), bg=CARD, fg=BLUE).pack(
            side="left", anchor="s", pady=(0, 16))
        self.temp_bar = ttk.Progressbar(tcol, maximum=50.0,
                                        style="temp.Horizontal.TProgressbar")
        self.temp_bar.pack(fill="x", pady=(4, 3))
        tk.Label(tcol, text="量程 0 ~ 50 ℃", font=(FONT_UI, 8),
                 bg=CARD, fg=FAINT).pack(anchor="w")

        # 湿度
        hcol = tk.Frame(row, bg=CARD)
        hcol.grid(row=0, column=1, sticky="new", padx=(6, 0))
        tk.Label(hcol, text="湿度", font=(FONT_UI, 10), bg=CARD, fg=MUTED).pack(anchor="w")
        hval = tk.Frame(hcol, bg=CARD)
        hval.pack(anchor="w")
        self.hum_var = tk.StringVar(value="--.-")
        tk.Label(hval, textvariable=self.hum_var, font=(FONT_UI, 42, "bold"),
                 bg=CARD, fg=CYAN).pack(side="left")
        tk.Label(hval, text="%RH", font=(FONT_UI, 13), bg=CARD, fg=CYAN).pack(
            side="left", anchor="s", pady=(0, 18))
        self.hum_bar = ttk.Progressbar(hcol, maximum=100.0,
                                       style="hum.Horizontal.TProgressbar")
        self.hum_bar.pack(fill="x", pady=(4, 3))
        tk.Label(hcol, text="量程 0 ~ 100 %RH", font=(FONT_UI, 8),
                 bg=CARD, fg=FAINT).pack(anchor="w")

        foot = tk.Frame(card, bg=CARD)
        foot.pack(fill="x", padx=20, pady=(10, 16))
        self.sensor_foot = tk.Label(foot, text="尚未收到温湿度上报",
                                    font=(FONT_UI, 9), bg=CARD, fg=MUTED)
        self.sensor_foot.pack(anchor="w")

    # ---------------------------------------------------------------- 右卡片
    def _build_flame_card(self, card):
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(head, text="火焰检测", font=(FONT_UI, 12, "bold"),
                 bg=CARD, fg=TXT).pack(side="left")
        self.flame_tag = tk.Label(head, text="等待数据", font=(FONT_UI, 9),
                                  bg="#EEF2F9", fg=MUTED, padx=10, pady=3)
        self.flame_tag.pack(side="right")

        wrap = tk.Frame(card, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=20, pady=(6, 0))
        self.badge = StatusBadge(wrap, width=330, height=124)
        self.badge.pack(anchor="center", pady=(6, 4))

        self.flame_desc = tk.Label(wrap, text="等待火焰终端上报…",
                                   font=(FONT_UI, 12, "bold"), bg=CARD, fg=MUTED)
        self.flame_desc.pack(anchor="center")

        foot = tk.Frame(card, bg=CARD)
        foot.pack(fill="x", padx=20, pady=(8, 16))
        self.flame_foot = tk.Label(foot, text="尚未收到火焰上报",
                                   font=(FONT_UI, 9), bg=CARD, fg=MUTED)
        self.flame_foot.pack(anchor="w")

    # ---------------------------------------------------------------- 网关状态条
    def _build_gateway_bar(self):
        bar = tk.Frame(self.root, bg=CARD, highlightthickness=1,
                       highlightbackground=BORDER)
        bar.pack(fill="x", padx=22, pady=(10, 0))
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=16, pady=9)

        self.gw_dot = tk.Canvas(inner, width=12, height=12, bg=CARD, highlightthickness=0)
        self.gw_dot_id = self.gw_dot.create_oval(2, 2, 10, 10, fill=GREY, outline="")
        self.gw_dot.pack(side="left")

        tk.Label(inner, text="网关状态", font=(FONT_UI, 9),
                 bg=CARD, fg=MUTED).pack(side="left", padx=(7, 8))

        self.gw_var = tk.StringVar(value="等待协调器上电…")
        self.gw_label = tk.Label(inner, textvariable=self.gw_var, font=(FONT_UI, 10, "bold"),
                                 bg=CARD, fg=MUTED)
        self.gw_label.pack(side="left")

        self.stat_var = tk.StringVar(value="已接收 0 行")
        tk.Label(inner, textvariable=self.stat_var, font=(FONT_UI, 9),
                 bg=CARD, fg=FAINT).pack(side="right")

    # ---------------------------------------------------------------- 日志区
    def _build_bottom(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=(8, 14))
        self.log_wrap = wrap

        # ---------------- 右下角：实时场景小图（先 pack right，保证贴右边）
        art = tk.Frame(wrap, bg=BG)
        art.pack(side="right", anchor="s", padx=(12, 0))
        tk.Label(art, text="实时场景", font=(FONT_UI, 9, "bold"),
                 bg=BG, fg=MUTED).pack(anchor="e")
        holder = tk.Frame(art, bg=CARD, highlightthickness=1,
                          highlightbackground=BORDER)
        holder.pack(pady=(4, 0))
        self.mini = MiniScene(holder)
        self.mini.pack()

        # ---------------- 左侧：通信日志
        side = tk.Frame(wrap, bg=BG)
        side.pack(side="left", fill="both", expand=True)

        top = tk.Frame(side, bg=BG)
        top.pack(fill="x", pady=(0, 4))
        tk.Label(top, text="通信日志", font=(FONT_UI, 9, "bold"),
                 bg=BG, fg=MUTED).pack(side="left")
        self._button(top, "清空", self.clear_log, "#E7ECF5", TXT, width=5).pack(side="right")
        self.btn_log = self._button(top, "隐藏日志", self.toggle_log, "#E7ECF5", TXT, width=8)
        self.btn_log.pack(side="right", padx=(0, 8))

        self.log_frame = tk.Frame(side, bg=BG)
        self.log_frame.pack(fill="both", expand=True)

        self.log = ScrolledText(self.log_frame, height=8, width=40, font=(FONT_MONO, 9),
                                bg="#FBFCFE", fg="#334155", relief="flat",
                                highlightthickness=1, highlightbackground=BORDER,
                                insertbackground=TXT, wrap="none")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("rx",   foreground="#334155")
        self.log.tag_config("app",  foreground=BLUE)
        self.log.tag_config("fire", foreground=RED)
        self.log.tag_config("safe", foreground=GREEN)
        self.log.tag_config("warn", foreground=AMBER)
        self.log.tag_config("sys",  foreground=MUTED)
        self.log.tag_config("err",  foreground=RED)
        self.log.configure(state="disabled")

    # ---------------------------------------------------------------- 串口操作
    def refresh_ports(self):
        ports = []
        if HAS_PYSERIAL:
            try:
                ports = [p.device for p in list_ports.comports()]
            except Exception:
                ports = []
        self.port_cb["values"] = ports
        cur = self.port_var.get()
        if ports and cur not in ports:
            self.port_var.set(ports[0])
        elif not ports:
            self.port_var.set("")
        if not ports:
            self._log("[系统] 未检测到可用串口，可点“演示模式”查看界面效果", "sys")

    def toggle_conn(self):
        if self.worker is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if not HAS_PYSERIAL:
            messagebox.showerror("缺少依赖",
                                 "未安装 pyserial。\n\n请在 PyCharm 终端执行：\n"
                                 "pip install pyserial")
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("提示", "请先选择串口（可点“刷新”重新扫描）")
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            baud = 9600

        self.stop_evt = threading.Event()
        self.worker = SerialWorker(port, baud, self.q, self.stop_evt)
        self.worker.start()

        self.conn_var.set(f"{port} 已连接")
        self.dot.itemconfig(self.dot_id, fill=GREEN)
        self.btn_conn.configure(text="断开", bg="#E5484D", activebackground="#E5484D")

    def disconnect(self):
        if self.stop_evt is not None:
            self.stop_evt.set()
        if self.worker is not None:
            try:
                self.worker.join(timeout=1.0)
            except Exception:
                pass
        self.worker = None
        self.stop_evt = None
        self.conn_var.set("未连接")
        self.dot.itemconfig(self.dot_id, fill=GREY)
        self.btn_conn.configure(text="连接", bg=BLUE, activebackground=BLUE)

    def toggle_demo(self):
        if isinstance(self.worker, DemoWorker):
            self.disconnect()
            return
        if self.worker is not None:
            self.disconnect()

        self.stop_evt = threading.Event()
        self.worker = DemoWorker(self.q, self.stop_evt)
        self.worker.start()

        self.conn_var.set("演示模式（模拟数据）")
        self.dot.itemconfig(self.dot_id, fill=AMBER)
        self.btn_conn.configure(text="断开", bg="#E5484D", activebackground="#E5484D")
        self._log("[系统] 已进入演示模式，按真实固件顺序灌入样例数据", "sys")

    # ---------------------------------------------------------------- 日志
    def _log(self, text: str, tag: str = "rx"):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n", tag)
        self.log.see("end")
        try:
            lines = int(self.log.index("end-1c").split(".")[0])
            if lines > 800:
                self.log.delete("1.0", "200.0")
        except Exception:
            pass
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.btn_log.configure(text="显示日志")
        else:
            self.log_frame.pack(fill="both", expand=True)
            self.btn_log.configure(text="隐藏日志")
    # ---------------------------------------------------------------- 队列泵
    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self.handle_line(payload)
                elif kind == "info":
                    self._log(f"[系统] {payload}", "sys")
                elif kind == "error":
                    self._log(f"[错误] {payload}", "err")
                    self.disconnect()
        except queue.Empty:
            pass
        self._after_id = self.root.after(60, self._pump)

    # ---------------------------------------------------------------- 核心：逐行处理
    def handle_line(self, line: str):
        self.line_count += 1
        self.stat_var.set(f"已接收 {self.line_count} 行" +
                          (f" · PANID {self.panid}" if self.panid else ""))

        # ---- 日志着色
        low = line.lower()
        if "failed" in low or "dropped" in low:
            tag = "warn"
        elif "FLAME=FIRE" in line:
            tag = "fire"
        elif "FLAME=SAFE" in line:
            tag = "safe"
        elif line.startswith("[APP]"):
            tag = "app"
        else:
            tag = "rx"
        self._log(line, tag)

        # ---- 网关状态识别
        if "gateway started" in line:
            self.set_gateway("started", "协调器（网关）应用程序已启动")

        m = RE_NETID.search(line)
        if m:
            self.panid = m.group(1).upper()
            self.stat_var.set(f"已接收 {self.line_count} 行 · PANID {self.panid}")

        if "network formed" in line:
            extra = f" · PANID {self.panid}" if self.panid else ""
            self.set_gateway("ready", f"协调器建网成功，网关就绪{extra}")

        # ---- 数据帧解析
        info = parse_line(line)
        kind = info.get("kind")

        if kind == "sensor":
            self.update_sensor(info)
        elif kind == "flame":
            self.update_flame(info)

    # ---------------------------------------------------------------- 刷新左卡片
    def update_sensor(self, info: dict):
        if "temp" in info:
            self.temp_var.set(f"{info['temp']:.1f}")
            self.temp_bar["value"] = max(0.0, min(50.0, info["temp"]))
        if "hum" in info:
            self.hum_var.set(f"{info['hum']:.1f}")
            self.hum_bar["value"] = max(0.0, min(100.0, info["hum"]))

        term = info.get("term")
        addr = info.get("addr")
        label = []
        if term:
            label.append(f"终端 T{term}")
        if addr:
            label.append(f"0x{addr}")
        self.sensor_tag.configure(text=" · ".join(label) if label else "温湿度终端",
                                  fg=BLUE, bg="#EAF0FF")

        cnt = info.get("cnt", 0)
        self.sensor_foot.configure(
            text=f"上报次数 {cnt}    ·    更新时间 {datetime.now().strftime('%H:%M:%S')}",
            fg=MUTED)

        if "temp" in info:
            self.mini.set_state(temp=info["temp"])

    # ---------------------------------------------------------------- 刷新右卡片
    def update_flame(self, info: dict):
        fire = info.get("flame", False)
        term = info.get("term")
        addr = info.get("addr")
        cnt  = info.get("cnt", 0)

        parts = []
        if term:
            parts.append(f"终端 T{term}")
        if addr:
            parts.append(f"0x{addr}")
        tag_text = " · ".join(parts) if parts else "火焰终端"

        if fire:
            self.badge.set_state("fire", "火警触发", "FLAME = FIRE")
            self.flame_desc.configure(text="检测到火焰，火警触发", fg=RED)
            self.flame_tag.configure(text=tag_text, fg="#FFFFFF", bg=RED)
            self.card_r.configure(highlightbackground=RED, highlightcolor=RED)
            self.mini.set_state(fire=True)
            if not self.alarm_visible:
                self.alarm.pack(fill="x", padx=22, pady=(4, 0), before=self.body)
                self.alarm_visible = True
        else:
            self.badge.set_state("safe", "环境安全", "FLAME = SAFE")
            self.flame_desc.configure(text="无火焰，环境安全", fg=GREEN)
            self.flame_tag.configure(text=tag_text, fg="#FFFFFF", bg=GREEN)
            self.card_r.configure(highlightbackground=BORDER, highlightcolor=BORDER)
            self.mini.set_state(fire=False)
            if self.alarm_visible:
                self.alarm.pack_forget()
                self.alarm_visible = False

        self.flame_foot.configure(
            text=f"上报次数 {cnt}    ·    更新时间 {datetime.now().strftime('%H:%M:%S')}",
            fg=MUTED)

    # ---------------------------------------------------------------- 网关状态条
    def set_gateway(self, state: str, text: str):
        if state == "ready":
            self.gw_dot.itemconfig(self.gw_dot_id, fill=GREEN)
            self.gw_label.configure(fg=GREEN)
        elif state == "started":
            self.gw_dot.itemconfig(self.gw_dot_id, fill=AMBER)
            self.gw_label.configure(fg=TXT)
        else:
            self.gw_dot.itemconfig(self.gw_dot_id, fill=GREY)
            self.gw_label.configure(fg=MUTED)
        self.gw_var.set(text)

    def on_close(self):
        self._closing = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        try:
            if self.stop_evt is not None:
                self.stop_evt.set()
            if self.worker is not None:
                self.worker.join(timeout=0.8)
        except Exception:
            pass
        self.root.destroy()


# ==============================================================================
# 七、命令行入口
# ==============================================================================
def run_selftest() -> int:
    samples = [
        "[APP] ZigBee_NewApp gateway started",
        "[APP] ZigBee Coord Network ID: 918D",
        "[APP] network formed, gateway ready",
        "[APP] joined network",
        "[GW] T2 addr=0x4C24 cnt=1 FLAME=FIRE",
        "[GW] T2 addr=0x4C24 cnt=2 FLAME=SAFE",
        "[GW] T1 addr=0xAF10 cnt=1 T=25.7C H=60.2%RH",
        "[GW] T1 addr=0xAF10 cnt=12 T=-3.4C H=9.9%RH",
        "[APP] not joined, report dropped",
    ]
    for s in samples:
        print(f"{s}\n    -> {parse_line(s)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="智能家居室内环境警告系统 上位机")
    parser.add_argument("--port", default="", help="直接连接的串口，如 COM3")
    parser.add_argument("--demo", action="store_true", help="启动即进入演示模式")
    parser.add_argument("--selftest", action="store_true", help="只运行解析自检后退出")
    parser.add_argument("--smoke", action="store_true", help="构建界面 1.5 秒后自动退出")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()

    root = tk.Tk()
    app = MonitorApp(root, auto_port=args.port, demo=args.demo)

    if args.smoke:
        root.after(1500, app.on_close)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
