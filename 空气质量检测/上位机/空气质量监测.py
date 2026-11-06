# -*- coding: utf-8 -*-
"""
================================================================================
 空气质量检测系统 · 上位机（ZigBee 协调器 + 烟雾传感器终端）
================================================================================

 运行方式：
    python 空气质量监测.py              自动扫描串口
    python 空气质量监测.py COM3         直接指定串口
    python 空气质量监测.py --demo       直接以演示模式启动（不需要串口）

 界面形态：
    写实照片底图 + 半透明玻璃面板（PIL 在后台合成，非卡通插画），
    左侧大号数码读数 + 32 段 LED 光柱，右侧扫描式浓度波形 + 设备信息表。
    烟雾浓度进入「严重」档时，面板描边与顶部报警条会红色脉冲呼吸。

 识别的串口行（协调器 UART0，9600-8-N-1）：
    [APP] ZigBee_NewApp gateway started
    [APP] network formed, gateway ready
    [GW] S1 addr=0x796F cnt=7 SMOKE=742 ADC=189 LEVEL=DANGER SRC=REMOTE

 字段：
    S1     网关按终端短地址首次出现顺序分配的编号
    addr   终端 16 位短地址
    cnt    该终端累计上报次数
    SMOKE  烟雾浓度（0 ~ 1000，等效 ppm）
    ADC    传感器原始采样值 0~255
    LEVEL  CLEAN 清新 / NORMAL 轻度异味 / WARN 明显烟雾 / DANGER 严重
    SRC    PERIODIC 周期上报 / KEY 本机按键手动 / REMOTE 网关请求手动

 安装依赖：  pip install pyserial pillow
================================================================================
"""

import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import messagebox

try:
    import serial
    import serial.tools.list_ports
except ImportError:                                    # 缺依赖时给出明确提示
    serial = None

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ==============================================================================
# 一、常量与配色
# ==============================================================================

SMOKE_MAX = 1000          # 与固件 NEWAPP_SMOKE_MAX 一致
SMOKE_NORMAL = 100        # 与固件 NEWAPP_SMOKE_NORMAL 一致
SMOKE_WARN = 300          # 与固件 NEWAPP_SMOKE_WARN 一致
SMOKE_DANGER = 700        # 与固件 NEWAPP_SMOKE_DANGER 一致

HISTORY_MAX = 120         # 波形最多保留的上报点数

FONT_CN = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# 标题用衬线字体：宋体骨架比黑体温润，配大字号 + 宽字距更像仪表铭牌
FONT_TITLE = "思源宋体 SemiBold"
FONT_LATIN = "Sitka Banner"

# 衬线字体在不同机器上未必都有，启动时按优先级回退一次
FONT_TITLE_FALLBACK = ("思源宋体 SemiBold", "华文中宋", "宋体", "微软雅黑")
FONT_LATIN_FALLBACK = ("Sitka Banner", "Cambria", "Constantia", "Georgia",
                       "Times New Roman")

TEXT = "#E9EFF6"
MUTED = "#98A8BA"
FAINT = "#63748A"
LINE = "#31414F"
PANEL_EDGE = "#4A5C6E"

# 等级 -> (中文名, 颜色, 建议)
LEVELS = {
    "CLEAN":  ("空气清新", "#3FD8A8", "空气洁净，无需任何处理"),
    "NORMAL": ("轻度异味", "#A8D14F", "建议开启通风，保持空气流通"),
    "WARN":   ("明显烟雾", "#F5A623", "检测到明显烟雾，请立即开窗排风并排查来源"),
    "DANGER": ("严重污染", "#FF4D4D", "浓度严重超标，请立即撤离现场并排查火源"),
}

SRC_TEXT = {
    "PERIODIC": "周期自动",
    "KEY": "手动 · 按键",
    "REMOTE": "手动 · 远程",
}

# 串口行解析：只认带 SMOKE= 的上报行，网关的下发提示行会被自然忽略
RE_SMOKE = re.compile(r"SMOKE=(\d+)")
RE_ADC = re.compile(r"ADC=(\d+)")
RE_LEVEL = re.compile(r"LEVEL=(CLEAN|NORMAL|WARN|DANGER)")
RE_SRC = re.compile(r"SRC=(PERIODIC|KEY|REMOTE)")
RE_SID = re.compile(r"\[GW\]\s*S(\d+)\b")
RE_ADDR = re.compile(r"addr=0x([0-9A-Fa-f]+)")
RE_CNT = re.compile(r"cnt=(\d+)")
RE_GW_START = re.compile(r"gateway started")
RE_GW_READY = re.compile(r"network formed")


