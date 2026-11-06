# ZigBee_NewApp — 基于 ZigBee 的大棚环境监测系统

基于 Z-Stack 2.5.1a 与 CC2530 的温室大棚环境监测项目。网络形态为 **1 个协调器网关 + 2 个终端**（星型）：

- **协调器网关**：上电自动建网，接收两个终端上报的环境数据，解析后经串口打印到上位机，并用 LED 提示收到的数据类型。
- **温湿度终端**：上电入网后**自动按周期采集空气温度 / 湿度并上报**，上电时先自检一次传感器并把结果打印到串口。
- **光敏终端**：上电入网后**自动按周期采集光照强度并上报**，并按大棚阈值判定光照等级（不足 / 偏弱 / 充足）。

> 本工程由「实验居家 —— 室内环境监测系统」改造而来：把原来的**火焰报警终端换成了光敏终端**，
> 并把业务场景从"室内环境警告"换成"大棚环境监测"。协议栈、OSAL 调度、按键 / LED / 串口驱动全部复用，
> 只重写了应用层与传感器驱动层。

---

## 一、工程位置

```
Projects\zstack\Samples\ZigBee_NewApp\
├─ CC2530DB\
│  ├─ ZigBee_NewApp.eww       IAR 工作区（打开这个）
│  ├─ ZigBee_NewApp.ewp       工程文件，含 4 个编译配置
│  ├─ ZigBee_NewApp.ewd       调试器配置
│  └─ Source\NewAppHw.c/.h    Demo 跳线判定（预留，未参与业务逻辑）
├─ Source\
│  ├─ NewApp.c / NewApp.h     应用层主逻辑
│  ├─ sensor.c / sensor.h     温湿度（DHT11）+ 光照（光敏）驱动
│  └─ OSAL_NewApp.c           OSAL 任务表
└─ README.md
```

上位机在工程目录外的 `新实验\上位机\`：

```
上位机\
├─ 大棚环境监测.py      Python + tkinter 图形界面
├─ requirements.txt
└─ 预览图\              三张界面效果图
```

---

## 二、四个编译配置

| 配置 | 角色宏 | 烧录到 | 输出 hex |
| --- | --- | --- | --- |
| CoordinatorEB | NEWAPP_ROLE_COORDINATOR | 协调器网关 | ZigBee_NewApp-Coordinator.hex |
| SensorTerminalEB | NEWAPP_ROLE_SENSOR_TERM | **温湿度终端** | ZigBee_NewApp-SensorTerminal.hex |
| LightTerminalEB | NEWAPP_ROLE_LIGHT_TERM | **光敏终端** | ZigBee_NewApp-LightTerminal.hex |
| RouterEB | NEWAPP_ROLE_SENSOR_TERM | 可选路由中继（一般不用） | ZigBee_NewApp-Router.hex |

hex 输出在各自的 `Exe` 目录，例如 `CC2530DB\CoordinatorEB\Exe\`。

> **最小可运行组合**：协调器烧 `CoordinatorEB`，温湿度节点烧 `SensorTerminalEB`，
> 光敏节点烧 `LightTerminalEB`。`RouterEB` 只有需要中继距离时才用。

---

## 三、串口输出（9600-8-N-1）

### 3.1 协调器网关上电

```
[APP] ZigBee_NewApp gateway started
[APP] network formed, gateway ready
```

### 3.2 温湿度终端上电（串口接在终端上时可见）

```
[APP] ZigBee_NewApp sensor-terminal started
[APP] temp/hum sensor ready, T=25.6C H=60.5%RH
[APP] joined network
[APP] T=25.7C H=60.2%RH cnt=1 tx=OK
[APP] T=25.8C H=59.9%RH cnt=2 tx=OK
```

### 3.3 光敏终端上电（串口接在终端上时可见）

```
[APP] ZigBee_NewApp light-terminal started
[APP] light sensor ready, LUX=18 ADC=2 LEVEL=NIGHT
[APP] joined network
[APP] LUX=19 ADC=2 LEVEL=NIGHT cnt=1 tx=OK
[APP] LUX=21 ADC=3 LEVEL=NIGHT cnt=2 tx=OK
```

### 3.4 网关持续打印大棚环境（两个终端入网后自动上报，各 5s 一帧）

```
[GW] T1 addr=0xAF10 cnt=1 T=25.6C H=60.5%RH
[GW] T2 addr=0x4C24 cnt=1 LUX=18 ADC=2 LEVEL=NIGHT
[GW] T1 addr=0xAF10 cnt=2 T=25.7C H=60.2%RH
[GW] T2 addr=0x4C24 cnt=2 LUX=1260 ADC=160 LEVEL=DAY
```

字段含义：

| 字段 | 说明 |
| --- | --- |
| `T1` / `T2` | 网关按终端短地址**首次出现的顺序**自动编号，不区分类型 |
| `addr` | 终端 16 位短地址 |
| `cnt` | 该终端累计上报次数（从 1 开始递增，可用于验证丢包） |
| `T` | 空气温度，单位 ℃ |
| `H` | 空气湿度，单位 %RH |
| `LUX` | 光照强度，单位 lx |
| `ADC` | 光敏原始采样值（0~255），用于判断传感器是否工作正常 |
| `LEVEL` | 光照等级：`NIGHT` 不足 / `DUSK` 偏弱 / `DAY` 充足 |

> 网关按**帧内容**区分是哪种终端：带 `T=` / `H=` 的是温湿度帧，带 `LUX=` 的是光照帧。
> 所以 `T1` 是温湿度还是光照，取决于哪块板先上电，上位机按内容判断、不依赖编号。

---

## 四、上报数据帧（10 字节，小端）

两种终端共用同一个 10 字节帧，靠第 0 字节 `nodeType` 区分载荷含义：

| 偏移 | 温湿度帧（nodeType = 1） | 光敏帧（nodeType = 2） |
| --- | --- | --- |
| 0 | nodeType = 1 | nodeType = 2 |
| 1-2 | 终端短地址 | 终端短地址 |
| 3-4 | 上报次数 | 上报次数 |
| 5-6 | 温度（0.1℃，int16） | 光照强度（lx，uint16） |
| 7 | 湿度低字节 | 光敏原始采样值 0~255 |
| 8 | 湿度高字节 | 光照等级 0 夜 / 1 微亮 / 2 白天 |
| 9 | 保留 | 保留 |

链路参数：端点 20、Profile 0x0F08、上报簇号 1，终端向 0x0000 单播。

---

## 五、大棚环境判定逻辑

```
读取光照 lux
   ↓
