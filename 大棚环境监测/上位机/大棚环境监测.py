#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大棚环境监测系统  —— ZigBee 温室大棚串口上位机
================================================================================
配套下位机工程：ZigBee_NewApp（CC2530 + Z-Stack 2.5.1a，大棚环境监测版）
                 1 个协调器网关 + 温湿度终端 + 光敏终端

识别的串口行（协调器 UART0，9600-8-N-1）：

    [GW] T1 addr=0xAF10 cnt=3 T=25.6C H=60.5%RH
    [GW] T2 addr=0x4C24 cnt=4 LUX=1260 ADC=160 LEVEL=DAY

    温湿度帧：T1/T2  节点编号   addr 短地址   cnt 上报次数
              T 空气温度（℃）   H 空气湿度（%RH）

    光照帧：  LUX 光照强度（lx）  ADC 光敏原始采样
              LEVEL 光照等级 NIGHT 不足 / DUSK 偏弱 / DAY 充足

界面左侧是随环境实时变化的大棚场景，右侧是温度 / 湿度 / 光照三张状态卡片：

    温度  < 15℃ 偏低   15 ~ 30℃ 适宜   > 30℃ 偏高
    湿度  < 45%RH 偏干 45 ~ 85%RH 适宜  > 85%RH 偏湿
    光照  < 80 lx 不足 80 ~ 250 lx 偏弱  ≥ 250 lx 充足

大棚场景的对应关系：
    天空与亮度  ← 光照等级      叶片颜色 ← 温度（偏冷发青、适宜翠绿、偏热发黄）
    棚膜水雾    ← 湿度（越潮水滴越多）

演示模式（无硬件 / 无串口时用）：
    点右上角「演示」按钮，或运行时加 --demo 参数：

        python 大棚环境监测.py --demo

    演示数据由 DemoWorker 按脚本化的环境曲线生成，输出的文本行与协调器真实
    串口输出**逐字一致**（`[APP] ZigBee_NewApp gateway started`、
    `[GW] T1 addr=0xAF10 cnt=n T=..C H=..%RH` 等），因此界面走的是与真实接线
    时完全相同的解析 / 渲染路径，不是另一套假界面。
    一轮演示约 45 秒，依次覆盖：凌晨低温高湿光照不足 → 日出转暖偏弱 →
    上午适宜充足 → 正午高温偏干强光 → 傍晚降温回暖，即温度 / 湿度 / 光照
    三张卡片的全部状态都会出现。

安装依赖：  pip install pyserial      （仅真实串口需要，演示模式无需安装）
运行方式：  python 大棚环境监测.py
            python 大棚环境监测.py COM3     直接指定串口
            python 大棚环境监测.py --demo   启动即进入演示模式
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
except Exception:                                    # pragma: no cover
    HAS_PYSERIAL = False


# ==============================================================================
# 一、外观常量（深墨绿主题，呼应大棚 / 植物）
# ==============================================================================
APP_TITLE = "大棚环境监测系统"

BG      = "#08120E"        # 窗口底色
CARD    = "#101E18"        # 卡片底色
BORDER  = "#1D3A2C"        # 描边
TXT     = "#E7F3EC"        # 主文字
MUTED   = "#7C9C8C"        # 次要文字
FAINT   = "#54705F"        # 更淡

GREEN   = "#3DD68C"        # 适宜
AMBER   = "#FFB020"        # 偏高 / 偏弱
RED     = "#F05252"        # 报警
CYAN    = "#4FC3F7"        # 湿度
SUN     = "#FFC53D"        # 光照充足
MOON    = "#8FA6FF"        # 光照不足

FONT_UI = "Microsoft YaHei UI"

# 各光照等级的天空渐变（上 -> 下）
SKY = {
    "DAY":   ("#3E8FC4", "#BFE4F2"),
    "DUSK":  ("#22304F", "#D97C46"),
    "NIGHT": ("#040A12", "#12202F"),
}

# 大棚阈值
TEMP_LOW, TEMP_HIGH = 15.0, 30.0      # 适宜区间（℃）
HUM_LOW,  HUM_HIGH  = 45.0, 85.0      # 适宜区间（%RH）
LUX_LOW,  LUX_OK    = 80, 250         # 不足 / 偏弱 / 充足 分界（lx）


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


