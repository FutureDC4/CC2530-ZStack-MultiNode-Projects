#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
室内温湿度监测系统  —— ZigBee 串口上位机（仪表盘 + 实时趋势）
================================================================================
配套下位机工程：ZigBee_NewApp（CC2530 + Z-Stack 2.5.1a，室内温湿度监测版）
                 1 个协调器网关 + 1 个温湿度终端

识别的串口行（协调器 UART0，9600-8-N-1）：

    [APP] ZigBee_NewApp gateway started
    [APP] network formed, gateway ready
    [GW] T1 addr=0xAF10 cnt=1 T=25.6C H=60.5%RH ENV=OK

    T1    节点编号     addr 终端短地址    cnt 上报次数
    T     温度（℃）    H    湿度（%RH）    ENV 网关侧舒适判定（OK / ALARM）

界面结构（与前面两个项目的界面刻意做得不一样）：

    ┌──────────────────────────────────────────────┐
    │ 顶部：标题 + 串口控制 + 连接指示灯            │
    ├──────────────────────┬───────────────────────┤
    │  温度半圆仪表         │   湿度半圆仪表         │
    ├──────────────────────┴───────────────────────┤
    │  环境趋势：双 Y 轴折线，最近 40 帧，温度带面积  │
    ├──────────────────────────────────────────────┤
    │ 底部：节点 / 帧数 / 更新时间 / 网关状态        │
    └──────────────────────────────────────────────┘

判定阈值（与下位机 NewApp.h 中的宏一致）：

    温度  < 15℃ 偏冷   15 ~ 30℃ 适宜   > 30℃ 偏热
    湿度  < 45%RH 偏干 45 ~ 85%RH 适宜  > 85%RH 偏湿

演示模式（没有硬件、没有串口时也能完整展示运行过程）：
    点右上角「演示」按钮，或运行时加 --demo 参数：

        python 室内温湿度监测.py --demo

    演示数据由 DemoWorker 生成，输出的文本行与协调器真实串口输出逐字一致，
    因此界面走的是和真实接线完全相同的解析 / 渲染路径。
    一轮约 48 秒，依次覆盖：夜间偏冷 → 清晨舒适 → 午后偏热偏干 → 梅雨偏湿。

安装依赖：  pip install pyserial      （仅真实串口需要，演示模式无需安装）
运行方式：  python 室内温湿度监测.py
            python 室内温湿度监测.py COM3     直接指定串口
            python 室内温湿度监测.py --demo   启动即进入演示模式