level = lux < 80 ? NIGHT（不足，需要补光）
      : lux < 250 ? DUSK（偏弱）
      : DAY（充足）
   ↓
组帧单播给网关（lux / adc / level）
```

阈值在 `NewApp.h` 中：

- `NEWAPP_LUX_NIGHT`（默认 80 lx）：低于此值判为**光照不足**
- `NEWAPP_LUX_DUSK`（默认 250 lx）：低于此值判为**光照偏弱**

温湿度终端只做采集与上报，判定阈值放在上位机里，便于随时调整：

| 指标 | 偏低 / 偏干 | 适宜 | 偏高 / 偏湿 |
| --- | --- | --- | --- |
| 空气温度 | < 15 ℃ | 15 ~ 30 ℃ | > 30 ℃（> 32 ℃ 转红报警） |
| 空气湿度 | < 45 %RH | 45 ~ 85 %RH | > 85 %RH |
| 光照强度 | < 80 lx 不足 | ≥ 250 lx 充足 | 80 ~ 250 lx 偏弱 |

上报周期在 `NewApp.h` 中：`NEWAPP_REPORT_PERIOD`（默认 5000 ms）。

---

## 六、按键与 LED

板载按键只有 `SW6`（P0.1，板载 S1）与摇杆（P2.0），摇杆方向通过 ADC 识别为上 / 下 / 左 / 右 / 中五个键值。

| 操作 | 温湿度终端 / 光敏终端 | 协调器网关 |
| --- | --- | --- |
| 摇杆上 | LED1 闪 2 下 | LED1 闪 2 下 |
| 摇杆下 | LED2 闪 2 下 | LED2 闪 2 下 |
| 摇杆左 | LED3 闪 2 下 | LED3 闪 2 下 |
| 摇杆右 | LED1 快速闪 4 下 | LED1 快速闪 4 下 |
| 摇杆中键 | 熄灭所有 LED | 熄灭所有 LED |
| 板载 S1（SW6） | 立即采集并上报一次，不必等 5s 周期 | 本地提示，不发起无线通信 |

LED 约定：

| 灯 | 终端 | 网关 |
| --- | --- | --- |
| LED1 | 入网成功 / 发送成功 | 收到**温湿度**帧 |
| LED2 | 入网成功 | 收到**光照**帧 |
| LED3 | 未入网或发送失败 | **光照不足时常亮**，光照恢复后熄灭 |

---

## 七、传感器

### 7.1 默认行为：模拟数据（免硬件演示）

两个传感器的模拟开关默认都打开，不接任何传感器即可完成完整演示：

| 宏（在 `sensor.h` 中） | 默认值 | 说明 |
| --- | --- | --- |
| `SENSOR_SIMULATE` | 1 | 温湿度：模拟值在 **24.0~30.0℃、45.0~65.0%RH** 之间缓慢漂移 |
| `SENSOR_LIGHT_SIMULATE` | 1 | 光照：**60 秒走完一个昼夜**的曲线 |

光照模拟曲线：

```
0 ~ 8s    夜晚    8 ~ 20 lx        → LEVEL=NIGHT，光照不足
8 ~ 14s   黎明    20 ~ 1000 lx     → 快速变亮
14 ~ 40s  白天    1200 ~ 1800 lx   → LEVEL=DAY，光照充足
40 ~ 46s  黄昏    1800 ~ 60 lx     → 快速变暗
46 ~ 60s  夜晚    60 ~ 15 lx       → LEVEL=NIGHT
```

一分钟左右就能看到大棚从"夜间光照不足"到"白天光照充足"再回到夜间的完整循环。

### 7.2 接真实传感器

1. 把 `sensor.h` 里对应的模拟开关改成 0：温湿度改 `SENSOR_SIMULATE`，光照改 `SENSOR_LIGHT_SIMULATE`；
2. 按实际接线修改引脚宏（默认值如下）：

   | 传感器 | 方式 | 宏 | 默认引脚 |
   | --- | --- | --- | --- |
   | DHT11 温湿度 | 单总线 | `SENSOR_DHT11_BIT` | **P0.7** |
   | 光敏（推荐） | 模拟量 AO | `SENSOR_LIGHT_ADC_CHN` | P0.5 = AIN5 |
   | 光敏 | 开关量 DO | `SENSOR_LIGHT_DO_BIT` | P0.4 |

3. 光敏用 AO 时 `SENSOR_LIGHT_USE_ADC` 为 1，用 DO 时改为 0；
4. 若发现数值方向反了（越亮数字越小），把 `SENSOR_LIGHT_ADC_RISING` 改为 0；
5. 若 DO 模块是低电平表示有光，把 `SENSOR_LIGHT_DO_ACTIVE_HIGH` 改为 0；
6. `SENSOR_LIGHT_LUX_MAX`（默认 2000）是 ADC 满量程对应的 lx，可按实测校准；
7. DHT11 的读取时序对延时敏感，`Sensor_DelayTicks()` 的循环次数需要按实测校准。

> **引脚冲突提醒**：摇杆方向识别占用了 **P0.6 / AIN6**，所以 DHT11 默认改到了 **P0.7**。
> 如果模块已经接在 P0.6 上，把 `SENSOR_DHT11_BIT` 从 `BV(7)` 改回 `BV(6)` 即可。

---

## 八、组网参数

- 信道与 PANID 在 `Projects\zstack\Tools\CC2530DB\f8wConfig.cfg` 中：`DEFAULT_CHANLIST`（默认信道 11）、
  `ZDAPP_CONFIG_PAN_ID`（默认 0xFFFF 自动选择）。
- 多人同时实验时，请给每个小组设置独立的 PANID，网关与终端必须一致。

---

## 九、编译与下载

1. 用 IAR 打开 `CC2530DB\ZigBee_NewApp.eww`；
2. 左侧 Workspace 下拉框选择配置（CoordinatorEB / SensorTerminalEB / LightTerminalEB）；
3. `Project → Rebuild All`，确认 0 error；
4. 用 CC Debugger 连接节点，`Download and Debug`（或直接 Download）烧写；
5. **先给协调器上电建网**，再给两个终端上电入网；
6. 协调器串口接电脑，串口助手 9600-8-N-1，即可看到自动滚动输出的环境数据。

---

## 十、上位机

```bash
pip install pyserial
python 大棚环境监测.py
python 大棚环境监测.py COM3      # 直接指定串口
```

界面结构：

- **左侧**：大棚剖面场景，实时反映环境
  - 天空与棚内亮度 ← 光照等级（白天 / 傍晚 / 夜晚，带太阳、月亮、星空）
  - 叶片颜色 ← 温度（偏冷发青、适宜翠绿、偏热发黄）
  - 棚膜上的露珠数量 ← 湿度（越潮越多）
- **右侧三张卡片**：棚内温度、棚内湿度、光照强度，各自带上下限判定与颜色提示
- **底部**：两个节点的编号与短地址、累计上报帧数、最后更新时间、网关就绪状态

界面不显示通信日志，也不带模拟演示模式，只呈现监控结果。

预览图见 `上位机\预览图\`。

---

## 十一、验收要点

| 编号 | 测试项 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| T1 | 协调器建网 | 仅协调器上电，串口接电脑 | 输出 `gateway started`、`network formed, gateway ready` |
| T2 | 温湿度终端自检 | 只给温湿度终端上电（可不建网） | 输出 `temp/hum sensor ready, T=...C H=...%RH` |
| T3 | 光敏终端自检 | 只给光敏终端上电（可不建网） | 输出 `light sensor ready, LUX=... ADC=... LEVEL=...` |
| T4 | 终端入网 | 两个终端上电 | 各自输出 `joined network`，LED2 闪 3 下 |
| T5 | 自动上报 | 入网后不做任何操作 | 网关每 5s 收到两帧，分别带 `T= H=` 与 `LUX= LEVEL=` |
| T6 | 温湿度变化 | 观察 1 分钟以上 | `T=` 在 24.0~30.0℃ 漂移，`H=` 在 45~65%RH 漂移 |
| T7 | 光照昼夜变化 | 观察 60s 以上 | `LEVEL` 依次出现 `NIGHT → DAY → NIGHT`，`LUX` 同步变化 |
| T8 | 光照不足提示 | 等到 `LEVEL=NIGHT` | 网关 LED3 常亮；光照恢复后 LED3 熄灭 |
| T9 | 按键立即上报 | 按终端板载 S1 | 不等周期，立即多输出一帧，`cnt` 加 1 |
| T10 | 未入网保护 | 只给终端上电，不建网 | LED3 闪 3 下，输出 `not joined, report dropped` |
| T11 | 本地按键隔离 | 按摇杆方向键 | 仅本地 LED 变化，网关无输出 |
| T12 | 串口稳定性 | 连续运行 1 分钟以上 | 计数连续递增，无乱码、无丢帧 |
| T13 | 上位机显示 | 打开上位机并连接协调器串口 | 三张卡片与场景随数据实时刷新，无通信日志、无演示模式 |

---

## 十二、与上一版（实验居家 · 室内环境监测）的差异

| 对比项 | 室内环境监测版 | 大棚环境监测版 |
| --- | --- | --- |
| 应用场景 | 室内环境警告（火警） | 温室大棚环境监测（补光 / 通风 / 灌溉提示） |
| 网络规模 | 1 协调器 + 温湿度终端 + 火焰终端 | 1 协调器 + 温湿度终端 + **光敏终端** |
| 传感器 | DHT11 温湿度 + 火焰开关量 | DHT11 温湿度 + **光敏（AO 模拟量 / DO 开关量）** |
| 上报触发 | 温湿度周期 + 火焰状态变化 | **两个终端都按 5s 周期自动上报** + 按键 |
| 数据帧 | temp / hum / flame（火焰帧只占 1 字节） | temp / hum 与 **lux / adc / level**，两种载荷复用同一 10 字节帧 |
| 上电自检 | 无 | **有**，终端上电先打印一次传感器读数 |
| 网关 LED | LED3 火警闪烁 | LED3 **光照不足常亮** |
| 编译配置 | Coordinator / Router / SensorTerminal / **FlameTerminal** | Coordinator / Router / SensorTerminal / **LightTerminal** |
| 上位机 | 智能家居室内环境警告系统 | **大棚环境监测系统**（温度 / 湿度 / 光照三卡 + 大棚场景） |