# ==============================================================================
# 三、串口数据解析
# ==============================================================================
RE_TEMP  = re.compile(r"T=(-?\d+(?:\.\d+)?)C")
RE_HUM   = re.compile(r"H=(\d+(?:\.\d+)?)%RH")
RE_LUX   = re.compile(r"LUX=(\d+)")
RE_LEVEL = re.compile(r"LEVEL=(NIGHT|DUSK|DAY)")
RE_ADC   = re.compile(r"ADC=(\d+)")
RE_TID   = re.compile(r"\bT(\d+)\b")
RE_ADDR  = re.compile(r"addr=0x([0-9A-Fa-f]+)")
RE_CNT   = re.compile(r"cnt=(\d+)")


def parse_line(line: str) -> dict:
    """解析一行上报；既没有温湿度也没有光照字段时返回空字典。"""
    m_temp = RE_TEMP.search(line)
    m_hum = RE_HUM.search(line)
    m_lux = RE_LUX.search(line)
    if not (m_temp or m_hum or m_lux):
        return {}

    info = {}
    if m_temp:
        info["temp"] = float(m_temp.group(1))
    if m_hum:
        info["hum"] = float(m_hum.group(1))
    if m_lux:
        info["lux"] = int(m_lux.group(1))

    m = RE_LEVEL.search(line)
    if m:
        info["level"] = m.group(1)
    m = RE_ADC.search(line)
    if m:
        info["adc"] = int(m.group(1))
    m = RE_TID.search(line)
    if m:
        info["tid"] = m.group(1)
    m = RE_ADDR.search(line)
    if m:
        info["addr"] = m.group(1).upper()
    m = RE_CNT.search(line)
    if m:
        info["cnt"] = int(m.group(1))
    return info


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
    # 名称,  虚拟时刻(h), 温度℃, 湿度%RH, 光照lx
    ("凌晨",  5.0, 13.8, 89.0,    8),      # 低温 + 高湿 + 光照不足
    ("日出",  6.6, 17.5, 82.0,  170),      # 转暖 + 光照偏弱
    ("上午",  9.5, 24.2, 63.0,  920),      # 温湿度适宜 + 光照充足
    ("正午", 13.0, 31.4, 41.0, 1780),      # 高温 + 偏干 + 强光
    ("傍晚", 18.6, 23.0, 71.0,  300),      # 回落适宜
]


def level_of(lux: int) -> str:
    """按与下位机一致的分档，把光照值换算成 NIGHT / DUSK / DAY。"""
    if lux < LUX_LOW:
        return "NIGHT"
    if lux < LUX_OK:
        return "DUSK"
    return "DAY"


