# ZigBee_NewApp

基于 Z-Stack 2.5.1a 与 CC2530 的室内环境监测项目。网络形态为 **1 个协调器网关 + 2 个终端**：
终端一负责温湿度采集，终端二负责火焰检测与火警自动上报，网关负责建网、接收并串口输出到上位机。

## 一、工程位置

```
Projects\zstack\Samples\ZigBee_NewApp\
├─ CC2530DB\
│  ├─ ZigBee_NewApp.eww       IAR 工作区（打开这个）
│  ├─ ZigBee_NewApp.ewp       工程文件，含 4 个编译配置
│  ├─ ZigBee_NewApp.ewd       调试器配置
│  └─ Source\NewAppHw.c/.h    Demo 配置的跳线判定
├─ Source\
│  ├─ NewApp.c / NewApp.h     应用层主逻辑
│  ├─ sensor.c / sensor.h     温湿度 / 火焰传感器驱动
│  └─ OSAL_NewApp.c           OSAL 任务表
└─ README.md
```

## 二、四个编译配置

| 配置 | 角色宏 | 烧录到 | 输出 hex |
| --- | --- | --- | --- |
| CoordinatorEB | NEWAPP_ROLE_COORDINATOR | 协调器网关 | ZigBee_NewApp-Coordinator.hex |
| RouterEB | NEWAPP_ROLE_SENSOR_TERM | 可选路由中继 | ZigBee_NewApp-Router.hex |
| SensorTerminalEB | NEWAPP_ROLE_SENSOR_TERM | 温湿度终端 | ZigBee_NewApp-SensorTerminal.hex |
| FlameTerminalEB | NEWAPP_ROLE_FLAME_TERM | 火焰终端 | ZigBee_NewApp-FlameTerminal.hex |

hex 输出在各自的 `Exe` 目录，例如 `CC2530DB\CoordinatorEB\Exe\`。

## 三、按键与 LED

板载按键只有 `SW6`（P0.1，板载 S1）与摇杆（P2.0）；摇杆方向通过 ADC 识别为上/下/左/右/中五个键值。

| 操作 | 终端 | 网关 |
| --- | --- | --- |
| 摇杆上 / 下 / 左 / 右 | 本地 LED 闪烁，不发无线 | 本地 LED 闪烁 |
| 摇杆中键 | 熄灭所有 LED | 熄灭所有 LED |
| 板载 S1（SW6） | 读取传感器并单播上报一次 | 本地闪烁，不发无线 |
| 上电入网后 | 温湿度终端每 5s 自动上报一次 | — |

周期上报开关：`NewApp.h` 里的 `NEWAPP_SENSOR_REPORT_PERIOD`（默认 5000 ms，设为 0 则只由按键触发）。

LED 约定：LED1 = 无线发送/温湿度上报，LED2 = 入网成功/火焰安全上报，LED3 = 未入网或发送失败/火警报警。

## 四、上报数据帧（10 字节，小端）

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| 0 | nodeType | 1 = 温湿度终端，2 = 火焰终端 |
| 1-2 | addr | 终端短地址 |
| 3-4 | count | 上报次数 |
| 5-6 | temp | 温度，单位 0.1℃（有符号） |
| 7-8 | hum | 湿度，单位 0.1%RH |
| 9 | flame | 1 检测到明火，0 安全 |

簇号 `NEWAPP_REPORT_CLUSTERID = 1`，端点 20，Profile 0x0F08，终端向 0x0000 单播。

## 五、串口输出

协调器串口 **9600-8-N-1**，收到上报后打印，例如：

```
[APP] ZigBee_NewApp gateway started
[APP] network formed, gateway ready
[GW] T1 addr=0x796F cnt=3 T=25.6C H=60.5%RH
[GW] T2 addr=0x1234 cnt=1 FLAME=FIRE
[GW] T2 addr=0x1234 cnt=2 FLAME=SAFE
```

`T1/T2` 由网关按终端短地址首次出现顺序自动编号。

## 六、传感器

`sensor.h` 里 `SENSOR_SIMULATE` 默认为 `1`：温湿度输出缓慢变化的模拟值，火焰每 30s 出现 5s 的“有火”窗口，
不接传感器即可完成组网、上报、串口显示的完整演示。

接真实硬件时：

1. 把 `SENSOR_SIMULATE` 改为 `0`；
2. 按实际接线修改 `SENSOR_DHT11_*`（默认 P0.6）与 `SENSOR_FLAME_*`（默认 P0.5）引脚宏；
3. 火焰模块若高电平表示检测到火焰，把 `SENSOR_FLAME_ACTIVE_HIGH` 改为 `1`（默认低电平表示有火）；
4. DHT11 位时序需在 32MHz 下按实测校准 `Sensor_DelayTicks()` 的循环次数。

## 七、组网参数

- 信道与 PANID 在 `Projects\zstack\Tools\CC2530DB\f8wConfig.cfg` 中：`DEFAULT_CHANLIST`（默认信道 11）、
  `ZDAPP_CONFIG_PAN_ID`（默认 0xFFFF 自动选择）。
- 多人同时实验时，请给每个小组设置独立的 PANID，三个节点必须一致。

## 八、编译与下载

1. 用 IAR 打开 `CC2530DB\ZigBee_NewApp.eww`；
2. 左侧 Workspace 下拉框选择配置（CoordinatorEB / SensorTerminalEB / FlameTerminalEB）；
3. `Project → Rebuild All`，确认 0 error；
4. 用 CC Debugger 连接节点，`Download and Debug`（或直接 Download）烧写；
5. 先给协调器上电建网，再给两个终端上电入网；
6. 协调器串口接电脑，串口助手 9600-8-N-1 观察上报数据。
