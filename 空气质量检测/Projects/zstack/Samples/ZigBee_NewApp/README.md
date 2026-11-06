# ZigBee_NewApp — 基于 ZigBee 的空气质量检测系统

CC2530F256 + Z-Stack 2.5.1a（IAR EW8051）实现的两节点星型网络：

- **协调器网关**：上电自动建网，接收烟雾终端上报的浓度数据，解析后经串口打印，
  并按等级驱动 LED 报警；按 KEY1 可反向请求终端立即上报一次。
- **烟雾终端**：上电入网后**按 5s 周期自动采集并上报**（无需按键）；
  按 KEY1 可随时手动补报一帧；浓度进入「超标 / 严重」档时本地点亮 / 闪烁报警灯。

与前面两个项目（室内环境监测、大棚环境监测）的区别在于：
**上行是自动的，下行也有一条手动通道**（网关请求 → 终端补报），
上位机能看出每一帧是「周期上报」「本机手动」还是「网关请求」。

---

## 一、工程位置

```
D:\Work\A\空气检测\
├─ Components\                  Z-Stack 2.5.1a 协议栈
├─ Projects\zstack\Samples\ZigBee_NewApp\
│  ├─ Source\
│  │  ├─ NewApp.c / NewApp.h     应用层主逻辑
│  │  ├─ sensor.c / sensor.h     烟雾传感器（MQ 系列）驱动
│  │  └─ OSAL_NewApp.c           OSAL 任务表
│  ├─ CC2530DB\                  IAR 工程（.ewp / .ewd / .eww）
│  └─ README.md
├─ msvcr71.dll
└─ 上位机\
   ├─ 空气质量监测.py      Python + tkinter 上位机
   ├─ requirements.txt
   ├─ assets\bg.jpg       写实背景底图
   └─ 预览图\             界面效果图
```

---

## 二、三个编译配置

| 配置 | 角色宏 | 烧录到 | 输出 hex |
| --- | --- | --- | --- |
| `CoordinatorEB` | `NEWAPP_ROLE_COORDINATOR` | 协调器（接电脑） | `ZigBee_NewApp-Coordinator.hex` |
| `SmokeTerminalEB` | `NEWAPP_ROLE_SMOKE_TERM` | 烟雾终端 | `ZigBee_NewApp-Smoke.hex` |
| `RouterEB` | `NEWAPP_ROLE_SMOKE_TERM` | 可选中继（同一条终端代码） | `ZigBee_NewApp-Router.hex` |

角色宏位于 `NewApp.h` 末尾的三段 `#if defined(...)`，编译配置里的
`CCDefines` 只需给一个角色宏，其余由头文件自动派生
（`NEWAPP_IS_TERMINAL`、`NEWAPP_NODE_TYPE`、节点名字等）。

---

## 三、串口输出（9600-8-N-1）

### 3.1 协调器上电

```
[APP] ZigBee_NewApp gateway started
[APP] network formed, gateway ready
```

### 3.2 烟雾终端上电（串口接在终端上时可见）

```
[APP] ZigBee_NewApp smoke-terminal started
[APP] smoke sensor ready, SMOKE=132 ADC=34 LEVEL=NORMAL
[APP] joined network
[APP] SMOKE=138 ADC=35 LEVEL=NORMAL cnt=1 src=PERIODIC tx=OK
[APP] SMOKE=145 ADC=37 LEVEL=NORMAL cnt=2 src=PERIODIC tx=OK
```

上电自检那一行**不需要建网**就会打印，接上传感器单独上电即可确认模块工作正常。

### 3.3 网关持续打印（终端入网后自动上报，每 5s 一帧）

```
[GW] S1 addr=0x796F cnt=1 SMOKE=138 ADC=35 LEVEL=NORMAL SRC=PERIODIC
[GW] S1 addr=0x796F cnt=2 SMOKE=145 ADC=37 LEVEL=NORMAL SRC=PERIODIC
[GW] S1 addr=0x796F cnt=3 SMOKE=512 ADC=131 LEVEL=WARN SRC=PERIODIC
[GW] S1 addr=0x796F cnt=4 SMOKE=768 ADC=196 LEVEL=DANGER SRC=PERIODIC
```

| 字段 | 说明 |
| --- | --- |
| `S1` | 网关按终端短地址首次出现顺序自动分配的编号 |
| `addr` | 终端 16 位短地址 |
| `cnt` | 该终端累计上报次数（从 1 开始递增，可用于验证丢包） |
| `SMOKE` | 烟雾浓度，0 ~ 1000（等效 ppm） |
| `ADC` | 传感器 8 位原始采样值，用于判断模块是否工作正常 |
| `LEVEL` | `CLEAN` 清新 / `NORMAL` 轻度异味 / `WARN` 明显烟雾 / `DANGER` 严重 |
| `SRC` | `PERIODIC` 周期上报 / `KEY` 本机按键 / `REMOTE` 网关请求 |

