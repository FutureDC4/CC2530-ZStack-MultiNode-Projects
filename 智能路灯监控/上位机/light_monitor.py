#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路灯智能监控系统  —— ZigBee 光敏路灯串口上位机
================================================================================
配套下位机工程：ZigBee_NewApp（CC2530 + Z-Stack 2.5.1a，路灯控制版）
                 1 个协调器网关 + 光敏路灯终端

识别的串口行（协调器 UART0，9600-8-N-1）：

    [GW] L1 addr=0x796F cnt=4 LUX=1260 ADC=160 LEVEL=DAY LAMP=OFF MODE=AUTO

    字段：L1    路灯编号        addr  终端短地址
          cnt   上报次数        LUX   光照强度 lx
          ADC   光敏原始采样    LEVEL 光照等级 NIGHT / DUSK / DAY
          LAMP  路灯状态        ON 开灯 / OFF 关灯
          MODE  工作模式        AUTO 自动 / MANUAL_ON 手动开 / MANUAL_OFF 手动关

界面只做两件事：显示"光亮充足 / 光亮不足"与"路灯打开 / 路灯关闭"，
左侧场景画面随光照等级与路灯状态实时变化。
路灯状态卡片的副标题会提示当前是"自动控制"还是"手动控制"
（手动 = 按了节点上的 KEY1，此时路灯状态不再跟随光照变化）。

演示模式：
    没有串口、没有开发板也能演示。演示线程按剧本生成与固件逐字一致的
    协调器上报行（前缀 [GW]、字段名、单位都相同），走同一个 handle_line()，
    所以界面代码路径与接真实硬件时完全一样。剧本覆盖：黄昏渐暗 -> 入夜自动
    开灯 -> 深夜 -> KEY1 手动关灯 / 开灯 -> 摇杆恢复自动 -> 破晓 -> 白天关灯。

安装依赖：  pip install pyserial        （仅真实串口需要，演示模式不用装）
运行方式：  python light_monitor.py              正常连接串口
            python light_monitor.py --demo       直接以演示模式启动
            python light_monitor.py COM3         指定串口自动连接