================================================================================
"""

from __future__ import annotations

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

try:
    import serial
    from serial.tools import list_ports
    HAS_PYSERIAL = True
except Exception:                                     # pragma: no cover
    HAS_PYSERIAL = False


# ==============================================================================
# 一、外观常量（深紫灰 + 珊瑚 / 薄荷，与前面几套界面区分开）
# ==============================================================================
APP_TITLE = "室内温湿度监测系统"

BG      = "#11121C"        # 窗口底色
CARD    = "#191B2A"        # 卡片底色
CARD_HI = "#1E2133"        # 卡片高亮底
BORDER  = "#2C3049"        # 描边
GRID    = "#232741"        # 图表网格
TXT     = "#EDEFFA"        # 主文字
MUTED   = "#8B91B4"        # 次要文字
FAINT   = "#5A5F80"        # 更淡

MINT    = "#5FE0C0"        # 适宜 / 正常
CORAL   = "#FF8A5B"        # 偏高 / 偏热
AMBER   = "#FFC15E"        # 偏干 / 提醒
SKY     = "#6FB8FF"        # 偏冷
VIOLET  = "#9AA6FF"        # 偏湿

TEMP_C  = CORAL            # 温度曲线主色
HUM_C   = MINT             # 湿度曲线主色

FONT_UI = "Microsoft YaHei UI"

# 舒适区间（与 NewApp.h 一致）
TEMP_LOW, TEMP_HIGH = 15.0, 30.0
HUM_LOW,  HUM_HIGH  = 45.0, 85.0

# 仪表量程
TEMP_MIN, TEMP_MAX = -10.0, 50.0
HUM_MIN,  HUM_MAX  = 0.0, 100.0


# ==============================================================================
# 二、颜色工具
# ==============================================================================
def _rgb(color: str):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def blend(c1: str, c2: str, t: float) -> str:
    """把 c1 按比例 t（0~1）混向 c2。Canvas 没有 alpha，只能预先混色。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return "#%02X%02X%02X" % (
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


# ==============================================================================
# 三、串口数据解析
# ==============================================================================
RE_TEMP = re.compile(r"T=(-?\d+(?:\.\d+)?)C")
RE_HUM  = re.compile(r"H=(\d+(?:\.\d+)?)%RH")
RE_TID  = re.compile(r"\bT(\d+)\b")
RE_ADDR = re.compile(r"addr=0x([0-9A-Fa-f]+)")
RE_CNT  = re.compile(r"cnt=(\d+)")
RE_ENV  = re.compile(r"ENV=(OK|ALARM)")


def parse_line(line: str) -> dict:
    """解析一行上报；既没有温度也没有湿度时返回空字典。"""
    m_temp = RE_TEMP.search(line)
    m_hum = RE_HUM.search(line)
    if not (m_temp or m_hum):
        return {}

    info = {}
    if m_temp:
        info["temp"] = float(m_temp.group(1))
    if m_hum:
        info["hum"] = float(m_hum.group(1))

    m = RE_TID.search(line)
    if m:
        info["tid"] = m.group(1)
    m = RE_ADDR.search(line)
    if m:
        info["addr"] = m.group(1).upper()
    m = RE_CNT.search(line)
    if m:
        info["cnt"] = int(m.group(1))
    m = RE_ENV.search(line)
    if m:
        info["env"] = m.group(1)
    return info


def judge_temp(t):
    if t is None:
        return None, MUTED, "等待数据"
    if t < TEMP_LOW:
        return "偏冷", SKY, "低于 %g℃，建议升温" % TEMP_LOW
    if t > TEMP_HIGH:
        return "偏热", CORAL if t <= TEMP_HIGH + 3 else "#FF6B81", "高于 %g℃，建议降温通风" % TEMP_HIGH
    return "适宜", MINT, "舒适区间 %g ~ %g℃" % (TEMP_LOW, TEMP_HIGH)


def judge_hum(h):
    if h is None:
        return None, MUTED, "等待数据"
    if h < HUM_LOW:
        return "偏干", AMBER, "低于 %g%%RH，建议加湿" % HUM_LOW
    if h > HUM_HIGH:
        return "偏湿", VIOLET, "高于 %g%%RH，建议除湿" % HUM_HIGH
    return "适宜", MINT, "舒适区间 %g ~ %g%%RH" % (HUM_LOW, HUM_HIGH)


# ==============================================================================
# 四、串口读取线程
# ==============================================================================
class SerialWorker(threading.Thread):
    """后台按行读串口，结果放进队列交给主线程。"""

    def __init__(self, port: str, baud: int, out_q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.out_q = out_q
        self.stop_evt = stop_evt
        self.ser = None

    def run(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
        except Exception as exc:
            self.out_q.put(("error", "打开串口 %s 失败：%s" % (self.port, exc)))
            return

        self.out_q.put(("info", "connected"))

        buf = b""
        while not self.stop_evt.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                if not self.stop_evt.is_set():
                    self.out_q.put(("error", "串口读取异常：%s" % exc))
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

    def close(self) -> None:
        self.stop_evt.set()


# ==============================================================================
# 五、演示数据源（无串口时使用）
# ==============================================================================
DEMO_SCENES = [
    # 名称,  虚拟时刻(h), 温度℃, 湿度%RH
    ("夜间",  2.0, 14.2, 70.0),      # 偏冷
    ("清晨",  7.0, 22.8, 66.0),      # 舒适
    ("午后", 13.0, 31.2, 42.0),      # 偏热 + 偏干
    ("梅雨", 19.0, 21.5, 88.0),      # 偏湿
    ("夜晚", 22.0, 25.4, 55.0),      # 舒适
]


class DemoWorker(threading.Thread):
    """演示模式数据源：不占用串口，按脚本生成协调器上报文本行。

    产出的字符串与真实固件逐字一致（前缀 [GW]、字段名、单位都相同），
    统一塞进同一个队列，所以主线程完全不区分"数据来自串口还是演示"。
    """

    TICK = 1.2            # 每帧上报间隔（秒）
    HOLD = 9.6            # 每个场景保持时间（秒）

    def __init__(self, out_q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self.out_q = out_q
        self.stop_evt = stop_evt

    # ------------------------------------------------------------ 环境模型
    def _sample(self, elapsed: float):
        n = len(DEMO_SCENES)
        ph = elapsed % (self.HOLD * n)
        i = int(ph // self.HOLD)
        u = (ph - i * self.HOLD) / self.HOLD
        cur = DEMO_SCENES[i]
        nxt = DEMO_SCENES[(i + 1) % n]

        # 前 70% 时间保持当前场景，最后 30% 平滑过渡
        w = 0.0 if u < 0.70 else (u - 0.70) / 0.30
        w = w * w * (3.0 - 2.0 * w)                   # smoothstep

        hour_a = cur[1]
        hour_b = nxt[1] + (24.0 if nxt[1] < hour_a else 0.0)
        temp = cur[2] + (nxt[2] - cur[2]) * w
        hum = cur[3] + (nxt[3] - cur[3]) * w
        hour = (hour_a + (hour_b - hour_a) * w) % 24.0
        return temp, hum, hour

    # ------------------------------------------------------------ 线程主体
    def run(self) -> None:
        # ---- 上电启动过程，与协调器真实串口输出逐字一致
        for delay, text in ((0.6, "[APP] ZigBee_NewApp gateway started"),
                            (1.6, "[APP] network formed, gateway ready")):
            if self.stop_evt.wait(delay):
                return
            self.out_q.put(("line", text))

        t0 = time.time()
        cnt = 0

        while not self.stop_evt.is_set():
            elapsed = time.time() - t0
            temp, hum, hour = self._sample(elapsed)

            # 轻微抖动，避免曲线完全平滑、看着像假数据
            temp += random.uniform(-0.08, 0.08)
            hum = max(1.0, min(99.0, hum + random.uniform(-0.25, 0.25)))

            ok = (TEMP_LOW <= temp <= TEMP_HIGH) and (HUM_LOW <= hum <= HUM_HIGH)
            cnt += 1

            self.out_q.put(("line",
                            "[GW] T1 addr=0xAF10 cnt=%d T=%.1fC H=%.1f%%RH ENV=%s"
                            % (cnt, temp, hum, "OK" if ok else "ALARM")))
            self.out_q.put(("demo_clock",
                            "%02d:%02d" % (int(hour), int((hour % 1.0) * 60))))

            if self.stop_evt.wait(self.TICK):
                return


# ==============================================================================
# 六、半圆仪表
# ==============================================================================
class ArcGauge(tk.Canvas):
    """半圆表盘：底弧 + 彩色进度弧 + 指针 + 大号数值。

    与前面项目的"数字卡片"不同，这里用真正的表盘表达"当前读数在量程中的位置"。
    """

    def __init__(self, master, lo, hi, unit, title, **kw):
        super().__init__(master, highlightthickness=0, bg=CARD, **kw)
        self.lo, self.hi = float(lo), float(hi)
        self.unit = unit
        self.title = title
        self.value = None
        self.color = MUTED
        self.note = "等待数据"
        self.bind("<Configure>", lambda e: self.redraw())

    def set_value(self, value, color, note):
        self.value = value
        self.color = color
        self.note = note
        self.redraw()

    # ------------------------------------------------------------------ 绘制
    def redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 80:
            return
        self.delete("all")

        cx = w / 2.0
        cy = h * 0.86
        r = min(w * 0.40, h * 0.62)
        thick = max(9, int(r * 0.10))

        x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r

        # 底弧
        self.create_arc(x0, y0, x1, y1, start=180, extent=-180,
                        style="arc", outline=GRID, width=thick)

        frac = 0.0
        if self.value is not None:
            frac = (self.value - self.lo) / (self.hi - self.lo)
            frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)

        # 进度弧 + 弧端指示点（不用指针，避免压住中间的大字）
        if frac > 0.004:
            self.create_arc(x0, y0, x1, y1, start=180, extent=-180 * frac,
                            style="arc", outline=self.color, width=thick)

            a = math.radians(180 - 180 * frac)
            kx = cx + math.cos(a) * r
            ky = cy - math.sin(a) * r
            kr = thick * 0.66
            self.create_oval(kx - kr, ky - kr, kx + kr, ky + kr,
                             fill=blend(CARD, self.color, 0.30), outline="")
            self.create_oval(kx - kr * 0.52, ky - kr * 0.52,
                             kx + kr * 0.52, ky + kr * 0.52,
                             fill=self.color, outline="")

        # 刻度：0 / 25 / 50 / 75 / 100%
        for i in range(5):
            a = math.radians(180 - 36 * i)
            rr = r + thick * 0.55
            self.create_line(cx + math.cos(a) * (r - thick * 0.60),
                             cy - math.sin(a) * (r - thick * 0.60),
                             cx + math.cos(a) * rr,
                             cy - math.sin(a) * rr,
                             fill=FAINT, width=1)

        # 标题
        self.create_text(cx, h * 0.12, text=self.title,
                         fill=MUTED, font=(FONT_UI, 10))

        # 数值（无数据时显示 --）
        num = "--" if self.value is None else "%.1f" % self.value
        color = self.color if self.value is not None else FAINT
        fsize = int(min(34, r * 0.26))
        self.create_text(cx + 12, cy - r * 0.52, text=num, anchor="e",
                         fill=color, font=(FONT_UI, fsize, "bold"))
        self.create_text(cx + 17, cy - r * 0.45, text=self.unit, anchor="w",
                         fill=blend(color, MUTED, 0.35), font=(FONT_UI, 10))

        # 判定文字
        self.create_text(cx, cy - r * 0.20, text=self.note,
                         fill=blend(color, MUTED, 0.30), font=(FONT_UI, 9))

        # 量程两端
        self.create_text(cx - r, cy + 9, text="%g" % self.lo, anchor="n",
                         fill=FAINT, font=(FONT_UI, 8))
        self.create_text(cx + r, cy + 9, text="%g" % self.hi, anchor="n",
                         fill=FAINT, font=(FONT_UI, 8))


# ==============================================================================
# 七、实时趋势图（双 Y 轴）
# ==============================================================================
class TrendChart(tk.Canvas):
    """最近 N 帧的温度 / 湿度折线：左轴温度，右轴湿度，温度带渐变面积。"""

    WINDOW = 40

    def __init__(self, master, **kw):
        super().__init__(master, highlightthickness=0, bg=CARD, **kw)
        self.samples = []          # [(temp, hum, clock)]
        self.bind("<Configure>", lambda e: self.redraw())

    def push(self, temp, hum, clock) -> None:
        self.samples.append((temp, hum, clock))
        if len(self.samples) > self.WINDOW:
            del self.samples[:-self.WINDOW]
        self.redraw()

    def clear(self) -> None:
        self.samples = []
        self.redraw()

    # ------------------------------------------------------------------ 绘制
    def redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 120 or h < 90:
            return
        self.delete("all")

        L, R, T, B = 44, 50, 16, 22
        pw, ph = w - L - R, h - T - B
        if pw < 20 or ph < 20:
            return

        # ---- 横向网格 + 左右刻度（左温度 / 右湿度）
        for i in range(5):
            y = T + ph * i / 4.0
            self.create_line(L, y, w - R, y,
                             fill=BORDER if i in (0, 4) else GRID, width=1)
            tv = TEMP_MAX - (TEMP_MAX - TEMP_MIN) * i / 4.0
            hv = HUM_MAX - (HUM_MAX - HUM_MIN) * i / 4.0
            self.create_text(L - 8, y, text="%g" % tv, anchor="e",
                             fill=FAINT, font=(FONT_UI, 8))
            self.create_text(w - R + 8, y, text="%g" % hv, anchor="w",
                             fill=FAINT, font=(FONT_UI, 8))

        self.create_text(L - 8, T - 8, text="℃", anchor="e",
                         fill=blend(FAINT, TEMP_C, 0.55), font=(FONT_UI, 8))
        self.create_text(w - R + 8, T - 8, text="%RH", anchor="w",
                         fill=blend(FAINT, HUM_C, 0.55), font=(FONT_UI, 8))

        n = len(self.samples)
        if n == 0:
            self.create_text(L + pw / 2.0, T + ph / 2.0, text="等待数据…",
                             fill=FAINT, font=(FONT_UI, 11))
            return

        def px(i):
            return L + pw * i / float(self.WINDOW - 1)

        def py_t(v):
            k = (v - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)
            k = 0.0 if k < 0 else (1.0 if k > 1 else k)
            return T + ph * (1.0 - k)

        def py_h(v):
            k = (v - HUM_MIN) / (HUM_MAX - HUM_MIN)
            k = 0.0 if k < 0 else (1.0 if k > 1 else k)
            return T + ph * (1.0 - k)

        tp = [(px(i), py_t(s[0])) for i, s in enumerate(self.samples)]
        hp = [(px(i), py_h(s[1])) for i, s in enumerate(self.samples)]

        # ---- 温度面积（Canvas 无 alpha，用与底色混合的实色代替）
        if len(tp) >= 2:
            poly = []
            for x, y in tp:
                poly.extend((x, y))
            poly.extend((tp[-1][0], T + ph, tp[0][0], T + ph))
            self.create_polygon(poly, fill=blend(CARD, TEMP_C, 0.10),
                                outline="")

        # ---- 曲线：外层淡描边当作发光，内层实色
        if len(tp) >= 2:
            self.create_line([c for p in tp for c in p], fill=blend(CARD, TEMP_C, 0.45),
                             width=5, smooth=True, splinesteps=12)
            self.create_line([c for p in tp for c in p], fill=TEMP_C,
                             width=2, smooth=True, splinesteps=12)
            self.create_line([c for p in hp for c in p], fill=blend(CARD, HUM_C, 0.40),
                             width=5, smooth=True, splinesteps=12)
            self.create_line([c for p in hp for c in p], fill=HUM_C,
                             width=2, smooth=True, splinesteps=12)

        # ---- 当前值端点
        for p, color in ((tp[-1], TEMP_C), (hp[-1], HUM_C)):
            x, y = p
            self.create_oval(x - 5, y - 5, x + 5, y + 5,
                             fill=blend(CARD, color, 0.30), outline="")
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")

        # ---- 时间标注（首 / 尾）
        self.create_text(L, T + ph + 11, text=self.samples[0][2], anchor="w",
                         fill=FAINT, font=(FONT_UI, 8))
        self.create_text(L + pw, T + ph + 11, text=self.samples[-1][2], anchor="e",
                         fill=FAINT, font=(FONT_UI, 8))
        if n >= 2:
            self.create_text(L + pw * 0.5, T + ph + 11,
                             text="%d 帧" % n, fill=blend(FAINT, BG, 0.15),
                             font=(FONT_UI, 8))


# ==============================================================================
# 八、主界面
# ==============================================================================
class MonitorApp:

    def __init__(self, root: tk.Tk, auto_port: str = "", auto_demo: bool = False):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker = None
        self.stop_evt = None
        self.demo_worker = None
        self.demo_stop = None
        self.demo_clock = None
        self._closing = False
        self._after_id = None

        self.st = {"id": None, "addr": None, "cnt": 0, "temp": None, "hum": None}
        self.gw_ready = False

        self._init_fonts()
        self._init_window()
        self._init_style()
        self._build_ui()
        self._fit_window()

        self.refresh_ports()
        if auto_demo:
            self.root.after(400, self.start_demo)
        elif auto_port:
            self.port_var.set(auto_port)
            self.root.after(300, self.toggle_conn)

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
        w, h = 1180, 780
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        self.root.minsize(1000, 700)

    def _fit_window(self):
        """按内容实际需要收缩窗口，避免高 DPI 下右侧控制区被裁掉。"""
        try:
            self.root.update_idletasks()
            w = max(self.root.winfo_reqwidth(), 1000)
            h = max(self.root.winfo_reqheight(), 700)
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w, h = min(w, sw - 40), min(h, sh - 80)
            self.root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        except Exception:
            pass

    def _init_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground="#1B1F31", background="#1B1F31",
                        foreground=TXT, arrowcolor=MUTED,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        selectbackground="#1B1F31", selectforeground=TXT,
                        insertcolor=TXT)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", "#1B1F31"), ("disabled", "#1B1F31")],
                  foreground=[("readonly", TXT), ("disabled", FAINT)],
                  background=[("readonly", "#1B1F31"), ("active", "#2C3049")],
                  selectbackground=[("readonly", "#1B1F31")],
                  selectforeground=[("readonly", TXT)],
                  arrowcolor=[("readonly", MUTED), ("active", TXT)])
        self.root.option_add("*TCombobox*Listbox.background", "#1B1F31")
        self.root.option_add("*TCombobox*Listbox.foreground", TXT)

    # ---------------------------------------------------------------- 界面
    def _build_ui(self):
        self._build_header()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(2, 0))
        body.columnconfigure(0, weight=1, uniform="g")
        body.columnconfigure(1, weight=1, uniform="g")
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=1)

        # ---- 两张仪表
        self.card_t = self._panel(body)
        self.card_t.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        self.gauge_t = ArcGauge(self.card_t, TEMP_MIN, TEMP_MAX, "℃", "室内温度", height=272)
        self.gauge_t.pack(fill="both", expand=True, padx=3, pady=3)

        self.card_h = self._panel(body)
        self.card_h.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 10))
        self.gauge_h = ArcGauge(self.card_h, HUM_MIN, HUM_MAX, "%RH", "室内湿度", height=272)
        self.gauge_h.pack(fill="both", expand=True, padx=3, pady=3)

        # ---- 趋势卡片
        self.card_c = self._panel(body)
        self.card_c.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.card_c.rowconfigure(1, weight=1)
        self.card_c.columnconfigure(0, weight=1)

        head = tk.Frame(self.card_c, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        tk.Label(head, text="环境趋势", font=(FONT_UI, 10), bg=CARD, fg=MUTED) \
            .pack(side="left")

        self.comfort_var = tk.StringVar(value="等待数据")
        self.comfort_label = tk.Label(head, textvariable=self.comfort_var,
                                      font=(FONT_UI, 9), bg=CARD, fg=FAINT)
        self.comfort_label.pack(side="right")

        self._legend(head, HUM_C, "湿度 %RH")
        self._legend(head, TEMP_C, "温度 ℃")

        self.chart = TrendChart(self.card_c, height=200)
        self.chart.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))

        self._build_footer()

    def _panel(self, parent):
        return tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER)

    def _legend(self, parent, color, text):
        box = tk.Frame(parent, bg=CARD)
        box.pack(side="right", padx=(0, 14))
        dot = tk.Canvas(box, width=18, height=8, bg=CARD, highlightthickness=0)
        dot.create_line(0, 4, 18, 4, fill=color, width=3)
        dot.pack(side="left")
        tk.Label(box, text=text, font=(FONT_UI, 8), bg=CARD, fg=MUTED) \
            .pack(side="left", padx=(4, 0))

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 8))

        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, font=(FONT_UI, 17, "bold"),
                 bg=BG, fg=TXT).pack(anchor="w")
        tk.Label(left, text="ZigBee 温湿度节点  ·  协调器 + 单节点星型组网",
                 font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        ctrl = tk.Frame(head, bg=BG)
        ctrl.pack(side="right")

        tk.Label(ctrl, text="串口", font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(ctrl, textvariable=self.port_var, width=9,
                                    state="readonly", values=[], style="Dark.TCombobox")
        self.port_cb.pack(side="left", padx=(5, 6))

        self._button(ctrl, "刷新", self.refresh_ports, "#1B1F31", MUTED, 5) \
            .pack(side="left", padx=(0, 10))

        self.baud_var = tk.StringVar(value="9600")
        self.baud_cb = ttk.Combobox(ctrl, textvariable=self.baud_var, width=7,
                                    state="readonly", values=["9600", "38400", "115200"],
                                    style="Dark.TCombobox")
        self.baud_cb.pack(side="left", padx=(0, 10))

        self.btn_conn = self._button(ctrl, "连接", self.toggle_conn, MINT, "#0B2B24", 6)
        self.btn_conn.pack(side="left", padx=(0, 8))

        self.btn_demo = self._button(ctrl, "演示", self.toggle_demo, "#1F2440", "#A9B4FF", 6)
        self.btn_demo.pack(side="left", padx=(0, 12))

        self.dot = tk.Canvas(ctrl, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=FAINT, outline="")
        self.dot.pack(side="left")
        self.conn_var = tk.StringVar(value="未连接")
        tk.Label(ctrl, textvariable=self.conn_var, font=(FONT_UI, 9),
                 bg=BG, fg=MUTED).pack(side="left", padx=(6, 0))

    def _button(self, parent, text, cmd, bg, fg, width=6):
        return tk.Button(parent, text=text, command=cmd, width=width,
                         font=(FONT_UI, 9), bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg,
                         relief="flat", bd=0, cursor="hand2",
                         padx=8, pady=5, highlightthickness=0)

    def _build_footer(self):
        bar = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x", padx=18, pady=(10, 14))
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=16, pady=9)

        self.info_var = tk.StringVar(value="等待温湿度终端上报…")
        tk.Label(inner, textvariable=self.info_var, font=(FONT_UI, 9),
                 bg=CARD, fg=MUTED).pack(side="left")

        self.gw_var = tk.StringVar(value="网关 未就绪")
        self.gw_label = tk.Label(inner, textvariable=self.gw_var, font=(FONT_UI, 9),
                                 bg=CARD, fg=FAINT)
        self.gw_label.pack(side="right")

    # ---------------------------------------------------------------- 串口
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

    def toggle_conn(self):
        if self.demo_worker is not None:
            self.stop_demo()
        if self.worker is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if not HAS_PYSERIAL:
            messagebox.showerror("缺少依赖",
                                 "未安装 pyserial。\n\n请执行：\npip install pyserial")
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

        self.conn_var.set("%s 已连接" % port)
        self.dot.itemconfig(self.dot_id, fill=MINT)
        self.btn_conn.configure(text="断开", bg="#331E28", fg="#FF7C8E")

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
        self.dot.itemconfig(self.dot_id, fill=FAINT)
        self.btn_conn.configure(text="连接", bg=MINT, fg="#0B2B24")
        self._reset_state()

    # ---------------------------------------------------------------- 演示模式
    def toggle_demo(self):
        if self.demo_worker is not None:
            self.stop_demo()
        else:
            self.start_demo()

    def start_demo(self):
        """启动演示：不需要串口，也不会占用已连接的串口。"""
        if self.worker is not None:
            self.disconnect()
        self._reset_state()

        self.demo_stop = threading.Event()
        self.demo_worker = DemoWorker(self.q, self.demo_stop)
        self.demo_worker.start()

        self._set_demo_ui(True)
        self.conn_var.set("演示模式")
        self.dot.itemconfig(self.dot_id, fill=AMBER)
        self.info_var.set("演示模式：正在模拟协调器上电建网与节点上报…")

    def stop_demo(self):
        if self.demo_stop is not None:
            self.demo_stop.set()
        if self.demo_worker is not None:
            try:
                self.demo_worker.join(timeout=1.0)
            except Exception:
                pass
        self.demo_worker = None
        self.demo_stop = None

        # 丢掉队列里剩余的演示数据，否则停止后画面会再跳一下
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

        self._set_demo_ui(False)
        self.conn_var.set("未连接")
        self.dot.itemconfig(self.dot_id, fill=FAINT)
        self._reset_state()

    def _set_demo_ui(self, on: bool):
        """演示运行时禁用串口控件，避免两路数据源互相打架。"""
        self.port_cb.configure(state="disabled" if on else "readonly")
        self.baud_cb.configure(state="disabled" if on else "readonly")
        self.btn_conn.configure(state="disabled" if on else "normal",
                                bg="#1A1C2A" if on else MINT,
                                fg=FAINT if on else "#0B2B24")
        self.btn_demo.configure(text="停止演示" if on else "演示",
                                bg="#3A2611" if on else "#1F2440",
                                fg=AMBER if on else "#A9B4FF",
                                activebackground="#3A2611" if on else "#1F2440")

    def _reset_state(self):
        self.st.update({"id": None, "addr": None, "cnt": 0, "temp": None, "hum": None})
        self.gw_ready = False
        self.demo_clock = None
        self.gw_var.set("网关 未就绪")
        self.gw_label.configure(fg=FAINT)
        self.info_var.set("等待温湿度终端上报…")
        self.chart.clear()
        self._render()

    # ---------------------------------------------------------------- 队列
    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self.handle_line(payload)
                elif kind == "demo_clock":
                    self.demo_clock = payload
                elif kind == "error":
                    messagebox.showerror("串口错误", payload)
                    self.disconnect()
        except queue.Empty:
            pass
        self._after_id = self.root.after(60, self._pump)

    def handle_line(self, line: str):
        if "network formed" in line:
            self.gw_ready = True
            self.gw_var.set("网关 已就绪")
            self.gw_label.configure(fg=MINT)

        info = parse_line(line)
        if not info:
            return

        for k in ("temp", "hum", "tid", "addr", "cnt"):
            if k in info:
                self.st[k if k != "tid" else "id"] = info[k]

        if self.st["temp"] is not None and self.st["hum"] is not None:
            clock = self.demo_clock or datetime.now().strftime("%H:%M:%S")
            self.chart.push(self.st["temp"], self.st["hum"], clock)

        self._render()

    # ---------------------------------------------------------------- 渲染
    def _render(self):
        st = self.st
        temp, hum = st["temp"], st["hum"]

        word_t, color_t, note_t = judge_temp(temp)
        word_h, color_h, note_h = judge_hum(hum)

        self.gauge_t.set_value(temp, color_t, note_t)
        self.gauge_h.set_value(hum, color_h, note_h)
        self.card_t.configure(highlightbackground=blend(CARD, color_t, 0.45))
        self.card_h.configure(highlightbackground=blend(CARD, color_h, 0.45))

        # ---- 舒适度徽标
        if temp is None or hum is None:
            self.comfort_var.set("等待数据")
            self.comfort_label.configure(fg=FAINT)
        else:
            bad = []
            if word_t != "适宜":
                bad.append(word_t)
            if word_h != "适宜":
                bad.append(word_h)
            if bad:
                self.comfort_var.set("环境提示：" + " · ".join(bad))
                self.comfort_label.configure(fg=blend(CARD, AMBER, 0.95))
            else:
                self.comfort_var.set("环境舒适")
                self.comfort_label.configure(fg=MINT)

        # ---- 底部
        parts = []
        if st["id"]:
            addr = "  0x%s" % st["addr"] if st["addr"] else ""
            parts.append("温湿度节点 T%s%s" % (st["id"], addr))
        elif st["temp"] is not None:
            parts.append("温湿度终端（串口直连本机）")

        if parts:
            parts.append("累计上报 %d 帧" % (st["cnt"] or 0))
            parts.append("更新 " + datetime.now().strftime("%H:%M:%S"))
            if self.demo_clock:
                parts.insert(0, "演示模式")
                parts.append("虚拟时间 " + self.demo_clock)
            self.info_var.set("    ·    ".join(parts))

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
            if self.demo_stop is not None:
                self.demo_stop.set()
            if self.demo_worker is not None:
                self.demo_worker.join(timeout=0.8)
        except Exception:
            pass
        self.root.destroy()


# ==============================================================================
# 九、入口
# ==============================================================================
def main() -> int:
    argv = sys.argv[1:]
    auto_demo = any(a in ("--demo", "-d") for a in argv)
    rest = [a for a in argv if not a.startswith("-")]
    auto_port = rest[0] if rest else ""

    root = tk.Tk()
    MonitorApp(root, auto_port=auto_port, auto_demo=auto_demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