class DemoWorker(threading.Thread):
    """演示模式数据源：不占用串口，按脚本生成协调器上报文本行。

    产出的字符串与真实固件逐字一致（前缀 `[GW]`、字段名、单位都相同），
    统一塞进同一个队列，所以主线程完全不区分"数据来自串口还是演示"。
    """

    TICK = 1.5            # 每轮上报间隔（秒）
    HOLD = 9.0            # 每个场景保持时间（秒）

    def __init__(self, out_q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self.out_q = out_q
        self.stop_evt = stop_evt

    # ------------------------------------------------------------ 环境模型
    def _sample(self, elapsed: float):
        """按 elapsed 在场景表里插值，返回 温度 / 湿度 / 光照 / 虚拟时刻。"""
        n = len(DEMO_SCENES)
        ph = elapsed % (self.HOLD * n)
        i = int(ph // self.HOLD)
        u = (ph - i * self.HOLD) / self.HOLD
        cur = DEMO_SCENES[i]
        nxt = DEMO_SCENES[(i + 1) % n]

        # 前 70% 时间保持当前场景，最后 30% 平滑过渡到下一个场景
        w = 0.0 if u < 0.70 else (u - 0.70) / 0.30
        w = w * w * (3.0 - 2.0 * w)                       # smoothstep

        hour_a = cur[1]
        hour_b = nxt[1] + (24.0 if nxt[1] < hour_a else 0.0)
        temp = cur[2] + (nxt[2] - cur[2]) * w
        hum = cur[3] + (nxt[3] - cur[3]) * w
        lux = cur[4] + (nxt[4] - cur[4]) * w
        hour = (hour_a + (hour_b - hour_a) * w) % 24.0
        return temp, hum, lux, hour

    # ------------------------------------------------------------ 线程主体
    def run(self) -> None:
        # ---- 上电启动过程，与协调器真实串口输出逐字一致
        for delay, text in ((0.6, "[APP] ZigBee_NewApp gateway started"),
                            (1.6, "[APP] network formed, gateway ready")):
            if self.stop_evt.wait(delay):
                return
            self.out_q.put(("line", text))

        t0 = time.time()
        cnt_th = 0
        cnt_lt = 0

        while not self.stop_evt.is_set():
            elapsed = time.time() - t0
            temp, hum, lux, hour = self._sample(elapsed)

            # 叠加微小抖动，避免曲线完全平滑、看着像假数据
            temp += random.uniform(-0.12, 0.12)
            hum = max(1.0, min(99.0, hum + random.uniform(-0.35, 0.35)))
            lux = max(1.0, lux * random.uniform(0.985, 1.015))

            lux_i = int(round(lux))
            adc = max(0, min(255, int(round(lux / 1800.0 * 255.0))))
            level = level_of(lux_i)

            cnt_th += 1
            self.out_q.put(("line",
                            "[GW] T1 addr=0xAF10 cnt=%d T=%.1fC H=%.1f%%RH"
                            % (cnt_th, temp, hum)))
            self.out_q.put(("demo_clock",
                            "%02d:%02d" % (int(hour), int((hour % 1.0) * 60))))

            # 两个终端的上报错开半个周期，比同时到达更像真实的并发上报
            if self.stop_evt.wait(0.55):
                return

            cnt_lt += 1
            self.out_q.put(("line",
                            "[GW] T2 addr=0x4C24 cnt=%d LUX=%d ADC=%d LEVEL=%s"
                            % (cnt_lt, lux_i, adc, level)))

            if self.stop_evt.wait(self.TICK - 0.55):
                return


# ==============================================================================
# 六、左侧大棚场景
# ==============================================================================
class GreenhouseScene(tk.Canvas):
    """拱形大棚剖面：天空随光照变化，叶片颜色随温度变化，棚膜水雾随湿度变化。"""

    def __init__(self, master, **kw):
        super().__init__(master, highlightthickness=0, bg=SKY["NIGHT"][1], **kw)
        self._level = None
        self._temp = None
        self._hum = None
        random.seed(20261022)
        self._stars = [(random.random(), random.uniform(0.04, 0.52),
                        random.choice((1, 1, 2))) for _ in range(64)]
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, level, temp, hum) -> None:
        key = (level, None if temp is None else round(temp, 1),
               None if hum is None else round(hum, 0))
        if key == (self._level, self._temp, self._hum):
            return
        self._level, self._temp, self._hum = key
        self.redraw()

    # ------------------------------------------------------------------ 绘制
    def redraw(self) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 60 or h < 60:
            return

        self.delete("all")
        level = self._level or "NIGHT"
        horizon = int(h * 0.70)

        self._draw_sky(w, horizon, level)
        if level in ("NIGHT", "DUSK"):
            self._draw_stars(w, horizon, level)
        self._draw_celestial(w, horizon, level)
        self._draw_ground(w, h, horizon, level)
        self._draw_greenhouse(w, h, horizon, level)

    # ---------------------------------------------------------------- 天空
    def _draw_sky(self, w, horizon, level):
        top, bottom = SKY[level]
        bands = 46
        for i in range(bands):
            y0 = int(horizon * i / bands)
            y1 = int(horizon * (i + 1) / bands) + 1
            self.create_rectangle(0, y0, w, y1,
                                  fill=blend(top, bottom, i / (bands - 1)),
                                  outline="")

    def _draw_stars(self, w, horizon, level):
        dim = 0.5 if level == "DUSK" else 1.0
        c = blend(SKY[level][0], "#FFFFFF", 0.8 * dim)
        for rx, ry, size in self._stars:
            x, y = rx * w, ry * horizon
            self.create_oval(x, y, x + size, y + size, fill=c, outline="")

    def _draw_celestial(self, w, horizon, level):
        cx = int(w * 0.20)

        def halo(cy, r0, r1, base, warm, steps=8):
            for i in range(steps):
                f = i / (steps - 1)
                r = int(r0 - (r0 - r1) * f)
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=blend(base, warm, 0.05 + 0.45 * f), outline="")

        if level == "DAY":
            cy = int(horizon * 0.26)
            halo(cy, 56, 25, blend(SKY["DAY"][0], SKY["DAY"][1], 0.30), "#FFEFB8")
            self.create_oval(cx - 24, cy - 24, cx + 24, cy + 24, fill="#FFD863", outline="")
        elif level == "DUSK":
            cy = int(horizon * 0.88)
            halo(cy, 54, 25, blend(SKY["DUSK"][0], SKY["DUSK"][1], 0.90), "#FFC98A")
            self.create_oval(cx - 24, cy - 24, cx + 24, cy + 24, fill="#FF8A3D", outline="")
        else:
            cy = int(horizon * 0.24)
            self.create_oval(cx - 31, cy - 31, cx + 31, cy + 31, fill="#16243A", outline="")
            self.create_oval(cx - 24, cy - 24, cx + 24, cy + 24, fill="#D8E2F5", outline="")
            self.create_oval(cx - 6, cy - 11, cx + 9, cy + 4, fill="#C3CFE8", outline="")
            self.create_oval(cx - 13, cy + 5, cx - 2, cy + 15, fill="#C3CFE8", outline="")

    # ---------------------------------------------------------------- 地面
    def _draw_ground(self, w, h, horizon, level):
        soil_top = blend("#2A2118", SKY[level][1], 0.12)
        soil_bot = blend("#150F0B", SKY[level][1], 0.05)
        bands = 14
        for i in range(bands):
            y0 = horizon + int((h - horizon) * i / bands)
            y1 = horizon + int((h - horizon) * (i + 1) / bands) + 1
            self.create_rectangle(0, y0, w, y1,
                                  fill=blend(soil_top, soil_bot, i / (bands - 1)),
                                  outline="")
        # 地平线一条淡淡的亮边，把天空和地面分开
        self.create_rectangle(0, horizon, w, horizon + 2,
                              fill=blend(SKY[level][1], "#FFFFFF", 0.22), outline="")

        # 几条淡淡的田垄，避免地面大片空黑
        furrow = blend(soil_top, "#FFFFFF", 0.07)
        for ratio in (0.24, 0.46, 0.72):
            y = horizon + int((h - horizon) * ratio)
            x = -w * 0.04
            while x < w:
                self.create_line(x, y, x + w * 0.07, y, fill=furrow, width=2)
                x += w * 0.12

    # ---------------------------------------------------------------- 大棚
    def _draw_greenhouse(self, w, h, horizon, level):
        x0 = int(w * 0.08)
        x1 = int(w * 0.92)
        top = int(h * 0.17)
        cx = (x0 + x1) // 2
        rx = max(20, (x1 - x0) // 2)
        ry = max(20, horizon - top)

        # 棚内亮度：光照越弱棚里越暗
        dim = {"DAY": 0.0, "DUSK": 0.30, "NIGHT": 0.55}[level]

        # --- 棚膜：上半椭圆（chord 填充，底边正好落在地平线上）
        film = blend(blend(SKY[level][0], SKY[level][1], 0.55), "#E6FFF1", 0.14)
        self.create_arc(x0, horizon - ry, x1, horizon + ry,
                        start=0, extent=180, style="chord",
                        fill=film, outline="")

        # --- 棚内土垄
        bed_h = int((h - horizon) * 0.16) + 4
        self.create_rectangle(x0 + 6, horizon - bed_h, x1 - 6, horizon + 2,
                              fill=blend("#3A2A1B", SKY[level][1], 0.10), outline="")

        # --- 作物：后排小、前排大
        leaf = self._leaf_color(dim)
        self._draw_row(cx, horizon - bed_h * 0.15, int(rx * 1.72), 9, h * 0.050, leaf)
        self._draw_row(cx, horizon + 1, int(rx * 1.94), 7, h * 0.072, leaf)

        # --- 骨架：沿拱面的竖杆 + 顶部弧
        frame = blend("#2F4A3C", "#D9F2E4", 0.18)
        posts = 7
        for i in range(posts):
            px = x0 + int((x1 - x0) * i / (posts - 1))
            t = (px - cx) / float(rx)
            s = max(0.0, 1.0 - t * t)
            py = horizon - int(ry * math.sqrt(s))
            if i == 0 or i == posts - 1:
                self.create_line(px, py, px, horizon + 6, fill=frame, width=3)
            else:
                self.create_line(px, py, px, horizon - bed_h, fill=frame, width=2)

        self.create_arc(x0, horizon - ry, x1, horizon + ry,
                        start=0, extent=180, style="arc", outline=frame, width=3)
        # 两道横向拉杆，让拱顶看起来更像棚架
        for k in (0.42, 0.74):
            self.create_arc(cx - rx, horizon - int(ry * k), cx + rx, horizon + int(ry * k),
                            start=0, extent=180, style="arc",
                            outline=blend(frame, film, 0.35), width=1)

        # --- 棚膜上的水雾 / 水滴：湿度越高越明显
        self._draw_mist(x0, x1, horizon, ry, level)

    def _leaf_color(self, dim):
        """叶片颜色：温度决定色相，光照决定亮度。"""
        t = self._temp
        if t is None:
            base = GREEN
        elif t < TEMP_LOW:
            base = blend(GREEN, CYAN, 0.55)          # 偏冷：发青
        elif t > TEMP_HIGH:
            base = blend(GREEN, AMBER, 0.60)         # 偏热：发黄
        else:
            base = GREEN
        return blend(base, "#06120C", dim)

    def _draw_row(self, cx, base_y, span, count, size, leaf):
        if count < 2:
            return
        step = span / float(count - 1)
        left = cx - span / 2.0
        for i in range(count):
            x = left + step * i
            self._draw_crop(x, base_y, size * (0.86 + 0.28 * random.random()), leaf)

    def _draw_crop(self, x, base_y, size, leaf):
        stem = blend(leaf, "#0C2416", 0.30)
        self.create_line(x, base_y, x, base_y - size, fill=stem,
                         width=max(2, int(size * 0.13)))
        self.create_oval(x - size * 0.58, base_y - size * 0.88,
                         x + size * 0.02, base_y - size * 0.44,
                         fill=leaf, outline="")
        self.create_oval(x - size * 0.02, base_y - size * 1.00,
                         x + size * 0.58, base_y - size * 0.56,
                         fill=blend(leaf, "#FFFFFF", 0.10), outline="")

    def _draw_mist(self, x0, x1, horizon, ry, level):
        """棚膜内侧的露珠：贴在弧面附近，湿度越高数量越多。"""
        hum = self._hum
        if hum is None or hum < HUM_LOW:
            return
        ratio = min(1.0, (hum - HUM_LOW) / 40.0)
        count = int(8 + 26 * ratio)
        drop = blend("#CFEFFA", SKY[level][1], 0.30)
        rnd = random.Random(7)
        cx = (x0 + x1) / 2.0
        half = (x1 - x0) / 2.0
        for _ in range(count):
            u = rnd.uniform(-0.96, 0.96)                 # 沿棚膜的横向位置
            s = max(0.0, 1.0 - u * u)
            k = rnd.uniform(0.72, 0.96)                  # 越接近 1 越贴着棚膜
            dx = cx + u * half * k
            dy = horizon - int(ry * math.sqrt(s) * k)
            r = rnd.uniform(1.3, 2.6)
            self.create_oval(dx - r, dy - r * 1.4, dx + r, dy + r * 1.4,
                             fill=drop, outline="")


# ==============================================================================
# 七、卡片图标
# ==============================================================================
def draw_thermo_icon(cv: tk.Canvas, color: str, level: float = 0.62) -> None:
    """温度计：底部球泡按液位填充。"""
    cv.delete("all")
    cx, top_y, bot_y = 22, 5, 30
    cv.create_oval(cx - 9, bot_y - 6, cx + 9, bot_y + 12, fill="", outline=color, width=2)
    cv.create_rectangle(cx - 4, top_y, cx + 4, bot_y + 4, fill="", outline=color, width=2)
    cv.create_oval(cx - 6, bot_y - 3, cx + 6, bot_y + 9, fill=color, outline="")
    y = int((bot_y + 2) - (bot_y + 2 - top_y - 4) * max(0.0, min(1.0, level)))
    cv.create_rectangle(cx - 2, y, cx + 2, bot_y + 4, fill=color, outline="")


def draw_drop_icon(cv: tk.Canvas, color: str) -> None:
    """水滴。"""
    cv.delete("all")
    cx, top, bot, r = 22, 6, 26, 11
    cv.create_oval(cx - r, bot - r, cx + r, bot + r, fill=color, outline="")
    cv.create_polygon(cx, top, cx - r * 0.92, bot - 3, cx + r * 0.92, bot - 3,
                      fill=color, outline="")
    cv.create_oval(cx - 5, bot - 4, cx - 1, bot + 2, fill=blend(color, "#FFFFFF", 0.55), outline="")


def draw_sun_icon(cv: tk.Canvas, color: str) -> None:
    cv.delete("all")
    cx, cy, r = 22, 22, 8
    for i in range(8):
        a = math.radians(i * 45)
        cv.create_line(cx + math.cos(a) * 13, cy + math.sin(a) * 13,
                       cx + math.cos(a) * 18, cy + math.sin(a) * 18,
                       fill=color, width=3, capstyle="round")
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")


def draw_cloud_icon(cv: tk.Canvas, color: str, bg: str) -> None:
    """云（光照偏弱 / 不足时用）。"""
    cv.delete("all")
    cv.create_oval(6, 16, 24, 30, fill="", outline=color, width=2)
    cv.create_oval(16, 10, 36, 30, fill="", outline=color, width=2)
    cv.create_oval(26, 19, 40, 30, fill="", outline=color, width=2)


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

        # 两类终端分别记录，靠帧内容区分，不依赖 T 编号先后
        self.th = {"id": None, "addr": None, "cnt": 0, "temp": None, "hum": None}
        self.lt = {"id": None, "addr": None, "cnt": 0, "lux": None,
                   "adc": None, "level": None}
        self.gateway_ready = False

        self._init_fonts()
        self._init_window()
        self._init_style()
        self._build_ui()

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
        w, h = 1160, 720
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        self.root.minsize(940, 620)

    def _init_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground="#13251C", background="#13251C",
                        foreground=TXT, arrowcolor=MUTED,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        selectbackground="#13251C", selectforeground=TXT,
                        insertcolor=TXT)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", "#13251C"), ("disabled", "#13251C")],
                  foreground=[("readonly", TXT), ("disabled", FAINT)],
                  background=[("readonly", "#13251C"), ("active", "#1D3A2C")],
                  selectbackground=[("readonly", "#13251C")],
                  selectforeground=[("readonly", TXT)],
                  arrowcolor=[("readonly", MUTED), ("active", TXT)])
        self.root.option_add("*TCombobox*Listbox.background", "#13251C")
        self.root.option_add("*TCombobox*Listbox.foreground", TXT)

    # ---------------------------------------------------------------- 界面
    def _build_ui(self):
        self._build_header()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.scene = GreenhouseScene(left)
        self.scene.pack(fill="both", expand=True, padx=3, pady=3)

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        for r in range(3):
            right.rowconfigure(r, weight=1, uniform="card")
        right.columnconfigure(0, weight=1)

        self.card_t, self.icon_t, self.val_t, self.sub_t = \
            self._make_card(right, "棚内温度", AMBER, "℃")
        self.card_h, self.icon_h, self.val_h, self.sub_h = \
            self._make_card(right, "棚内湿度", CYAN, "%RH")
        self.card_l, self.icon_l, self.val_l, self.sub_l = \
            self._make_card(right, "光照强度", SUN, "lx")

        self.card_t.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.card_h.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        self.card_l.grid(row=2, column=0, sticky="nsew")

        self._build_footer()
        self._render()

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 8))

        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, font=(FONT_UI, 17, "bold"),
                 bg=BG, fg=TXT).pack(anchor="w")
        tk.Label(left, text="ZigBee 温湿度 + 光敏  ·  温室大棚环境实时监测",
                 font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        ctrl = tk.Frame(head, bg=BG)
        ctrl.pack(side="right")

        tk.Label(ctrl, text="串口", font=(FONT_UI, 9), bg=BG, fg=MUTED).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(ctrl, textvariable=self.port_var, width=9,
                                    state="readonly", values=[], style="Dark.TCombobox")
        self.port_cb.pack(side="left", padx=(5, 6))

        self._button(ctrl, "刷新", self.refresh_ports, "#13251C", MUTED, 5).pack(side="left", padx=(0, 10))

        self.baud_var = tk.StringVar(value="9600")
        self.baud_cb = ttk.Combobox(ctrl, textvariable=self.baud_var, width=7, state="readonly",
                                    values=["9600", "38400", "115200"],
                                    style="Dark.TCombobox")
        self.baud_cb.pack(side="left", padx=(0, 10))

        self.btn_conn = self._button(ctrl, "连接", self.toggle_conn, GREEN, "#062A1B", 6)
        self.btn_conn.pack(side="left", padx=(0, 8))

        self.btn_demo = self._button(ctrl, "演示", self.toggle_demo, "#1A2B3D", "#8FC7FF", 6)
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

    def _make_card(self, parent, title, accent, unit):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(top, text=title, font=(FONT_UI, 10), bg=CARD, fg=MUTED).pack(side="left")
        bar = tk.Canvas(top, width=26, height=3, bg=CARD, highlightthickness=0)
        bar.create_rectangle(0, 0, 26, 3, fill=accent, outline="")
        bar.pack(side="right", pady=6)

        body = tk.Frame(card, bg=CARD)
        body.pack(expand=True, padx=16, pady=(0, 6))

        icon = tk.Canvas(body, width=44, height=44, bg=CARD, highlightthickness=0)
        icon.pack(side="left", padx=(0, 12), anchor="n", pady=(8, 0))

        col = tk.Frame(body, bg=CARD)
        col.pack(side="left", anchor="n")

        row = tk.Frame(col, bg=CARD)
        row.pack(anchor="w")
        val = tk.Label(row, text="--", font=(FONT_UI, 24, "bold"), bg=CARD, fg=MUTED)
        val.pack(side="left")
        tk.Label(row, text=unit, font=(FONT_UI, 10), bg=CARD, fg=MUTED) \
            .pack(side="left", anchor="s", pady=(0, 5), padx=(4, 0))

        sub = tk.Label(col, text="等待数据", font=(FONT_UI, 9), bg=CARD, fg=FAINT, anchor="w")
        sub.pack(anchor="w", pady=(2, 0))
        return card, icon, val, sub

    def _build_footer(self):
        bar = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x", padx=18, pady=(10, 14))
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=16, pady=9)

        self.info_var = tk.StringVar(value="等待温湿度终端与光敏终端上报…")
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
        if self.demo_worker is not None:          # 演示进行中，先停演示
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
        self.btn_conn.configure(text="连接", bg=GREEN, fg="#062A1B")
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
        self.info_var.set("演示模式：正在模拟协调器上电建网与终端上报…")

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
                                bg="#16211C" if on else GREEN,
                                fg=FAINT if on else "#062A1B")
        self.btn_demo.configure(text="停止演示" if on else "演示",
                                bg="#3D2410" if on else "#1A2B3D",
                                fg=AMBER if on else "#8FC7FF",
                                activebackground="#3D2410" if on else "#1A2B3D")

    def _reset_state(self):
        """清空所有已显示的数据，回到"等待数据"状态。"""
        self.th.update({"id": None, "addr": None, "cnt": 0, "temp": None, "hum": None})
        self.lt.update({"id": None, "addr": None, "cnt": 0,
                        "lux": None, "adc": None, "level": None})
        self.gateway_ready = False
        self.demo_clock = None
        self.gw_var.set("网关 未就绪")
        self.gw_label.configure(fg=FAINT)
        self.info_var.set("等待温湿度终端与光敏终端上报…")
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
            self.gateway_ready = True
            self.gw_var.set("网关 已就绪")
            self.gw_label.configure(fg=GREEN)

        info = parse_line(line)
        if not info:
            return

        # 靠帧内容判断是哪类终端：有温度/湿度就是温湿度帧，有 LUX 就是光照帧
        if ("temp" in info) or ("hum" in info):
            for k in ("temp", "hum", "tid", "addr", "cnt"):
                if k in info:
                    self.th[k if k != "tid" else "id"] = info[k]
        if ("lux" in info) or ("level" in info):
            for k in ("lux", "adc", "level", "tid", "addr", "cnt"):
                if k in info:
                    self.lt[k if k != "tid" else "id"] = info[k]

        self._render()

    # ---------------------------------------------------------------- 渲染
    def _render(self):
        th, lt = self.th, self.lt
        temp, hum, lux, level = th["temp"], th["hum"], lt["lux"], lt["level"]

        # ---------------- 温度卡片
        if temp is None:
            self.val_t.configure(text="--", fg=MUTED)
            self.sub_t.configure(text="等待数据")
            draw_thermo_icon(self.icon_t, FAINT, 0.5)
            self.card_t.configure(highlightbackground=BORDER)
        else:
            if temp < TEMP_LOW:
                word, color = "偏低，注意保温", CYAN
            elif temp > TEMP_HIGH:
                word, color = "偏高，注意通风", RED if temp > TEMP_HIGH + 2 else AMBER
            else:
                word, color = None, GREEN
            self.val_t.configure(text="%.1f" % temp, fg=color)
            if word is None:
                self.sub_t.configure(text="环境适宜 · 适宜区间 %g~%g℃" % (TEMP_LOW, TEMP_HIGH),
                                     fg=blend(color, MUTED, 0.25))
            else:
                self.sub_t.configure(text="%s · 适宜 %g~%g℃" % (word, TEMP_LOW, TEMP_HIGH),
                                     fg=blend(color, MUTED, 0.25))
            draw_thermo_icon(self.icon_t, color,
                             (temp - 5.0) / 35.0)
            self.card_t.configure(highlightbackground=blend(CARD, color, 0.45))

        # ---------------- 湿度卡片
        if hum is None:
            self.val_h.configure(text="--", fg=MUTED)
            self.sub_h.configure(text="等待数据")
            draw_drop_icon(self.icon_h, FAINT)
            self.card_h.configure(highlightbackground=BORDER)
        else:
            if hum < HUM_LOW:
                word, color = "偏干，建议灌溉", AMBER
            elif hum > HUM_HIGH:
                word, color = "偏湿，注意排湿", AMBER
            else:
                word, color = None, GREEN
            self.val_h.configure(text="%.1f" % hum, fg=color)
            if word is None:
                self.sub_h.configure(text="环境适宜 · 适宜区间 %g~%g%%RH" % (HUM_LOW, HUM_HIGH),
                                     fg=blend(color, MUTED, 0.25))
            else:
                self.sub_h.configure(text="%s · 适宜 %g~%g%%RH" % (word, HUM_LOW, HUM_HIGH),
                                     fg=blend(color, MUTED, 0.25))
            draw_drop_icon(self.icon_h, color)
            self.card_h.configure(highlightbackground=blend(CARD, color, 0.45))

        # ---------------- 光照卡片
        if lux is None:
            self.val_l.configure(text="--", fg=MUTED)
            self.sub_l.configure(text="等待数据")
            draw_cloud_icon(self.icon_l, FAINT, CARD)
            self.card_l.configure(highlightbackground=BORDER)
        else:
            if level == "DAY" or lux >= LUX_OK:
                word, color = "光照充足", SUN
                draw_sun_icon(self.icon_l, SUN)
            elif level == "DUSK" or lux >= LUX_LOW:
                word, color = "光照偏弱，建议补光", AMBER
                draw_cloud_icon(self.icon_l, AMBER, CARD)
            else:
                word, color = "光照不足，需要补光", MOON
                draw_cloud_icon(self.icon_l, MOON, CARD)
            self.val_l.configure(text="%d" % lux, fg=color)
            tail = " · ADC %d" % lt["adc"] if lt["adc"] is not None else ""
            self.sub_l.configure(text=word + tail, fg=blend(color, MUTED, 0.25))
            self.card_l.configure(highlightbackground=blend(CARD, color, 0.45))

        # ---------------- 场景
        self.scene.set_data(level, temp, hum)

        # ---------------- 底部
        parts = []
        if th["id"]:
            addr = "  0x%s" % th["addr"] if th["addr"] else ""
            parts.append("温湿度节点 T%s%s" % (th["id"], addr))
        elif th["temp"] is not None:
            parts.append("温湿度终端（串口直连本机）")

        if lt["id"]:
            addr = "  0x%s" % lt["addr"] if lt["addr"] else ""
            parts.append("光敏节点 T%s%s" % (lt["id"], addr))
        elif lt["lux"] is not None:
            parts.append("光敏终端（串口直连本机）")

        if parts:
            cnt = (th["cnt"] or 0) + (lt["cnt"] or 0)
            parts.append("累计上报 %d 帧" % cnt)
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