def _rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def blend(c1, c2, t):
    """按比例混合两个 #RRGGBB 颜色（Canvas 没有 alpha，靠混色模拟透明度）"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return "#%02X%02X%02X" % (int(r1 + (r2 - r1) * t),
                              int(g1 + (g2 - g1) * t),
                              int(b1 + (b2 - b1) * t))


def level_of(density):
    """按与固件相同的阈值判定等级"""
    if density is None:
        return None
    if density >= SMOKE_DANGER:
        return "DANGER"
    if density >= SMOKE_WARN:
        return "WARN"
    if density >= SMOKE_NORMAL:
        return "NORMAL"
    return "CLEAN"


def parse_line(line):
    """解析一行串口输出；不是有效上报行时返回 None"""
    if not line:
        return None

    info = {}

    if RE_GW_START.search(line):
        info["event"] = "gw_start"
    if RE_GW_READY.search(line):
        info["event"] = "gw_ready"

    m = RE_SMOKE.search(line)
    if m:
        info["density"] = int(m.group(1))

    m = RE_ADC.search(line)
    if m:
        info["adc"] = int(m.group(1))

    m = RE_LEVEL.search(line)
    if m:
        info["level"] = m.group(1)

    m = RE_SRC.search(line)
    if m:
        info["src"] = m.group(1)

    m = RE_SID.search(line)
    if m:
        info["sid"] = m.group(1)

    m = RE_ADDR.search(line)
    if m:
        info["addr"] = m.group(1).upper()

    m = RE_CNT.search(line)
    if m:
        info["cnt"] = int(m.group(1))

    return info or None


def sim_density(t):
    """演示模式用的浓度曲线，形状与固件 sensor.c 的模拟曲线一致（90s 一轮）"""
    x = t % 90.0
    if x < 15:
        v = 40 + x * 20 / 15
    elif x < 28:
        v = 60 + (x - 15) * 50 / 13
    elif x < 40:
        v = 110 + (x - 28) * 230 / 12
    elif x < 52:
        v = 340 + (x - 40) * 400 / 12
    elif x < 62:
        v = 740 + (x - 52) * 30 / 10
    elif x < 78:
        v = 770 - (x - 62) * 650 / 16
    else:
        v = 120 - (x - 78) * 80 / 12
    return max(0.0, min(1000.0, v + random.uniform(-6, 6)))


# ==============================================================================
# 二、数据源：真实串口 / 演示模拟
# ==============================================================================

class SerialWorker(threading.Thread):
    """后台读串口，按行塞进队列，主线程只负责渲染"""

    def __init__(self, q, stop_evt, port, baud=9600):
        super().__init__(daemon=True)
        self.q = q
        self.stop_evt = stop_evt
        self.port = port
        self.baud = baud

    def run(self):
        if serial is None:
            self.q.put(("error", "缺少 pyserial，请先执行：pip install pyserial"))
            return

        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.3)
        except Exception as exc:
            self.q.put(("error", "打开 %s 失败：%s" % (self.port, exc)))
            return

        buf = b""
        try:
            while not self.stop_evt.is_set():
                try:
                    data = ser.read(256)
                except Exception as exc:
                    self.q.put(("error", "串口读取失败：%s" % exc))
                    break
                if not data:
                    continue
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("gbk", "ignore").strip("\r\n \t")
                    if text:
                        self.q.put(("line", text))
        finally:
            try:
                ser.close()
            except Exception:
                pass


class DemoWorker(threading.Thread):
    """演示数据源：生成与真实协调器逐字一致的串口文本行，界面走同一条解析路径"""

    def __init__(self, q, stop_evt):
        super().__init__(daemon=True)
        self.q = q
        self.stop_evt = stop_evt

    def run(self):
        self.q.put(("line", "[APP] ZigBee_NewApp gateway started"))
        time.sleep(0.4)
        self.q.put(("line", "[APP] network formed, gateway ready"))
        time.sleep(0.3)

        t0 = time.time()
        count = 0
        next_at = 0.9

        while not self.stop_evt.is_set():
            elapsed = time.time() - t0
            if elapsed >= next_at:
                count += 1
                dens = int(sim_density(elapsed))
                adc = int(dens * 255 / SMOKE_MAX)
                level = level_of(dens)

                if count % 11 == 0:
                    src = "REMOTE"
                    self.q.put(("line", "[GW] KEY1 report request -> 1 terminal(s)"))
                    self.q.put(("line",
                                "[GW] -> S1 addr=0x796F cmd=REPORT_NOW sent"))
                elif count % 7 == 0:
                    # 终端侧按 KEY1 手动上报，网关只看到一帧 SRC=KEY
                    src = "KEY"
                else:
                    src = "PERIODIC"

                self.q.put(("line",
                            "[GW] S1 addr=0x796F cnt=%d SMOKE=%d ADC=%d "
                            "LEVEL=%s SRC=%s" % (count, dens, adc, level, src)))
                next_at += 1.6
            time.sleep(0.08)


# ==============================================================================
# 三、绘图小工具（Canvas 没有圆角矩形与透明度，这里手工实现）
# ==============================================================================

def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    """用多边形拼一个圆角矩形，返回 item id"""
    r = max(0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    pts = []
    corners = ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
               (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180))
    for cx, cy, start in corners:
        for i in range(7):
            a = math.radians(start + i * 15)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return canvas.create_polygon(pts, smooth=False, **kw)


def pick_font(candidates, default="Microsoft YaHei UI"):
    """按优先级挑一个本机真正装了的字体名"""
    try:
        avail = set(tkfont.families())
    except Exception:
        return default
    for name in candidates:
        if name in avail:
            return name
    return default


def px_size(widget, px):
    """把"想要的像素字高"换算成 Tk 的 point 字号。

    Tk 的 scaling 就是 1 point 对应多少像素，高 DPI 屏幕上直接写 size=22
    会被放大成 44px，所以标题一律按像素反算，换台机器才长得一样。
    """
    try:
        sc = float(widget.tk.call("tk", "scaling"))
    except Exception:
        sc = 1.0
    if sc <= 0:
        sc = 1.0
    return max(6, int(round(px / sc)))


_FONT_CACHE = {}


def sized_font(canvas, family, size_px, weight="normal"):
    """按像素字高取字体对象；标题会在每次重绘时用到，缓存一份避免反复创建"""
    key = (family, px_size(canvas, size_px), weight)
    f = _FONT_CACHE.get(key)
    if f is None:
        try:
            f = tkfont.Font(root=canvas, family=key[0], size=key[1],
                            weight=key[2])
        except Exception:
            f = tkfont.Font(root=canvas, family="Microsoft YaHei UI",
                            size=key[1], weight=key[2])
        _FONT_CACHE[key] = f
    return f


def spaced_text(canvas, x, y, text, family, size_px, fill, spacing=0.0,
                weight="normal", anchor="center", tags="st"):
    """逐字绘制实现字距控制（Canvas 原生不支持 letter-spacing）。

    size_px 是希望的像素字高。anchor="center" 时 x 为整串中心，
    返回 (左端, 右端)，方便外面画左右对称的装饰线。
    """
    f = sized_font(canvas, family, size_px, weight)
    gaps = [f.measure(ch) for ch in text]
    total = sum(gaps) + spacing * max(0, len(text) - 1)

    cur = x - total / 2.0 if anchor == "center" else x
    for ch, gw in zip(text, gaps):
        if ch != " ":
            canvas.create_text(cur + gw / 2.0, y, text=ch, anchor="center",
                               fill=fill, font=f, tags=tags)
        cur += gw + spacing
    return (x - total / 2.0, x + total / 2.0)


def hairline(canvas, x0, x1, y, color, steps=10, tags="st"):
    """一条两端渐隐的细线，用来给标题做对称装饰"""
    if x1 - x0 < 4:
        return
    seg = (x1 - x0) / float(steps)
    for i in range(steps):
        t = 1.0 - abs((i + 0.5) / steps * 2.0 - 1.0)      # 中间不透明度最高
        canvas.create_line(x0 + i * seg, y, x0 + (i + 1) * seg + 0.5, y,
                           fill=blend(color, "#000000", 1.0 - t),
                           width=1, tags=tags)


# ==============================================================================
# 四、主界面
# ==============================================================================

class MonitorApp:
    def __init__(self, root: tk.Tk, auto_port: str = ""):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_evt = None
        self.demo = False

        self._closing = False
        self._after_id = None
        self._resize_job = None
        self._built = (0, 0)
        self._bg_photo = None
        self._bg_item = None
        self._master = None
        self._panel_list = []

        self.phase = 0.0
        self.hist = []
        self.gw_ready = False
        self.gw_started = False

        self.state = {
            "density": None, "adc": None, "level": None, "src": None,
            "sid": None, "addr": None, "cnt": None, "ts": None,
        }

        self.ports = []
        self.port_idx = 0

        self.cw, self.ch = 1180, 740      # 首帧布局的兜底尺寸，Configure 后会被真实值覆盖

        self._setup_window()
        self._setup_canvas()
        self._load_backdrop()

        # 标题字体要在 root 存在后才能探测本机装了哪些
        self.font_title = pick_font(FONT_TITLE_FALLBACK, "宋体")
        self.font_latin = pick_font(FONT_LATIN_FALLBACK, "Georgia")

        self._layout()
        self.refresh_ports()

        if auto_port:
            self._select_port(auto_port)

        self.q_pump()

        if auto_port:
            self.root.after(400, self.toggle_conn)

    # ------------------------------------------------------------------ 窗口
    def _setup_window(self):
        self.root.title("空气质量检测系统 · ZigBee 烟雾监测")
        self.root.configure(bg="#080D13")
        self.root.geometry("1180x740")
        self.root.minsize(1040, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_canvas(self):
        self.canvas = tk.Canvas(self.root, bg="#080D13", highlightthickness=0,
                                bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)

    def _load_backdrop(self):
        """加载写实底图；找不到就用深色渐变兜底，不影响功能"""
        if not HAS_PIL:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("bg.jpg", "bg.png"):
            path = os.path.join(here, "assets", name)
            if os.path.exists(path):
                try:
                    self._master = Image.open(path).convert("RGB")
                    return
                except Exception:
                    self._master = None

    # ------------------------------------------------------------------ 布局
    def _on_configure(self, event):
        if self._closing:
            return
        if abs(event.width - self._built[0]) < 6 and \
           abs(event.height - self._built[1]) < 6:
            return
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(120, self._apply_layout)

    def _apply_layout(self):
        self._resize_job = None
        if self._closing:
            return
        self._layout()
        self._rebuild_background()
        self._draw_static()
        self._draw_dynamic()

    def _layout(self):
        w = max(self.canvas.winfo_width(), 1040)
        h = max(self.canvas.winfo_height(), 680)
        self.cw, self.ch = w, h

        m = 26
        top = 110
        bottom = h - 64

        lx1 = m + int((w - 2 * m) * 0.545)
        rx0 = lx1 + 18
        rx1 = w - m

        wave_h = int((bottom - top) * 0.56)

        self.L = {
            "left": (m, top, lx1, bottom),
            "wave": (rx0, top, rx1, top + wave_h),
            "info": (rx0, top + wave_h + 16, rx1, bottom),
        }
        self._panel_list = list(self.L.values())

        right = w - m
        self.B = {
            "demo": (right - 78, 20, right, 54),
            "conn": (right - 78 - 10 - 86, 20, right - 78 - 10, 54),
            "port": (right - 78 - 10 - 86 - 10 - 142, 20,
                     right - 78 - 10 - 86 - 10, 54),
        }

    # ------------------------------------------------------------------ 背景
    def _rebuild_background(self):
        w, h = self.cw, self.ch
        self._built = (w, h)

        if not HAS_PIL:
            self.canvas.delete("bg")
            self.canvas.create_rectangle(0, 0, w, h, fill="#0B1119", outline="",
                                         tags="bg")
            self._bg_item = None
            self.canvas.tag_lower("bg")
            return

        img = self._compose_background(w, h)
        self._bg_photo = ImageTk.PhotoImage(img)

        if self._bg_item is None:
            self._bg_item = self.canvas.create_image(0, 0, anchor="nw",
                                                     image=self._bg_photo,
                                                     tags="bg")
        else:
            self.canvas.itemconfig(self._bg_item, image=self._bg_photo)
        self.canvas.tag_lower("bg")

    def _compose_background(self, w, h):
        """底图 cover 裁切 -> 压暗 -> 冷调 -> 暗角 -> 玻璃面板"""
        if self._master is not None:
            mw, mh = self._master.size
            k = max(w / float(mw), h / float(mh))
            nw, nh = int(mw * k + 0.5), int(mh * k + 0.5)
            img = self._master.resize((nw, nh), Image.LANCZOS)
            left = (nw - w) // 2
            top = (nh - h) // 2
            img = img.crop((left, top, left + w, top + h))
            img = img.filter(ImageFilter.GaussianBlur(0.7))
            img = ImageEnhance.Brightness(img).enhance(0.70)
            img = ImageEnhance.Contrast(img).enhance(1.08)
            tint = Image.new("RGB", (w, h), (9, 15, 24))
            img = Image.blend(img, tint, 0.24)
        else:
            # 兜底：竖向渐变，别让界面在没有图片时变成纯黑
            img = Image.new("RGB", (w, h), (10, 15, 22))
            d = ImageDraw.Draw(img)
            for y in range(h):
                t = y / float(h)
                d.line((0, y, w, y), fill=(int(11 + 16 * t),
                                           int(17 + 20 * t),
                                           int(26 + 24 * t)))

        # 暗角：中心亮、四周压黑，让中间信息更突出
        try:
            grad = Image.radial_gradient("L").resize((w, h), Image.BILINEAR)
            mask = grad.point(lambda v: int(v * 0.48))
            img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, mask)
        except Exception:
            pass

        # 玻璃面板：半透明深色 + 极细亮边
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for (x0, y0, x1, y1) in self._panel_list:
            od.rounded_rectangle((x0, y0, x1, y1), radius=14,
                                 fill=(13, 19, 27, 188),
                                 outline=(150, 180, 210, 38), width=1)
            od.line((x0 + 16, y0 + 1, x1 - 16, y0 + 1), fill=(190, 215, 235, 26))
        return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # ------------------------------------------------------------------ 静态层
    def _draw_static(self):
        c = self.canvas
        c.delete("st")
        w, h = self.cw, self.ch

        # ---------------- 顶部铭牌：居中宋体大标题 + 衬线英文小标
        # 标题不再贴左上角，改为整幅画面上部居中；两侧衬对称细线，底部一条渐隐收口线
        cx = w / 2.0
        b_left = self.B["port"][0]                  # 右侧按钮组左边界，装饰线不能越界

        t_left, t_right = spaced_text(
            c, cx, 42, "空气质量检测系统",
            self.font_title, 32, "#F3F8FD", spacing=9, tags="st")

        # 两侧对称短线：长度随窗口自适应，永远左右等长
        tick = min(88, max(0, (b_left - 16) - (t_right + 14)))
        if tick > 8:
            hairline(c, t_right + 14, t_right + 14 + tick, 42, "#8FA6BC",
                     steps=8, tags="st")
            hairline(c, t_left - 14 - tick, t_left - 14, 42, "#8FA6BC",
                     steps=8, tags="st")

        spaced_text(c, cx, 83, "AIR QUALITY MONITORING",
                    self.font_latin, 11, blend(TEXT, FAINT, 0.55),
                    spacing=4, tags="st")

        # 页眉收口线：两端渐隐，把标题区和三块面板分开
        hairline(c, 26, w - 26, 104, "#6E8499", steps=16, tags="st")

        # 右上：端口 / 连接 / 演示
        self._draw_button("port", self._port_label(), MUTED)
        if self.worker is None:
            self._draw_button("conn", "连接", "#3FD8A8")
        else:
            self._draw_button("conn", "断开", "#FF7B72")
        self._draw_button("demo", "停止演示" if self.demo else "演示",
                          "#F5A623" if self.demo else MUTED)

        # 面板标题
        lx0, ly0, lx1, _ = self.L["left"]
        c.create_text(lx0 + 30, ly0 + 30, text="烟雾浓度  /  SMOKE DENSITY",
                      anchor="w", fill=MUTED, font=(FONT_CN, 10), tags="st")

        wx0, wy0, wx1, _ = self.L["wave"]
        c.create_text(wx0 + 24, wy0 + 24, text="浓度趋势  /  LAST %d REPORTS"
                      % HISTORY_MAX, anchor="w", fill=MUTED,
                      font=(FONT_CN, 10), tags="st")

        ix0, iy0, _, _ = self.L["info"]
        c.create_text(ix0 + 24, iy0 + 24, text="链路与设备  /  LINK STATUS",
                      anchor="w", fill=MUTED, font=(FONT_CN, 10), tags="st")

        # 底部状态条
        c.create_line(26, h - 46, w - 26, h - 46, fill=blend(LINE, "#000000", 0.2),
                      tags="st")
        if self.worker is None:
            c.create_oval(28, h - 34, 38, h - 24, fill=FAINT, outline="", tags="st")
            left_txt = "未连接"
        elif self.demo:
            c.create_oval(28, h - 34, 38, h - 24, fill="#F5A623", outline="",
                          tags="st")
            left_txt = "演示模式 · 模拟数据"
        else:
            c.create_oval(28, h - 34, 38, h - 24, fill="#3FD8A8", outline="",
                          tags="st")
            left_txt = "已连接 %s · 9600 8-N-1" % self._port_label()

        c.create_text(46, h - 29, text=left_txt, anchor="w", fill=MUTED,
                      font=(FONT_CN, 9), tags="st")

        right_txt = "网关已建网" if self.gw_ready else (
            "网关启动中" if self.gw_started else "等待网关上电")
        c.create_text(w - 28, h - 29, text=right_txt, anchor="e",
                      fill=FAINT, font=(FONT_CN, 9), tags="st")

        # 波形网格（静态）
        self._draw_wave_grid()

    def _draw_button(self, key, label, color):
        x0, y0, x1, y1 = self.B[key]
        hot = self._hit.get(key, False)
        fill = blend("#101925", color, 0.16)
        round_rect(self.canvas, x0, y0, x1, y1, 9,
                   fill=blend(fill, color, 0.20) if hot else fill,
                   outline=blend(color, "#000000", 0.35), width=1, tags="st")
        self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=label,
                                fill=blend(color, TEXT, 0.25),
                                font=(FONT_CN, 9, "bold"), tags="st")

    def _draw_wave_grid(self):
        c = self.canvas
        wx0, wy0, wx1, wy1 = self.L["wave"]
        px0, py0 = wx0 + 52, wy0 + 56
        px1, py1 = wx1 - 20, wy1 - 30

        for v in (0, 250, 500, 750, 1000):
            y = py1 - (py1 - py0) * v / float(SMOKE_MAX)
            c.create_line(px0, y, px1, y, fill=LINE, dash=(2, 5), tags="st")
            c.create_text(px0 - 10, y, text=str(v), anchor="e", fill=FAINT,
                          font=(FONT_MONO, 8), tags="st")

        steps = 6
        for i in range(steps + 1):
            x = px0 + (px1 - px0) * i / float(steps)
            c.create_line(x, py0, x, py1, fill=blend(LINE, "#000000", 0.35),
                          dash=(2, 6), tags="st")

        # 阈值参考线：超标 / 严重
        for v, col in ((SMOKE_WARN, "#F5A623"), (SMOKE_DANGER, "#FF4D4D")):
            y = py1 - (py1 - py0) * v / float(SMOKE_MAX)
            c.create_line(px0, y, px1, y, fill=blend("#1A2430", col, 0.55),
                          dash=(6, 4), tags="st")
            c.create_text(px1 - 4, y - 8, text="%d" % v, anchor="e",
                          fill=blend(MUTED, col, 0.45), font=(FONT_MONO, 8),
                          tags="st")

    # ------------------------------------------------------------------ 动态层
    def _draw_dynamic(self):
        c = self.canvas
        c.delete("dyn")

        st = self.state
        dens = st["density"]
        lvl = st["level"] if st["level"] else level_of(dens)
        if lvl not in LEVELS:
            lvl = None
        color = LEVELS[lvl][1] if lvl else FAINT

        self._draw_readout(dens, lvl, color)
        self._draw_ladder(dens, lvl)
        self._draw_wave(lvl, color)
        self._draw_info(lvl, color)
        self._draw_alarm(lvl, color)

    def _draw_readout(self, dens, lvl, color):
        c = self.canvas
        lx0, ly0, lx1, ly1 = self.L["left"]
        inner_w = lx1 - lx0 - 60
        num_size = max(46, min(72, int((ly1 - ly0) * 0.125)))

        # 等级标签（右上角）
        name = LEVELS[lvl][0] if lvl else "等待数据"
        pill_w = 132
        px1 = lx1 - 28
        px0 = px1 - pill_w
        round_rect(c, px0, ly0 + 20, px1, ly0 + 54, 17,
                   fill=blend("#0C141C", color, 0.20),
                   outline=blend(color, "#000000", 0.30), width=1, tags="dyn")
        c.create_oval(px0 + 12, ly0 + 31, px0 + 24, ly0 + 43,
                      fill=color, outline="", tags="dyn")
        c.create_text(px0 + 32, ly0 + 37, text=name, anchor="w",
                      fill=blend(color, "#FFFFFF", 0.35),
                      font=(FONT_CN, 10, "bold"), tags="dyn")

        # 大号读数：先画再取实际包围盒，后面的元素按它的下沿排，避免叠字
        val = "--" if dens is None else "%d" % dens
        big_y = ly0 + 62
        item = c.create_text(lx0 + 32, big_y, text=val, anchor="nw",
                             fill=color if dens is not None else FAINT,
                             font=(FONT_MONO, num_size, "bold"), tags="dyn")
        box = c.bbox(item)
        num_bottom = box[3] if box else (big_y + num_size)

        c.create_text(lx0 + 34, num_bottom - 2, text="ppm · 等效烟雾浓度",
                      anchor="nw", fill=blend(color, MUTED, 0.45),
                      font=(FONT_CN, 9), tags="dyn")

        self._ladder_y = num_bottom + 40      # 供 _draw_ladder 使用
        self._advice_y = self._ladder_y + 92

        # 建议
        advice = LEVELS[lvl][2] if lvl else "等待终端上报数据，请确认协调器已建网、烟雾终端已入网"
        c.create_text(lx0 + 30, self._advice_y, text=advice, anchor="nw",
                      fill=blend(color, TEXT, 0.55) if lvl else MUTED,
                      font=(FONT_CN, 10), width=inner_w, tags="dyn")

        # 分隔线 + 阈值说明 + 原始采样
        c.create_line(lx0 + 30, ly1 - 96, lx1 - 28, ly1 - 96,
                      fill=blend(LINE, "#000000", 0.15), tags="dyn")

        adc = self.state["adc"]
        c.create_text(lx0 + 30, ly1 - 76, text="原始采样", anchor="w",
                      fill=FAINT, font=(FONT_CN, 9), tags="dyn")
        c.create_text(lx0 + 112, ly1 - 76,
                      text="—" if adc is None else "%d / 255" % adc, anchor="w",
                      fill=MUTED, font=(FONT_MONO, 10), tags="dyn")

        c.create_text(lx1 - 28, ly1 - 76,
                      text="阈值 %d / %d / %d" % (SMOKE_NORMAL, SMOKE_WARN,
                                                  SMOKE_DANGER),
                      anchor="e", fill=FAINT, font=(FONT_MONO, 8), tags="dyn")

        # ADC 细条
        bx0, bx1 = lx0 + 30, lx1 - 28
        by0, by1 = ly1 - 58, ly1 - 50
        c.create_rectangle(bx0, by0, bx1, by1, fill="#0E151D", outline="",
                           tags="dyn")
        if adc:
            c.create_rectangle(bx0, by0, bx0 + (bx1 - bx0) * min(1.0, adc / 255.0),
                               by1, fill=blend("#0E151D", color, 0.75),
                               outline="", tags="dyn")

    def _draw_ladder(self, dens, lvl):
        """32 段 LED 光柱：每段自身的量程决定颜色，点亮的段用实色"""
        c = self.canvas
        lx0, ly0, lx1, _ = self.L["left"]
        x0, x1 = lx0 + 30, lx1 - 28
        y0 = getattr(self, "_ladder_y", ly0 + 200)
        y1 = y0 + 40

        segs = 32
        gap = 4
        seg_w = (x1 - x0 - gap * (segs - 1)) / float(segs)
        frac = 0.0 if dens is None else min(1.0, dens / float(SMOKE_MAX))
        lit = int(round(frac * segs))

        for i in range(segs):
            v = (i + 0.5) / segs * SMOKE_MAX
            if v >= SMOKE_DANGER:
                col = "#FF4D4D"
            elif v >= SMOKE_WARN:
                col = "#F5A623"
            elif v >= SMOKE_NORMAL:
                col = "#A8D14F"
            else:
                col = "#3FD8A8"

            sx0 = x0 + i * (seg_w + gap)
            sx1 = sx0 + seg_w
            if i < lit:
                c.create_rectangle(sx0, y0, sx1, y1, fill=col, outline="",
                                   tags="dyn")
            else:
                c.create_rectangle(sx0, y0, sx1, y1,
                                   fill=blend("#0B1219", col, 0.13),
                                   outline="", tags="dyn")

        # 刻度
        for v in (0, 250, 500, 750, 1000):
            tx = x0 + (x1 - x0) * v / float(SMOKE_MAX)
            anchor = "w" if v == 0 else ("e" if v == SMOKE_MAX else "center")
            c.create_text(tx, y1 + 14, text=str(v), anchor=anchor, fill=FAINT,
                          font=(FONT_MONO, 8), tags="dyn")

        # 当前值指针
        if dens is not None:
            mx = x0 + (x1 - x0) * frac
            c.create_polygon(mx - 5, y0 - 10, mx + 5, y0 - 10, mx, y0 - 2,
                             fill=LEVELS[lvl][1] if lvl else MUTED,
                             outline="", tags="dyn")

    def _draw_wave(self, lvl, color):
        c = self.canvas
        wx0, wy0, wx1, wy1 = self.L["wave"]
        px0, py0 = wx0 + 52, wy0 + 56
        px1, py1 = wx1 - 20, wy1 - 30

        data = self.hist[-HISTORY_MAX:]
        if len(data) < 2:
            c.create_text((px0 + px1) / 2, (py0 + py1) / 2,
                          text="等待上报数据…", fill=FAINT,
                          font=(FONT_CN, 10), tags="dyn")
            return

        step = (px1 - px0) / float(HISTORY_MAX - 1)
        offset = HISTORY_MAX - len(data)

        pts = []
        for i, v in enumerate(data):
            x = px0 + (offset + i) * step
            y = py1 - (py1 - py0) * min(1.0, v / float(SMOKE_MAX))
            pts.extend((x, y))

        # 面积
        c.create_polygon(pts + [px0 + (offset + len(data) - 1) * step, py1,
                                px0 + offset * step, py1],
                         fill=blend("#0D151E", color, 0.20), outline="",
                         tags="dyn")
        # 光晕 + 主线
        c.create_line(*pts, fill=blend("#0D151E", color, 0.45), width=5,
                      smooth=True, tags="dyn")
        c.create_line(*pts, fill=color, width=2, smooth=True, tags="dyn")

        # 最新点
        x_last, y_last = pts[-2], pts[-1]
        c.create_oval(x_last - 4, y_last - 4, x_last + 4, y_last + 4,
                      fill=color, outline=blend(color, "#FFFFFF", 0.6),
                      tags="dyn")
        c.create_text(px1, wy0 + 26, text="%d ppm" % data[-1], anchor="e",
                      fill=blend(color, TEXT, 0.3), font=(FONT_MONO, 10, "bold"),
                      tags="dyn")

    def _draw_info(self, lvl, color):
        c = self.canvas
        ix0, iy0, ix1, iy1 = self.L["info"]
        st = self.state

        col1 = ix0 + 24
        col2 = ix0 + (ix1 - ix0) * 0.52
        rows = (iy0 + 60, iy0 + 114, iy0 + 168)

        def cell(x, y, label, value, vcolor=None):
            c.create_text(x, y, text=label, anchor="nw", fill=FAINT,
                          font=(FONT_CN, 8), tags="dyn")
            c.create_text(x, y + 16, text=value, anchor="nw",
                          fill=vcolor or TEXT, font=(FONT_MONO, 12, "bold"),
                          tags="dyn")

        sid = ("S" + st["sid"]) if st["sid"] else "—"
        cell(col1, rows[0], "节点编号", sid)
        cell(col2, rows[0], "短地址",
             ("0x" + st["addr"]) if st["addr"] else "—")

        cell(col1, rows[1], "上报次数",
             "%d" % st["cnt"] if st["cnt"] is not None else "—")
        cell(col2, rows[1], "上报方式",
             SRC_TEXT.get(st["src"], "—"),
             blend("#F5A623", TEXT, 0.15) if st["src"] in ("KEY", "REMOTE") else None)

        cell(col1, rows[2], "更新时间",
             st["ts"].strftime("%H:%M:%S") if st["ts"] else "—")
        cell(col2, rows[2], "报警状态",
             "触发" if lvl == "DANGER" else ("关注" if lvl == "WARN" else "正常"),
             color if lvl else MUTED)

    def _draw_alarm(self, lvl, color):
        c = self.canvas
        # 报警层单独用一个 tag，动画每帧先清掉自己上一帧画的，避免堆叠
        c.delete("alarm")

        if lvl != "DANGER":
            return

        w = self.cw
        # 呼吸脉冲
        k = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self.phase))

        # 整窗红色描边
        round_rect(c, 6, 6, w - 6, self.ch - 6, 12, fill="",
                   outline=blend("#120A0C", "#FF3B3B", k), width=2, tags="alarm")

        # 顶部报警条：标题已居中，这里改挂到页眉左端的空白处，不压标题
        ax0 = 26
        round_rect(c, ax0, 20, ax0 + 212, 54, 15,
                   fill=blend("#160B0E", "#FF3B3B", 0.28 + 0.35 * k),
                   outline=blend("#FF3B3B", "#000000", 0.35), width=1,
                   tags="alarm")
        c.create_text(ax0 + 106, 37, text="● 烟雾报警  ALARM", fill="#FFE4E4",
                      font=(FONT_CN, 9, "bold"), tags="alarm")

    # ------------------------------------------------------------------ 交互
    _hit = {}

    def _on_motion(self, event):
        hit = {k: (r[0] <= event.x <= r[2] and r[1] <= event.y <= r[3])
               for k, r in getattr(self, "B", {}).items()}
        if hit != self._hit:
            self._hit = hit
            self.canvas.configure(cursor="hand2" if any(hit.values()) else "")
            if hasattr(self, "B"):
                self._draw_static()

    def _on_click(self, event):
        for key, (x0, y0, x1, y1) in getattr(self, "B", {}).items():
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                if key == "conn":
                    self.toggle_conn()
                elif key == "demo":
                    self.toggle_demo()
                elif key == "port":
                    self.cycle_port()
                return

    def _port_label(self):
        return self.ports[self.port_idx] if self.ports else "无串口"

    def refresh_ports(self):
        self.ports = []
        if serial is not None:
            try:
                self.ports = [p.device for p in
                              serial.tools.list_ports.comports()]
            except Exception:
                self.ports = []
        self.port_idx = 0 if self.port_idx >= len(self.ports) else self.port_idx

    def cycle_port(self):
        if not self.ports:
            self.refresh_ports()
        if not self.ports:
            return
        self.port_idx = (self.port_idx + 1) % len(self.ports)
        self._draw_static()

    def _select_port(self, port):
        self.refresh_ports()
        if port in self.ports:
            self.port_idx = self.ports.index(port)
        else:
            self.ports.insert(0, port)
            self.port_idx = 0
        self._draw_static()

    # ------------------------------------------------------------------ 连接
    def toggle_conn(self):
        if self.worker is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if serial is None:
            messagebox.showerror(
                "缺少依赖",
                "未安装 pyserial，请先在命令行执行：\n\npip install pyserial")
            return
        if self.demo:
            self.stop_worker()
            self.demo = False

        self.refresh_ports()
        if not self.ports:
            messagebox.showwarning("没有串口", "未检测到可用串口，可先点「演示」查看界面效果。")
            return

        port = self._port_label()
        self.stop_evt = threading.Event()
        self.worker = SerialWorker(self.q, self.stop_evt, port)
        self.worker.start()
        self._draw_static()

    def disconnect(self):
        self.stop_worker()
        self._draw_static()

    def toggle_demo(self):
        if self.demo:
            self.stop_worker()
            self.demo = False
            self.reset_state()
            self._draw_static()
            self._draw_dynamic()
        else:
            self.stop_worker()
            self.reset_state()
            self.demo = True
            self.gw_started = False
            self.gw_ready = False
            self.stop_evt = threading.Event()
            self.worker = DemoWorker(self.q, self.stop_evt)
            self.worker.start()
            self._draw_static()
            self._draw_dynamic()

    def stop_worker(self):
        try:
            if self.stop_evt is not None:
                self.stop_evt.set()
            if self.worker is not None:
                self.worker.join(timeout=0.6)
        except Exception:
            pass
        self.worker = None
        self.stop_evt = None

    def reset_state(self):
        self.hist = []
        for k in self.state:
            self.state[k] = None

    # ------------------------------------------------------------------ 队列
    def q_pump(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self.handle_line(payload)
                elif kind == "error":
                    self.stop_worker()
                    self._draw_static()
                    messagebox.showerror("串口错误", payload)
        except queue.Empty:
            pass
        self._after_id = self.root.after(60, self.q_pump)

    def handle_line(self, line):
        info = parse_line(line)
        if not info:
            return

        event = info.get("event")
        if event == "gw_start":
            self.gw_started = True
            self._draw_static()
            return
        if event == "gw_ready":
            self.gw_ready = True
            self._draw_static()
            return

        if "density" not in info:
            return

        st = self.state
        st["density"] = info["density"]
        st["level"] = info.get("level") or level_of(info["density"])
        st["adc"] = info.get("adc", st["adc"])
        st["src"] = info.get("src")
        st["sid"] = info.get("sid", st["sid"])
        st["addr"] = info.get("addr", st["addr"])
        st["cnt"] = info.get("cnt", st["cnt"])
        st["ts"] = datetime.now()

        self.hist.append(info["density"])
        if len(self.hist) > HISTORY_MAX * 2:
            self.hist = self.hist[-HISTORY_MAX:]

        self._draw_dynamic()

    # ------------------------------------------------------------------ 动画
    def animate(self):
        if self._closing:
            return
        self.phase += 0.16
        if self.state["level"] == "DANGER":
            self._draw_alarm("DANGER", LEVELS["DANGER"][1])
        self.root.after(80, self.animate)

    # ------------------------------------------------------------------ 关闭
    def on_close(self):
        self._closing = True
        for attr in ("_after_id", "_resize_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self.stop_worker()
        self.root.destroy()


# ==============================================================================
# 五、入口
# ==============================================================================

def main():
    port = ""
    demo = False
    for arg in sys.argv[1:]:
        if arg in ("--demo", "-d"):
            demo = True
        elif arg.startswith("--port="):
            port = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            port = arg

    root = tk.Tk()
    app = MonitorApp(root, auto_port=port)

    if demo:
        root.after(500, app.toggle_demo)

    app._draw_static()
    app._draw_dynamic()
    root.after(80, app.animate)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
