# ZigBee_NewApp — 基于 ZigBee 的室内温湿度监测系统

基于 Z-Stack 2.5.1a 与 CC2530F256 的室内温湿度监测项目。网络形态为 **1 个协调器网关 + 1 个温湿度终端**（星型）：

- **协调器网关**：上电自动建网，接收终端上报的温湿度帧，解析后经串口打印，
  并按是否落在舒适区间点亮 LED2（舒适）/ LED3（越界）。
- **温湿度终端**：上电入网后先自检一次传感器并把读数打到串口，之后**每 5 s 自动采集并上报**；
  按板载 S1 可随时补报一帧。

> 本工程是合集里最精简的一版：只保留温湿度一路采集，用来把「组网 → 周期上报 → 串口解析 → 上位机呈现」
> 这条链路做完整。协议栈、OSAL 调度、按键 / LED / 串口驱动与其他几个项目完全一致，
> 差别只在应用层与传感器驱动层。

---

## 一、工程位置

```
Projects\zstack\Samples\ZigBee_NewApp\
├─ CC2530DB\
│  ├─ ZigBee_NewApp.eww       IAR 工作区（打开这个）
│  ├─ ZigBee_NewApp.ewp       工程文件
│  ├─ ZigBee_NewApp.ewd       调试器配置
│  └─ Source\NewAppHw.c/.h    Demo 跳线判定（预留，未参与业务逻辑）
├─ Source\
│  ├─ NewApp.c / NewApp.h     应用层主逻辑
│  ├─ sensor.c / sensor.h     温湿度（DHT11）驱动 + 模拟数据
│  └─ OSAL_NewApp.c           OSAL 任务表
└─ README.md
```

上位机在工程目录外：

```
上位机\
├─ 室内温湿度监测.py     Python + tkinter 图形界面
├─ requirements.txt
└─ 预览图\              两张界面效果图
```

---

## 二、三个编译配置

| 配置 | 角色宏 | 烧录到 | 输出 hex |
| --- | --- | --- | --- |
| CoordinatorEB | NEWAPP_ROLE_COORDINATOR | 协调器网关 | ZigBee_NewApp-Coordinator.hex |
| SensorTerminalEB | NEWAPP_ROLE_SENSOR_TERM | 温湿度终端 | ZigBee_NewApp-SensorTerminal.hex |
| RouterEB | NEWAPP_ROLE_SENSOR_TERM | 可选路由中继（一般不用） | ZigBee_NewApp-Router.hex |

hex 输出在各自的 `Exe` 目录，例如 `CC2530DB\CoordinatorEB\Exe\`。

> **最小可运行组合**：协调器烧 `CoordinatorEB`，终端烧 `SensorTerminalEB`，两块钱就够。

---

## 三、串口输出（9600-8-N-1）

### 3.1 协调器网关上电

```
[APP] ZigBee_NewApp gateway started
[APP] ZigBee Coord Network ID: 918D
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

### 3.3 网关持续打印（终端入网后每 5 s 一帧）

```
[GW] T1 addr=0xAF10 cnt=1 T=25.6C H=60.5%RH ENV=OK
[GW] T1 addr=0xAF10 cnt=2 T=25.8C H=59.9%RH ENV=OK
[GW] T1 addr=0xAF10 cnt=3 T=31.2C H=41.6%RH ENV=ALARM
```

| 字段 | 说明 |
| --- | --- |
| `T1` | 节点编号，网关按终端短地址首次出现顺序分配 |
| `addr` | 终端 16 位短地址 |
| `cnt` | 该终端累计上报次数（从 1 递增，可用于验证丢包） |
| `T` | 空气温度，单位 ℃ |
| `H` | 空气湿度，单位 %RH |
| `ENV` | 网关侧舒适度判定：`OK` 舒适 / `ALARM` 越界 |

---

## 四、上报数据帧（10 字节，小端）

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| 0 | nodeType | 1 = 温湿度终端 |
| 1-2 | addr | 终端短地址 |
| 3-4 | count | 上报次数 |
| 5-6 | temp | 温度，单位 0.1℃，有符号 int16 |
| 7-8 | hum | 湿度，单位 0.1%RH，uint16 |
| 9 | reserved | 保留 |

链路参数：端点 20、Profile 0x0F08、上报簇号 1，终端向 0x0000 单播。
上报周期 `NEWAPP_REPORT_PERIOD`（默认 5000 ms），入网后首帧延迟 `NEWAPP_FIRST_REPORT_DELAY`（500 ms）。

---

## 五、判定与 LED

阈值在 `NewApp.h` 中：

```c
#define NEWAPP_TEMP_LOW    150      /* 15.0℃：低于此值偏冷 */
#define NEWAPP_TEMP_HIGH   300      /* 30.0℃：高于此值偏热 */
#define NEWAPP_HUM_LOW     450      /* 45.0%RH：低于此值偏干 */
#define NEWAPP_HUM_HIGH    850      /* 85.0%RH：高于此值偏湿 */
```