### 3.4 两条手动上报通道

**通道一：终端上按 KEY1**

```
[APP] KEY1 manual report
[APP] SMOKE=760 ADC=195 LEVEL=DANGER cnt=9 src=KEY tx=OK
```

网关立刻多打印一行 `SRC=KEY`。周期节拍不受影响，手动帧是**插入**而不是重置。

**通道二：网关按 KEY1 远程请求**

```
[GW] -> S1 addr=0x796F cmd=REPORT_NOW sent
[GW] KEY1 report request -> 1 terminal(s)
[GW] S1 addr=0x796F cnt=10 SMOKE=742 ADC=189 LEVEL=DANGER SRC=REMOTE
```

网关遍历地址表，向**所有**已入网的终端下发控制命令；终端收到后立即补报一帧并回传，
`SRC` 标记为 `REMOTE`。网关若还没收到过任何终端，会打印
`[GW] ctrl dropped, no terminal in table`。

---

## 四、数据帧与链路参数

上报帧（簇号 1，终端 → 0x0000 单播）固定 **10 字节**，多字节字段小端序：

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| 0 | nodeType | 固定 `1`（烟雾终端） |
| 1-2 | addr | 终端短地址 |
| 3-4 | count | 上报次数 |
| 5-6 | density | 烟雾浓度 0 ~ 1000 |
| 7 | adc | 原始采样值 0 ~ 255 |
| 8 | level | 0 清新 / 1 轻度 / 2 超标 / 3 严重 |
| 9 | src | 0 周期 / 1 本机按键 / 2 网关请求 |

控制帧（簇号 2，网关 → 终端）长度 **1 字节**：`0 = REPORT_NOW`（请求立即上报）。

链路参数：端点 20、Profile `0x0F08`、上报簇 1、控制簇 2。

---

## 五、空气质量判定

```
读 ADC → density = adc * 1000 / 255（可反相）
   ↓
level = density >= 700 ? DANGER
      : density >= 300 ? WARN
      : density >= 100 ? NORMAL
      : CLEAN
   ↓
LED1 报警：DANGER 无限闪，WARN 常亮，其余熄灭
   ↓
组帧单播给网关，并在本地串口回显
```

阈值在 `NewApp.h` 中：

| 宏 | 默认值 | 含义 |
| --- | --- | --- |
| `NEWAPP_SMOKE_NORMAL` | 100 | 高于此值：轻度异味 |
| `NEWAPP_SMOKE_WARN` | 300 | 高于此值：明显烟雾 |
| `NEWAPP_SMOKE_DANGER` | 700 | 高于此值：严重，声光报警 |

上报周期：`NEWAPP_REPORT_PERIOD`（默认 5000 ms）；入网后首帧延迟
`NEWAPP_FIRST_REPORT_DELAY`（500 ms）。

---

## 六、按键与 LED

板载独立按键是 `SW6`（P0.1，板载 S1，即 KEY1）；摇杆方向通过 ADC 识别为上/下/左/右/中。

| 操作 | 烟雾终端 | 协调器网关 |
| --- | --- | --- |
| **KEY1（S1）** | 立即采集并上报一帧（`SRC=KEY`） | 请求所有终端立即上报（终端侧 `SRC=REMOTE`） |
| 摇杆上 / 下 / 左 / 右 | LED3 闪 1 / 2 / 3 / 4 下 | 同左 |
| 摇杆中键 | 熄灭 LED2、LED3（不碰报警灯） | 同左 |

| 灯 | 终端 | 网关 |
| --- | --- | --- |
| LED1 | **报警灯**：DANGER 无限闪，WARN 常亮 | 跟随终端最近一次上报的等级 |
| LED2 | 入网成功 / 发送成功 | 收到上报帧 |
| LED3 | 读取失败或发送失败 | DANGER 时常亮（本地报警提示） |

---

## 七、传感器

### 7.1 默认：模拟数据（不接硬件也能跑通全流程）

`sensor.h` 中 `SENSOR_SMOKE_SIMULATE` 默认 **1**：按开机时间生成一条
**90 秒一轮**的烟雾曲线（清新 → 轻度异味 → 明显烟雾 → 重度 → 通风回落），
刚好覆盖上位机的四档配色与报警动画。

### 7.2 接真实模块

把 `SENSOR_SMOKE_SIMULATE` 改成 **0**，再按接线选择：

| 宏 | 默认 | 说明 |
| --- | --- | --- |
| `SENSOR_SMOKE_USE_ADC` | 1 | 1 = 用模拟量 AO 接 ADC；0 = 用开关量 DO 接普通 IO |
| `SENSOR_SMOKE_ADC_CHN` | `HAL_ADC_CHANNEL_5` | AO 接 **P0.5 / AIN5** |
| `SENSOR_SMOKE_DO_BIT` | `BV(4)` | DO 接 **P0.4** |
| `SENSOR_SMOKE_ADC_RISING` | 1 | 0 表示 ADC 越大浓度越低（反相） |
| `SENSOR_SMOKE_DO_ACTIVE_HIGH` | 1 | 0 表示 DO 低电平报警 |