================================================================================
"""

from __future__ import annotations

import queue
import math
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
except Exception:                                    # pragma: no cover
    HAS_PYSERIAL = False


# ==============================================================================
# 一、外观常量
# ==============================================================================
APP_TITLE = "路灯智能监控系统"

BG      = "#0B1220"        # 窗口底色
CARD    = "#141C2E"        # 卡片底色
CARD_HI = "#18223A"        # 卡片高亮
BORDER  = "#223050"        # 描边
TXT     = "#E8EEF9"        # 主文字
MUTED   = "#8493AD"        # 次要文字
FAINT   = "#5B6884"        # 更淡

SUN     = "#FFC53D"        # 光亮充足
MOON    = "#7C93FF"        # 光亮不足
LAMP_ON = "#FFB020"        # 路灯打开
OFF     = "#64708A"        # 路灯关闭
GREEN   = "#3DD68C"        # 已连接 / 网关就绪
RED     = "#F05252"

FONT_UI = "Microsoft YaHei UI"

# 光照阈值，与 NewApp.h 的 NEWAPP_LUX_NIGHT / NEWAPP_LUX_DUSK 一致
LUX_MAX   = 2000
LUX_NIGHT = 80
LUX_DUSK  = 250


# ==============================================================================
# 二、颜色工具
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


# 各光照等级的天空渐变（上 -> 下）
SKY = {
    "DAY":   ("#4E9FD8", "#C7E6F7"),
    "DUSK":  ("#26325E", "#E4834A"),
    "NIGHT": ("#050A18", "#152443"),
}


# ==============================================================================
# 三、串口数据解析
# ==============================================================================
RE_LEVEL = re.compile(r"LEVEL=(NIGHT|DUSK|DAY)")
RE_LAMP  = re.compile(r"LAMP=(ON|OFF)")
RE_MODE  = re.compile(r"MODE=(AUTO|MANUAL_ON|MANUAL_OFF)")
RE_LUX   = re.compile(r"LUX=(\d+)")
RE_ADC   = re.compile(r"ADC=(\d+)")
RE_LID   = re.compile(r"\bL(\d+)\b")
RE_ADDR  = re.compile(r"addr=0x([0-9A-Fa-f]+)")
RE_CNT   = re.compile(r"cnt=(\d+)")


def parse_line(line: str) -> dict:
    """解析一行光敏上报；没有 LEVEL / LAMP 字段时返回空字典。"""
    m_level = RE_LEVEL.search(line)
    m_lamp = RE_LAMP.search(line)
    if not m_level and not m_lamp:
        return {}

    info = {}
    if m_level:
        info["level"] = m_level.group(1)
    if m_lamp:
        info["lamp"] = (m_lamp.group(1) == "ON")

    m = RE_LUX.search(line)
    if m:
        info["lux"] = int(m.group(1))
    m = RE_ADC.search(line)
    if m:
        info["adc"] = int(m.group(1))
    m = RE_MODE.search(line)
    if m:
        info["mode"] = m.group(1)
    m = RE_LID.search(line)
    if m:
        info["lid"] = m.group(1)
    m = RE_ADDR.search(line)
    if m:
        info["addr"] = m.group(1).upper()
    m = RE_CNT.search(line)
    if m:
        info["cnt"] = int(m.group(1))
    return info


def level_of(lux: int) -> str:
    """与固件 NewApp_LightLevel() 完全一致的分级。"""
    if lux < LUX_NIGHT:
        return "NIGHT"
    if lux < LUX_DUSK:
        return "DUSK"
    return "DAY"


def lamp_of(mode: str, level: str) -> bool:
    """与固件一致的开关灯决策：手动听命令，自动只看是否入夜。"""
    if mode == "MANUAL_ON":
        return True
    if mode == "MANUAL_OFF":
        return False
    return level == "NIGHT"


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
            self.out_q.put(("error", f"打开串口 {self.port} 失败：{exc}"))
            return

        self.out_q.put(("info", "connected"))

        buf = b""
        while not self.stop_evt.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                if not self.stop_evt.is_set():
                    self.out_q.put(("error", f"串口读取异常：{exc}"))
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
# 五、演示数据源（不占串口）
# ==============================================================================
# 一轮剧本：时长(秒) / 起始 lx / 结束 lx / 工作模式 / 场景名
# 数值按固件阈值编排：250 lx 以上白天、80~250 黄昏、80 以下入夜自动开灯。
DEMO_BEATS = [
    (7.0,  900,  268, "AUTO",       "夕阳西下，光照渐弱"),
    (6.0,  268,   92, "AUTO",       "黄昏，跌破 250 lx"),
    (5.0,   92,   46, "AUTO",       "入夜，自动点亮路灯"),
    (7.0,   46,   21, "AUTO",       "深夜，路灯常亮"),
    (6.0,   21,   19, "MANUAL_OFF", "按下 KEY1，手动关灯"),
    (6.0,   19,   18, "MANUAL_ON",  "再按 KEY1，手动开灯"),
    (5.0,   18,   17, "AUTO",       "摇杆中键，恢复自动"),
    (8.0,   17,  262, "AUTO",       "破晓，光照回升"),
    (8.0,  262, 1520, "AUTO",       "白天，自动关灯"),
    (6.0, 1520,  880, "AUTO",       "午后，光照充足"),
]
DEMO_TOTAL = sum(b[0] for b in DEMO_BEATS)


class DemoWorker(threading.Thread):
    """演示模式数据源：不占用串口，按剧本生成协调器上报文本。

    产出的字符串与真实固件逐字一致（前缀 `[GW]`、字段名、单位都相同），
    统一塞进同一个队列，所以主线程完全不区分"数据来自串口还是演示"。
    """

    TICK = 1.4            # 每轮上报间隔（秒）

    def __init__(self, out_q: queue.Queue, stop_evt: threading.Event, offset: float = 0.0):
        super().__init__(daemon=True)
        self.out_q = out_q
        self.stop_evt = stop_evt
        self.t0 = time.time() - max(0.0, offset)
        self.cnt = 0

    # ------------------------------------------------------------ 剧本取样
    def _beat_at(self, elapsed: float):
        ph = elapsed % DEMO_TOTAL
        acc = 0.0
        for dur, lux0, lux1, mode, name in DEMO_BEATS:
            if ph < acc + dur:
                return lux0, lux1, mode, name, (ph - acc) / dur
            acc += dur
        lux0, lux1, mode, name = DEMO_BEATS[-1][1:5]
        return lux0, lux1, mode, name, 1.0

    # ------------------------------------------------------------ 线程主体
    def run(self) -> None:
        # ---- 上电启动过程，与协调器真实串口输出逐字一致
        for delay, text in ((0.5, "[APP] ZigBee_NewApp gateway started"),
                            (1.4, "[APP] network formed, gateway ready")):
            if self.stop_evt.wait(delay):
                return
            self.out_q.put(("line", text))

        while not self.stop_evt.is_set():
            lux0, lux1, mode, name, u = self._beat_at(time.time() - self.t0)

            lux = lux0 + (lux1 - lux0) * u
            lux += random.uniform(-2.0, 2.0)                     # 加一点抖动
            lux = max(1.0, min(float(LUX_MAX), lux))
            lux_i = int(round(lux))
            adc = max(0, min(255, int(round(lux_i * 255.0 / LUX_MAX))))
            level = level_of(lux_i)
            lamp = lamp_of(mode, level)

            self.cnt += 1
            self.out_q.put(("phase", name))
            self.out_q.put(("line",
                            "[GW] L1 addr=0x796F cnt=%d LUX=%d ADC=%d "
                            "LEVEL=%s LAMP=%s MODE=%s"
                            % (self.cnt, lux_i, adc, level,
                               "ON" if lamp else "OFF", mode)))

            if self.stop_evt.wait(self.TICK):
                return


# ==============================================================================
# 六、左侧场景画面
# ==============================================================================
class SceneCanvas(tk.Canvas):
    """天空 + 日月 + 马路 + 路灯，随光照等级与路灯开关变化。"""

    def __init__(self, master, **kw):
        super().__init__(master, highlightthickness=0, bg=SKY["NIGHT"][1], **kw)
        self._level = None
        self._lamp = None
        random.seed(20261021)
        self._stars = [(random.random(), random.uniform(0.05, 0.62),
                        random.choice((1, 1, 2))) for _ in range(70)]
        self.bind("<Configure>", lambda e: self.redraw())

    def set_scene(self, level, lamp) -> None:
        changed = (level != self._level) or (lamp != self._lamp)
        self._level = level
        self._lamp = lamp
        if changed:
            self.redraw()

    # ------------------------------------------------------------------ 绘制
    def redraw(self) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 40 or h < 40:
            return

        self.delete("all")
        level = self._level or "NIGHT"
        lamp_on = bool(self._lamp)
        sky_h = int(h * 0.74)

        top, bottom = SKY[level]
        self._gradient(sky_h, top, bottom)

        if level in ("NIGHT", "DUSK"):
            self._draw_stars(w, sky_h, level)

        self._draw_celestial(w, sky_h, level)

        # 马路
        self.create_rectangle(0, sky_h, w, h, fill="#23262E", outline="")
        self.create_rectangle(0, sky_h, w, sky_h + 2, fill="#333A48", outline="")
        self._draw_road(w, h, sky_h)

        head_x = int(w * 0.63)
        head_y = int(sky_h * 0.22)
        lamp_cx = head_x
        lamp_cy = head_y + 16

        if lamp_on:
            self._draw_glow(lamp_cx, lamp_cy, level)
            self._draw_pool(lamp_cx, sky_h, w, h)

        # 灯柱
        pole_w = max(5, int(w * 0.007))
        self.create_rectangle(head_x - pole_w // 2, head_y + 4,
                              head_x + pole_w // 2, sky_h + 6,
                              fill="#3A4152", outline="")
        self.create_rectangle(head_x - pole_w * 2, sky_h - 2,
                              head_x + pole_w * 2, sky_h + 6,
                              fill="#2E3543", outline="")

        # 灯头
        self.create_rectangle(head_x - 22, head_y - 4, head_x + 22, head_y + 5,
                              fill="#3A4152", outline="")
        shade = [head_x - 17, head_y + 5, head_x + 17, head_y + 5,
                 head_x + 12, head_y + 27, head_x - 12, head_y + 27]
        if lamp_on:
            self.create_polygon(shade, fill=SUN,
                                outline=blend(SUN, "#FFFFFF", 0.35))
        else:
            self.create_polygon(shade, fill="#2A3346", outline="#3A4152")

    def _gradient(self, height, top, bottom):
        bands = 52
        w = self.winfo_width()
        for i in range(bands):
            y0 = int(height * i / bands)
            y1 = int(height * (i + 1) / bands) + 1
            self.create_rectangle(0, y0, w, y1,
                                  fill=blend(top, bottom, i / (bands - 1)),
                                  outline="")

    def _draw_stars(self, w, sky_h, level):
        dim = 0.55 if level == "DUSK" else 1.0
        for rx, ry, size in self._stars:
            x = rx * w
            y = ry * sky_h
            c = blend(SKY[level][0], "#FFFFFF", 0.75 * dim)
            self.create_oval(x, y, x + size, y + size, fill=c, outline="")

    def _draw_celestial(self, w, sky_h, level):
        cx = int(w * 0.24)

        def halo(cy, r0, r1, base, warm, steps=8):
            for i in range(steps):
                f = i / (steps - 1)
                r = int(r0 - (r0 - r1) * f)
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=blend(base, warm, 0.05 + 0.45 * f), outline="")

        if level == "DAY":
            cy = int(sky_h * 0.30)
            halo(cy, 60, 27, blend(SKY["DAY"][0], SKY["DAY"][1], 0.30), "#FFE9A8")
            self.create_oval(cx - 26, cy - 26, cx + 26, cy + 26, fill="#FFD863", outline="")
        elif level == "DUSK":
            cy = int(sky_h * 0.90)
            halo(cy, 58, 27, blend(SKY["DUSK"][0], SKY["DUSK"][1], 0.90), "#FFC98A")
            self.create_oval(cx - 26, cy - 26, cx + 26, cy + 26, fill="#FF8A3D", outline="")
        else:
            cy = int(sky_h * 0.28)
            self.create_oval(cx - 34, cy - 34, cx + 34, cy + 34,
                             fill="#1B2A4A", outline="")
            self.create_oval(cx - 26, cy - 26, cx + 26, cy + 26, fill="#D8E2F5", outline="")
            self.create_oval(cx - 6, cy - 12, cx + 10, cy + 4,
                             fill="#C3CFE8", outline="")
            self.create_oval(cx - 14, cy + 6, cx - 2, cy + 16,
                             fill="#C3CFE8", outline="")

    def _draw_road(self, w, h, sky_h):
        y = sky_h + int((h - sky_h) * 0.58)
        dash, gap = 46, 34
        x = -10
        while x < w:
            self.create_rectangle(x, y, x + dash, y + 4, fill="#414A5C", outline="")
            x += dash + gap

    def _draw_glow(self, cx, cy, level):
        """灯头周围的柔和光晕：多层同心圆模拟渐变（Canvas 无 alpha）。"""
        base = blend(SKY[level][0], SKY[level][1], 0.30)
        steps = 14
        for i in range(steps):
            f = i / (steps - 1)
            r = int(132 - 108 * f)
            t = 0.015 + 0.42 * (f ** 2)
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=blend(base, LAMP_ON, t), outline="")

    def _draw_pool(self, cx, sky_h, w, h):
        """路面上的一小片暖色光斑。"""
        pool_w = w * 0.17
        pool_y = sky_h + int((h - sky_h) * 0.58)
        for i in range(5):
            f = i / 4
            ww = pool_w * (1.0 - 0.13 * i)
            hh = 13 - 2 * i
            self.create_oval(cx - ww, pool_y - hh, cx + ww, pool_y + hh + 4,
                             fill=blend("#23262E", LAMP_ON, 0.10 + 0.28 * f), outline="")


# ==============================================================================
# 七、状态图标
# ==============================================================================
def draw_sun_icon(cv: tk.Canvas, color: str) -> None:
    cv.delete("all")
    cx, cy, r = 24, 24, 9
    for i in range(8):
        a = math.radians(i * 45)
        x1, y1 = cx + math.cos(a) * 14, cy + math.sin(a) * 14
        x2, y2 = cx + math.cos(a) * 19, cy + math.sin(a) * 19
        cv.create_line(x1, y1, x2, y2, fill=color, width=3, capstyle="round")
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")


def draw_moon_icon(cv: tk.Canvas, color: str, bg: str) -> None:
    cv.delete("all")
    cx, cy, r = 24, 24, 12
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
    cv.create_oval(cx - r + 9, cy - r - 4, cx + r + 9, cy + r - 4, fill=bg, outline="")


def draw_bulb_icon(cv: tk.Canvas, on: bool) -> None:
    cv.delete("all")
    cx, cy = 24, 20
    if on:
        for r, t in ((20, 0.18), (16, 0.32), (13, 0.55)):
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=blend(CARD, LAMP_ON, t), outline="")
        cv.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, fill=LAMP_ON, outline="")
        cv.create_rectangle(cx - 6, cy + 10, cx + 6, cy + 18, fill="#C98A18", outline="")
        cv.create_rectangle(cx - 6, cy + 19, cx + 6, cy + 23, fill="#8A6416", outline="")
    else:
        cv.create_oval(cx - 10, cy - 10, cx + 10, cy + 10,
                       fill="", outline=OFF, width=2)
        cv.create_rectangle(cx - 6, cy + 10, cx + 6, cy + 23, fill="", outline=OFF, width=2)


# ==============================================================================
# 八、主界面
# ==============================================================================
class LightApp:

    def __init__(self, root: tk.Tk, auto_port: str = "", demo: bool = False,
                 demo_offset: float = 0.0):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker = None
        self.demo_worker = None
        self.stop_evt = None
        self._closing = False
        self._after_id = None
        self._demo_offset = demo_offset
        self._phase = ""

        self.state = {
            "level": None, "lamp": None, "mode": None, "lux": None,
            "adc": None, "lid": None, "addr": None, "cnt": 0,
        }
        self.gateway_ready = False

        self._init_fonts()
        self._init_window()
        self._init_style()
        self._build_ui()

        self.refresh_ports()
        if demo:
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
        w, h = 1080, 660
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.root.minsize(880, 580)

    def _init_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground="#1B2438", background="#1B2438",
                        foreground=TXT, arrowcolor=MUTED,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        selectbackground="#1B2438", selectforeground=TXT,
                        insertcolor=TXT)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", "#1B2438"), ("disabled", "#1B2438")],
                  foreground=[("readonly", TXT), ("disabled", FAINT)],
                  background=[("readonly", "#1B2438"), ("active", "#243050")],
                  selectbackground=[("readonly", "#1B2438")],
                  selectforeground=[("readonly", TXT)],
                  arrowcolor=[("readonly", MUTED), ("active", TXT)])
        self.root.option_add("*TCombobox*Listbox.background", "#1B2438")
        self.root.option_add("*TCombobox*Listbox.foreground", TXT)

    # ---------------------------------------------------------------- 界面
    def _build_ui(self):
        self._build_header()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        # 左：场景
        left = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.scene = SceneCanvas(left)
        self.scene.pack(fill="both", expand=True, padx=3, pady=3)

        # 右：两块状态
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.card_light, self.icon_light, self.txt_light, self.sub_light = \
            self._make_card(right, "光照状况", MOON)
        self.card_lamp, self.icon_lamp, self.txt_lamp, self.sub_lamp = \
            self._make_card(right, "路灯状态", OFF)
        self.card_light.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        self.card_lamp.grid(row=1, column=0, sticky="nsew")

        self._build_footer()
        self._render()

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 8))

        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, font=(FONT_UI, 17, "bold"),
                 bg=BG, fg=TXT).pack(anchor="w")
        tk.Label(left, text="ZigBee 光敏路灯  ·  光照自适应开关灯",
                 font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        ctrl = tk.Frame(head, bg=BG)
        ctrl.pack(side="right")

        tk.Label(ctrl, text="串口", font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(ctrl, textvariable=self.port_var, width=9,
                                    state="readonly", values=[], style="Dark.TCombobox")
        self.port_cb.pack(side="left", padx=(5, 6))

        self._button(ctrl, "刷新", self.refresh_ports, "#1B2438", MUTED, 5).pack(side="left", padx=(0, 10))

        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(ctrl, textvariable=self.baud_var, width=7, state="readonly",
                     values=["9600", "38400", "115200"],
                     style="Dark.TCombobox").pack(side="left", padx=(0, 10))

        self.btn_conn = self._button(ctrl, "连接", self.toggle_conn, LAMP_ON, "#1A1206", 6)
        self.btn_conn.pack(side="left", padx=(0, 8))

        self.btn_demo = self._button(ctrl, "演示模式", self.toggle_demo, "#1B2438", MUTED, 7)
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

    def _make_card(self, parent, title, accent):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(top, text=title, font=(FONT_UI, 10), bg=CARD, fg=MUTED).pack(side="left")
        bar = tk.Canvas(top, width=26, height=3, bg=CARD, highlightthickness=0)
        bar.create_rectangle(0, 0, 26, 3, fill=accent, outline="")
        bar.pack(side="right", pady=6)

        body = tk.Frame(card, bg=CARD)
        body.pack(expand=True, padx=18, pady=(0, 10))

        icon = tk.Canvas(body, width=48, height=48, bg=CARD, highlightthickness=0)
        icon.pack(side="left", padx=(0, 14), anchor="n", pady=(6, 0))

        col = tk.Frame(body, bg=CARD)
        col.pack(side="left", anchor="n")
        main = tk.Label(col, text="等待数据", font=(FONT_UI, 22, "bold"),
                        bg=CARD, fg=MUTED, anchor="w")
        main.pack(anchor="w")
        sub = tk.Label(col, text="—", font=(FONT_UI, 9), bg=CARD, fg=FAINT, anchor="w")
        sub.pack(anchor="w", pady=(3, 0))
        return card, icon, main, sub

    def _build_footer(self):
        bar = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x", padx=18, pady=(10, 14))
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=16, pady=9)

        self.info_var = tk.StringVar(value="等待光敏终端上报…")
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

        if self.demo_worker is not None:              # 接真实串口时自动退出演示
            self.stop_demo()

        self._reset_state()
        self.stop_evt = threading.Event()
        self.worker = SerialWorker(port, baud, self.q, self.stop_evt)
        self.worker.start()

        self.info_var.set("正在等待 %s 的光敏上报…" % port)
        self.conn_var.set(f"{port} 已连接")
        self.dot.itemconfig(self.dot_id, fill=GREEN)
        self.btn_conn.configure(text="断开", bg="#2A1A1E", fg=RED)

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
        self.btn_conn.configure(text="连接", bg=LAMP_ON, fg="#1A1206")

    # ---------------------------------------------------------------- 演示
    def toggle_demo(self):
        if self.demo_worker is not None:
            self.stop_demo()
        else:
            self.start_demo()

    def start_demo(self):
        """进入演示模式：不需要串口，也不需要开发板。"""
        if self.worker is not None:
            self.disconnect()

        self._reset_state()
        self.stop_evt = threading.Event()
        self.demo_worker = DemoWorker(self.q, self.stop_evt, self._demo_offset)
        self.demo_worker.start()

        self.conn_var.set("演示模式 模拟运行")
        self.dot.itemconfig(self.dot_id, fill=LAMP_ON)
        self.btn_demo.configure(text="退出演示", bg="#2A2110", fg=LAMP_ON)
        self.info_var.set("演示模式启动中 · 模拟协调器串口输出…")

    def stop_demo(self):
        if self.stop_evt is not None:
            self.stop_evt.set()
        if self.demo_worker is not None:
            try:
                self.demo_worker.join(timeout=1.0)
            except Exception:
                pass
        self.demo_worker = None
        self.stop_evt = None
        self._phase = ""
        self.conn_var.set("未连接")
        self.dot.itemconfig(self.dot_id, fill=FAINT)
        self.btn_demo.configure(text="演示模式", bg="#1B2438", fg=MUTED)
        self._reset_state()
        self.info_var.set("等待光敏终端上报…")
        self._render()

    def _reset_state(self):
        self.state = {
            "level": None, "lamp": None, "mode": None, "lux": None,
            "adc": None, "lid": None, "addr": None, "cnt": 0,
        }
        self.gateway_ready = False
        self.gw_var.set("网关 未就绪")
        self.gw_label.configure(fg=FAINT)
        self._phase = ""

    # ---------------------------------------------------------------- 队列
    def _pump(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self.handle_line(payload)
                elif kind == "phase":
                    self._phase = payload
                elif kind == "error":
                    messagebox.showerror("串口错误", payload)
                    self.disconnect()
        except queue.Empty:
            pass
        self._after_id = self.root.after(60, self._pump)

    def handle_line(self, line: str):
        if "network formed" in line:
            self.gateway_ready = True
            self.gw_var.set("网关 已就绪")
            self.gw_label.configure(fg=GREEN)

        info = parse_line(line)
        if not info:
            return

        for key in ("level", "lamp", "mode", "lux", "adc", "lid", "addr", "cnt"):
            if key in info:
                self.state[key] = info[key]
        self._render()

    # ---------------------------------------------------------------- 渲染
    def _render(self):
        st = self.state
        level = st["level"]
        lamp = st["lamp"]
        lux = st["lux"]

        # 光照卡片
        if level == "DAY":
            self.txt_light.configure(text="光亮充足", fg=SUN)
            sub = "光照 %s lx · 白天" % lux if lux is not None else "白天"
            draw_sun_icon(self.icon_light, SUN)
            self.card_light.configure(highlightbackground=blend(CARD, SUN, 0.45))
        elif level in ("NIGHT", "DUSK"):
            self.txt_light.configure(text="光亮不足", fg=MOON)
            if lux is not None:
                sub = "光照 %s lx · %s" % (lux, "夜晚" if level == "NIGHT" else "傍晚微亮")
            else:
                sub = "夜晚" if level == "NIGHT" else "傍晚微亮"
            draw_moon_icon(self.icon_light, MOON, CARD)
            self.card_light.configure(highlightbackground=blend(CARD, MOON, 0.45))
        else:
            self.txt_light.configure(text="等待数据", fg=MUTED)
            sub = "尚未收到光敏上报"
            draw_moon_icon(self.icon_light, FAINT, CARD)
        self.sub_light.configure(text=sub)

        # 路灯卡片
        if lamp is True:
            self.txt_lamp.configure(text="路灯打开", fg=LAMP_ON)
            self.card_lamp.configure(highlightbackground=blend(CARD, LAMP_ON, 0.55))
        elif lamp is False:
            self.txt_lamp.configure(text="路灯关闭", fg=OFF)
            self.card_lamp.configure(highlightbackground=BORDER)
        else:
            self.txt_lamp.configure(text="等待数据", fg=MUTED)
        draw_bulb_icon(self.icon_lamp, lamp is True)

        # 副标题：区分"手动控制"（按了节点 KEY1，路灯不再跟随光照）与"自动控制"
        if st["mode"] is None and st["lid"] is None:
            self.sub_lamp.configure(text="—", fg=FAINT)
        else:
            manual = st["mode"] in ("MANUAL_ON", "MANUAL_OFF")
            tail = "手动控制" if manual else "自动控制"
            head = "L%s 光敏路灯 · " % st["lid"] if st["lid"] else ""
            self.sub_lamp.configure(text=head + tail,
                                    fg=LAMP_ON if manual else FAINT)

        # 场景
        self.scene.set_scene(level, lamp)

        # 底部信息（演示模式下带上当前剧本场景名）
        prefix = ""
        if self.demo_worker is not None:
            prefix = ("演示 · %s    ·    " % self._phase) if self._phase \
                else "演示启动中    ·    "

        if st["level"] is not None:
            parts = []
            if st["lid"]:
                parts.append("路号 L" + st["lid"])
            if st["addr"]:
                parts.append("地址 0x" + st["addr"])
            parts.append("上报 %d 次" % st["cnt"])
            if st["lux"] is not None:
                parts.append("光照 %d lx" % st["lux"])
            if st["adc"] is not None:
                parts.append("ADC %d" % st["adc"])
            parts.append("更新 " + datetime.now().strftime("%H:%M:%S"))
            self.info_var.set(prefix + "    ·    ".join(parts))

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
            for th in (self.worker, self.demo_worker):
                if th is not None:
                    th.join(timeout=0.8)
        except Exception:
            pass
        self.root.destroy()


# ==============================================================================
# 九、入口
# ==============================================================================
def main() -> int:
    args = [a for a in sys.argv[1:]]
    demo = "--demo" in args
    offset = 0.0
    if "--offset" in args:
        try:
            offset = float(args[args.index("--offset") + 1])
        except Exception:
            offset = 0.0
    auto_port = ""
    for a in args:
        if a.startswith("--"):
            continue
        auto_port = a
        break

    root = tk.Tk()
    LightApp(root, auto_port=auto_port, demo=demo, demo_offset=offset)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