任一指标越界即 `ENV=ALARM`。

| 灯 | 终端 | 网关 |
| --- | --- | --- |
| LED1 | 建网 / 入网成功时闪 3 下；发送成功闪 1 下 | 收到一帧闪 1 下 |
| LED2 | 入网成功闪 3 下；按键补报闪 1 下 | **环境舒适时常亮** |
| LED3 | **未入网或发送失败**时闪（3 下 / 2 下） | **任一指标越界时常亮** |

---

## 六、按键

板载按键只有 `SW6`（P0.1，板载 S1）与摇杆（P2.0），摇杆方向通过 ADC 识别为上 / 下 / 左 / 右 / 中五个键值。

| 操作 | 温湿度终端 | 协调器网关 |
| --- | --- | --- |
| 摇杆上 / 下 / 左 | LED1 / LED2 / LED3 闪 2 下 | 同左 |
| 摇杆右 | LED1 快速闪 4 下 | 同左 |
| 摇杆中键 | 熄灭所有 LED | 同左 |
| 板载 S1（SW6） | **立即采集并上报一次**，不必等 5 s 周期 | 本地提示，不发起无线通信 |

---

## 七、传感器

### 7.1 默认行为：模拟数据（免硬件演示）

`sensor.h` 中 `SENSOR_SIMULATE` 默认为 `1`，不接任何传感器即可完成完整演示：

| 项目 | 模拟曲线 |
| --- | --- |
| 温度 | **22.0 ~ 31.0 ℃** 三角波，周期 120 s |
| 湿度 | **68.0 ~ 42.0 %RH**，与温度反相 |
| 抖动 | 每次读数叠加 0~2（温度）/ 0~4（湿度）的小抖动 |

一轮两分钟就能看到「适宜 → 偏热偏干 → 回落」的完整循环。

### 7.2 接真实传感器

1. 把 `sensor.h` 里 `SENSOR_SIMULATE` 改成 `0`；
2. 按实际接线修改引脚宏：

   | 传感器 | 方式 | 宏 | 默认引脚 |
   | --- | --- | --- | --- |
   | DHT11 温湿度 | 单总线 | `SENSOR_DHT11_BIT` | **P0.7** |

3. DHT11 的读取时序对延时敏感，`Sensor_DelayTicks()` 的循环次数需要按实测校准。

> **引脚冲突提醒**：摇杆方向识别占用了 **P0.6 / AIN6**，所以 DHT11 默认放在 **P0.7**。
> 如果模块已经接在 P0.6 上，把 `SENSOR_DHT11_BIT` 从 `BV(7)` 改回 `BV(6)` 即可。

---

## 八、组网参数

- 信道与 PANID 在 `Projects\zstack\Tools\CC2530DB\f8wConfig.cfg` 中：
  `DEFAULT_CHANLIST`（默认信道 11）、`ZDAPP_CONFIG_PAN_ID`（默认 0xFFFF 自动选择）。
- 多人同时实验时，请给每个小组设置独立的 PANID，网关与终端必须一致。

---

## 九、编译与下载

1. 用 IAR 打开 `CC2530DB\ZigBee_NewApp.eww`；
2. 左侧 Workspace 下拉框选择配置（CoordinatorEB / SensorTerminalEB）；
3. `Project → Rebuild All`，确认 0 error；
4. 用 CC Debugger 连接节点，`Download and Debug`（或直接 Download）烧写；
5. **先给协调器上电建网**，再给终端上电入网；
6. 协调器串口接电脑，串口助手 9600-8-N-1，即可看到每 5 s 一行的温湿度。

---

## 十、验收要点

| 编号 | 测试项 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| T1 | 协调器建网 | 仅协调器上电，串口接电脑 | 输出 `gateway started`、`network formed, gateway ready` |
| T2 | 终端自检 | 只给终端上电（可不建网） | 输出 `temp/hum sensor ready, T=...C H=...%RH` |
| T3 | 终端入网 | 终端上电 | 输出 `joined network`，LED2 闪 3 下 |
| T4 | 自动上报 | 入网后不做任何操作 | 网关每 5 s 收到一帧，`cnt` 连续递增 |
| T5 | 温湿度变化 | 观察 2 分钟 | `T` 在 22.0~31.0℃ 升降，`H` 在 68.0~42.0%RH 反相变化 |
| T6 | 舒适度 LED | 观察 `ENV` 与网关 LED | `ENV=OK` 时 LED2 常亮；`ENV=ALARM` 时 LED3 常亮 |
| T7 | 按键补报 | 按终端板载 S1 | 不等周期，立即多输出一帧，`cnt` 加 1 |
| T8 | 未入网保护 | 只给终端上电，不建网 | LED3 闪 3 下，输出 `not joined, report dropped` |
| T9 | 本地按键隔离 | 按摇杆方向键 | 仅本地 LED 变化，网关无输出 |
| T10 | 上位机显示 | 打开上位机并连接协调器串口 | 两块仪表与趋势曲线实时刷新 |