> **注意引脚冲突**：`hal_key.c` 的摇杆方向识别占用 `HAL_ADC_CHANNEL_6`（P0.6/AIN6），
> 烟雾传感器默认放在 P0.5，不要接到 P0.6。

---

## 八、组网参数

| 参数 | 值 | 位置 |
| --- | --- | --- |
| 信道 | 0x0B（11） | `Projects\zstack\Tools\CC2530DB\f8wConfig.cfg` |
| PAN ID | 0xFFFF（自动选择） | 同上 |
| 协调器短地址 | 0x0000 | 固定 |
| 终端地址分配 | 由协调器动态分配 | — |
| 串口波特率 | 9600 8-N-1 | `Components\mt\MT_UART.h` |

---

## 九、编译与下载

1. IAR 打开 `CC2530DB\ZigBee_NewApp.eww`
2. 选择配置：协调器用 `CoordinatorEB`，烟雾终端用 `SmokeTerminalEB`
3. `Rebuild All` → 下载
4. **先给协调器上电建网**，等串口打印 `network formed, gateway ready`，再给终端上电入网
5. 上位机串口接在**协调器**上

---

## 十、上位机

```bash
pip install pyserial pillow
python 空气质量监测.py                 # 自动扫描串口
python 空气质量监测.py COM3            # 直接指定串口
python 空气质量监测.py --demo          # 演示模式，不需要串口和硬件
```

界面形态：**写实照片底图 + 半透明玻璃面板**（PIL 后台合成），
顶部居中铭牌标题（衬线字体 + 宽字距），左侧大号数码读数 + 32 段 LED 光柱，
右上是扫描式浓度波形，右下是链路与设备信息表。

- 不显示通信日志，只呈现监控结果
- 浓度进入「严重」档时，整窗描边与页眉报警条红色呼吸脉冲
- **演示模式**（右上角「演示」按钮）用与真实协调器**逐字一致**的串口行喂给同一套解析器，
  所以演示和实机走的是同一条渲染路径
- 上报方式会显示为「周期自动 / 手动 · 按键 / 手动 · 远程」

预览图见 `上位机\预览图\`。

---

## 十一、验收要点

| 编号 | 测试项 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| T1 | 协调器建网 | 仅协调器上电，串口接电脑 | 输出 `gateway started`、`network formed, gateway ready` |
| T2 | 终端入网 | 烟雾终端上电 | 输出 `smoke sensor ready`、`joined network`，LED2 闪 |
| T3 | 周期上报 | 终端入网后不做任何操作 | 网关每 5s 输出一行 `[GW] S1 ... SRC=PERIODIC` |
| T4 | 终端手动上报 | 按终端 KEY1 | 终端打印 `KEY1 manual report`，网关多一行 `SRC=KEY` |
| T5 | 网关手动请求 | 按网关 KEY1 | 打印 `cmd=REPORT_NOW sent` 与 `report request -> 1`，随后出现 `SRC=REMOTE` |
| T6 | 超标报警 | 让浓度进入 300~700 | 终端 LED1 常亮，`LEVEL=WARN` |
| T7 | 严重报警 | 让浓度 ≥ 700 | 终端 LED1 无限闪烁，网关 LED1 闪、LED3 常亮，`LEVEL=DANGER` |
| T8 | 未入网保护 | 只给终端上电，不建网 | 只能看到自检行，无 `tx=OK`；LED3 闪 |
| T9 | 按键隔离 | 按摇杆方向键 | 仅 LED3 闪，网关无输出 |
| T10 | 上位机实机 | 串口接协调器并连接 | 读数、光柱、波形随上报刷新，「上报方式」随之变化 |
| T11 | 上位机演示 | 点「演示」或 `--demo` | 无串口也能看到完整运行过程 |
| T12 | 串口稳定性 | 连续运行 1 分钟以上 | `cnt` 连续递增，无乱码、无丢帧 |

---

## 十二、与前面几个项目的差异

| 项目 | 网络 | 上行 | 下行 | 数据帧 |
| --- | --- | --- | --- | --- |
| 实验居家 · 室内环境监测 | 协调器 + 温湿度 + 火焰 | 温湿度周期 / 火焰事件 | 无 | temp / hum / flame |
| 实验 灯光 · 路灯控制 | 协调器 + 光敏 | 光敏周期 | 控制命令（自动 / 强制开 / 强制关） | lux / lamp / level |
| 新实验 · 大棚环境监测 | 协调器 + 温湿度 + 光敏 | 双终端周期 | 无 | temp / hum / lux |
| **空气检测 · 空气质量** | 协调器 + 烟雾 | **周期 + 双通道手动** | 请求上报（`REPORT_NOW`） | smoke / adc / level / src |
