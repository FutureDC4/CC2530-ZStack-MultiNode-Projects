# ZigBee_NewApp — 基于 ZigBee 的路灯控制系统

基于 Z-Stack 2.5.1a 与 CC2530 的路灯控制项目。网络形态为 **1 个协调器网关 + 光敏路灯终端**（星型，可扩展到 2 个终端）：

- **协调器网关**：上电自动建网，接收终端上报的光照数据，解析后经串口打印到上位机，并用 LED1 镜像路灯状态；按 **KEY1（板载 S1）** 可远程手动开 / 关路灯。
- **光敏路灯终端**：上电入网后**自动按周期采集光照并上报**（无需按键），光照低于阈值时本地点亮路灯（LED1），高于阈值时熄灭；按 **KEY1** 可手动开 / 关本机路灯，节点上的 LED1 立即跟随，并把状态回传给网关。

> 本工程由「实验 3 基于 Z-Stack 的按键实验」的二次开发版本（室内环境监测）改造而来，复用了原有的协议栈、OSAL 调度框架、按键 / LED / 串口驱动，仅重写了应用层与传感器驱动层。

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
│  ├─ sensor.c / sensor.h     光敏传感器驱动
│  └─ OSAL_NewApp.c           OSAL 任务表
└─ README.md
```

---

## 二、四个编译配置

| 配置 | 角色宏 | 烧录到 | 输出 hex |
| --- | --- | --- | --- |
| CoordinatorEB | NEWAPP_ROLE_COORDINATOR | 协调器网关 | ZigBee_NewApp-Coordinator.hex |
| LightTerminalEB | NEWAPP_ROLE_LIGHT_TERM | 光敏路灯终端（主用） | ZigBee_NewApp-LightTerminal.hex |
| Light2TerminalEB | NEWAPP_ROLE_LIGHT_TERM | 第二路段路灯终端（可选） | ZigBee_NewApp-Light2Terminal.hex |
| RouterEB | NEWAPP_ROLE_LIGHT_TERM | 可选路由中继 | ZigBee_NewApp-Router.hex |

hex 输出在各自的 `Exe` 目录，例如 `CC2530DB\CoordinatorEB\Exe\`。

> LightTerminalEB 与 Light2TerminalEB 代码完全相同，只是输出目录不同，方便一次烧两个终端。网关按终端短地址首次出现的顺序自动编号为 L1、L2。

---

## 三、串口输出（9600-8-N-1）

### 3.1 网关上电

```
[APP] ZigBee_NewApp gateway started
[APP] network formed, gateway ready
```

### 3.2 光敏终端上电（串口接在终端上时可见）

```
[APP] ZigBee_NewApp light-terminal started
[APP] light sensor ready, LUX=18 ADC=2 LEVEL=NIGHT
[APP] joined network
[APP] LUX=18 ADC=2 LEVEL=NIGHT LAMP=ON MODE=AUTO cnt=1 tx=OK
[APP] LUX=19 ADC=2 LEVEL=NIGHT LAMP=ON MODE=AUTO cnt=2 tx=OK
```

### 3.3 网关持续打印光敏状态（终端入网后自动上报，每 3s 一帧）

```
[GW] L1 addr=0x796F cnt=1 LUX=18 ADC=2 LEVEL=NIGHT LAMP=ON MODE=AUTO
[GW] L1 addr=0x796F cnt=2 LUX=19 ADC=2 LEVEL=NIGHT LAMP=ON MODE=AUTO
[GW] L1 addr=0x796F cnt=3 LUX=21 ADC=2 LEVEL=NIGHT LAMP=ON MODE=AUTO
[GW] L1 addr=0x796F cnt=4 LUX=1260 ADC=160 LEVEL=DAY LAMP=OFF MODE=AUTO
[GW] L1 addr=0x796F cnt=5 LUX=1490 ADC=189 LEVEL=DAY LAMP=OFF MODE=AUTO
```

字段含义：

| 字段 | 说明 |
| --- | --- |
| `L1` / `L2` | 网关按终端短地址首次出现顺序自动分配的路灯编号 |
| `addr` | 终端 16 位短地址 |
| `cnt` | 该终端累计上报次数（从 1 开始递增，可用于验证丢包） |
| `LUX` | 光照强度，单位 lx |
| `ADC` | 光敏采样原始值（0~255），用于判断传感器是否工作正常 |
| `LEVEL` | `NIGHT` 夜晚 / `DUSK` 黄昏清晨 / `DAY` 白天 |
| `LAMP` | 路灯状态：`ON` 开灯 / `OFF` 关灯 |
| `MODE` | 工作模式：`AUTO` 自动 / `MANUAL_ON` 手动开 / `MANUAL_OFF` 手动关 |

### 3.4 KEY1 手动开关路灯

网关按 **KEY1**：以终端最近一次回传的路灯状态取反，直接下发 `FORCE_ON` / `FORCE_OFF`，
所以**按一下一定换一个状态**（与当前是白天还是夜晚无关）：

```
[GW] KEY1 manual -> lamp ON
[GW] -> L1 addr=0x796F cmd=FORCE_ON sent
```

终端收到后立即执行，节点上的 LED1 点亮，并马上回传一帧：

```
[APP] ctrl cmd=FORCE_ON (manual)
[APP] LUX=1490 ADC=189 LEVEL=DAY LAMP=ON MODE=MANUAL_ON cnt=6 tx=OK
```

网关随即打印出新的状态，上位机的"路灯状态"卡片会显示**手动控制**：

```
[GW] L1 addr=0x796F cnt=6 LUX=1490 ADC=189 LEVEL=DAY LAMP=ON MODE=MANUAL_ON
```

再按一次 KEY1 就关灯：

```
[GW] KEY1 manual -> lamp OFF
[GW] -> L1 addr=0x796F cmd=FORCE_OFF sent
[GW] L1 addr=0x796F cnt=7 LUX=1490 ADC=189 LEVEL=DAY LAMP=OFF MODE=MANUAL_OFF
```

网关按**摇杆中键**则下发 `AUTO`，路灯重新跟随光照自动开关：

```
[GW] KEY center -> AUTO
[GW] -> L1 addr=0x796F cmd=AUTO sent
[GW] L1 addr=0x796F cnt=8 LUX=1490 ADC=189 LEVEL=DAY LAMP=OFF MODE=AUTO
```

> 手动状态会**一直保持**，直到再按一次 KEY1 或按摇杆中键回到 AUTO，不会按时间自动回落。
> 直接按光敏终端自身的 KEY1 效果相同（本地生效 + 回传），网关的 LED1 与上位机显示会同步更新。
> 若网内有 L1、L2 两个终端，KEY1 会同时下发给两台，保证整条路灯状态一致。

---

## 四、上报数据帧（11 字节，小端）

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| 0 | nodeType | 1 = 光敏路灯终端 |
| 1-2 | addr | 终端短地址 |
| 3-4 | count | 上报次数 |
| 5-6 | lux | 光照强度，单位 lx |
| 7 | lamp | 路灯状态：1 开灯，0 关灯 |
| 8 | level | 0 夜晚，1 微亮，2 白天 |
| 9 | adc | 光敏原始采样值 0~255 |
| 10 | mode | 工作模式：0 自动，1 手动开，2 手动关 |

链路参数：端点 20、Profile 0x0F08、上报簇号 1、控制簇号 2，终端向 0x0000 单播。

控制命令帧：簇号 2，长度 1 字节，`0 = AUTO`、`1 = FORCE_ON`、`2 = FORCE_OFF`。

---

## 五、自动控制逻辑

```
读取光照 lux
   ↓
level = lux < 80 ? NIGHT : (lux < 250 ? DUSK : DAY)
   ↓
lamp = 手动开灯(FORCE_ON)  ? ON        ← KEY1 手动
      : 手动关灯(FORCE_OFF) ? OFF       ← KEY1 手动
      : (level == NIGHT) ? ON : OFF     ← 默认自动策略（AUTO）
   ↓
LED1 亮 = 路灯开，灭 = 路灯关
   ↓
组帧单播给网关（带 mode：AUTO / MANUAL_ON / MANUAL_OFF）
```

阈值在 `NewApp.h` 中：

- `NEWAPP_LUX_NIGHT`（默认 80 lx）：低于此值判定为夜晚，自动开灯
- `NEWAPP_LUX_DUSK`（默认 250 lx）：低于此值判定为微亮

上报周期在 `NewApp.h` 中：`NEWAPP_LIGHT_REPORT_PERIOD`（默认 3000 ms）。

### 5.1 手动优先

`NEWAPP_CMD_FORCE_ON / FORCE_OFF` 来自 KEY1，优先级高于光照自动判断：

- 手动开灯后，即使光照充足（`LEVEL=DAY`），路灯也**保持点亮**；
- 手动关灯后，即使天黑了（`LEVEL=NIGHT`），路灯也**保持熄灭**；
- 手动状态**不设超时**，一直保持到再按一次 KEY1 切换、或按摇杆中键回到 `AUTO`；
- 回到 `AUTO` 后立即按当前光照重新决策一次，并回传结果，网关和上位机马上看到变化。

---

## 六、按键与 LED

板载按键只有 `SW6`（P0.1，板载 S1，即 **KEY1**）与摇杆（P2.0），摇杆方向通过 ADC 识别为上 / 下 / 左 / 右 / 中五个键值。

| 操作 | 光敏终端 | 协调器网关 |
| --- | --- | --- |
| **KEY1（板载 S1 / SW6）** | **手动开 / 关本机路灯**：LED1 立即跟随，并立即上报 | **远程手动开 / 关所有路灯**：下发 `FORCE_ON` / `FORCE_OFF` |
| 摇杆中键 | 放弃手动，回到按光照自动控制并上报 | 下发 `AUTO`，让终端回到自动控制 |
| 摇杆上 | LED3 闪 1 下 | LED3 闪 1 下 |
| 摇杆下 | LED3 闪 2 下 | LED3 闪 2 下 |
| 摇杆左 | LED3 闪 3 下 | LED3 闪 3 下 |
| 摇杆右 | LED3 闪 4 下 | LED3 闪 4 下 |

方向键只让 LED3 闪烁，不占用 LED1（路灯指示）与 LED2（入网 / 发送提示），也不发起任何无线通信。

> KEY1 的切换判据是"终端最近一次回传的路灯状态"，所以不管路灯当前是被光照自动点亮的、
> 还是被终端自己的 KEY1 点亮的，网关按一下 KEY1 都会**取反**，不会出现"按了没反应"。

LED 约定：

| 灯 | 终端 | 网关 |
| --- | --- | --- |
| LED1 | **路灯状态**：亮 = 开灯，灭 = 关灯（KEY1 立即生效） | 镜像终端路灯状态 |
| LED2 | 入网成功 / 发送成功 | 收到上报帧 / 控制命令下发成功 |
| LED3 | 未入网或发送失败 | 控制命令下发失败；摇杆方向键提示 |

---

## 七、光敏传感器

### 7.1 默认行为：模拟数据（免硬件演示）

`sensor.h` 中 `SENSOR_LIGHT_SIMULATE` 默认为 `1`，输出一条 **60 秒走完一个昼夜** 的模拟光照曲线：

```
0 ~ 8s    夜晚    8 ~ 20 lx        → LEVEL=NIGHT，自动开灯
8 ~ 14s   黎明    20 ~ 1000 lx     → 变亮，自动关灯
14 ~ 40s  白天    1200 ~ 1800 lx   → LEVEL=DAY
40 ~ 46s  黄昏    1800 ~ 60 lx     → 变暗
46 ~ 60s  夜晚    60 ~ 15 lx       → LEVEL=NIGHT，自动开灯
```

不接任何传感器即可完成组网、上报、自动开关灯、串口显示的完整演示。

### 7.2 接真实光敏模块

1. 把 `sensor.h` 的 `SENSOR_LIGHT_SIMULATE` 改为 `0`；
2. 按实际接线修改引脚宏（默认值如下）：

   | 方式 | 宏 | 默认引脚 |
   | --- | --- | --- |
   | 模拟量 AO（推荐，可读具体 lx） | `SENSOR_LIGHT_ADC_CHN` | P0.5 = AIN5 |
   | 开关量 DO（只能判亮/暗） | `SENSOR_LIGHT_DO_BIT` | P0.4 |

3. `SENSOR_LIGHT_USE_ADC` 为 `1` 用 AO、为 `0` 用 DO；
4. 若发现数值方向反了（越亮数字越小），把 `SENSOR_LIGHT_ADC_RISING` 改为 `0`；
5. 若 DO 模块是低电平表示有光，把 `SENSOR_LIGHT_DO_ACTIVE_HIGH` 改为 `0`；
6. `SENSOR_LIGHT_LUX_MAX`（默认 2000）是 ADC 满量程对应的 lx，可按实测校准。

> 注意：**P0.5（AIN5）被光敏占用，P0.6（AIN6）已被摇杆方向识别占用**，两者不要复用同一路 ADC。

---

## 八、组网参数

- 信道与 PANID 在 `Projects\zstack\Tools\CC2530DB\f8wConfig.cfg` 中：`DEFAULT_CHANLIST`（默认信道 11）、
  `ZDAPP_CONFIG_PAN_ID`（默认 0xFFFF 自动选择）。
- 多人同时实验时，请给每个小组设置独立的 PANID，网关与终端必须一致。

---

## 九、编译与下载

1. 用 IAR 打开 `CC2530DB\ZigBee_NewApp.eww`；
2. 左侧 Workspace 下拉框选择配置（CoordinatorEB / LightTerminalEB）；
3. `Project → Rebuild All`，确认 0 error；
4. 用 CC Debugger 连接节点，`Download and Debug`（或直接 Download）烧写；
5. **先给协调器上电建网**，再给光敏终端上电入网；
6. 协调器串口接电脑，串口助手 9600-8-N-1，即可看到自动滚动输出的光敏状态。

---

## 十、验收要点

| 编号 | 测试项 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| T1 | 协调器建网 | 仅协调器上电，串口接电脑 | 输出 `gateway started`、`network formed, gateway ready` |
| T2 | 终端入网 | 光敏终端上电 | 输出 `joined network`，LED2 闪 3 下 |
| T3 | 光敏自动上报 | 终端入网后不做任何操作 | 网关每 3s 自动输出一行 `[GW] L1 ...` |
| T4 | 自动开灯 | 让光照低于 80 lx（遮盖光敏或等待模拟夜晚） | 终端 LED1 常亮，网关输出 `LEVEL=NIGHT LAMP=ON MODE=AUTO` |
| T5 | 自动关灯 | 让光照高于 250 lx（强光照射或等待模拟白天） | 终端 LED1 熄灭，网关输出 `LEVEL=DAY LAMP=OFF MODE=AUTO` |
| T6 | KEY1 手动开灯 | 网关按 KEY1 | 输出 `KEY1 manual -> lamp ON`、`cmd=FORCE_ON sent`；终端 LED1 常亮，网关随即打印 `LAMP=ON MODE=MANUAL_ON` |
| T7 | KEY1 手动关灯 | 再按一次 KEY1 | 输出 `KEY1 manual -> lamp OFF`、`cmd=FORCE_OFF sent`；终端 LED1 熄灭，网关打印 `LAMP=OFF MODE=MANUAL_OFF` |
| T8 | 手动状态保持 | 在 `LEVEL=DAY` 下手动开灯后等待 1 分钟以上 | 路灯一直亮着，`MODE` 始终为 `MANUAL_ON`，不会被自动控制改回 |
| T9 | 恢复自动 | 网关按摇杆中键 | 输出 `KEY center -> AUTO`、`cmd=AUTO sent`，随后 `MODE=AUTO`，路灯重新按光照自动开关 |
| T10 | 终端本地 KEY1 | 直接按光敏终端自己的 KEY1 | 终端输出 `KEY1 -> lamp ON/OFF (manual)`，LED1 立即变化，网关同步打印新的 `LAMP` / `MODE` |
| T11 | 未入网保护 | 只给终端上电，不建网 | LED3 闪 3 下，网关无输出 |
| T12 | 本地按键隔离 | 按摇杆方向键 | 仅 LED3 闪烁，网关无输出，路灯状态不变 |
| T13 | 串口稳定性 | 连续运行 1 分钟以上 | 计数连续递增，无乱码、无丢帧 |

---

## 十一、与上一版（室内环境监测）的差异

| 对比项 | 环境监测版 | 路灯控制版 |
| --- | --- | --- |
| 网络规模 | 1 协调器 + 2 终端（温湿度 / 火焰） | 1 协调器 + 光敏路灯终端（可扩展 L2） |
| 传感器 | DHT11 温湿度 + 火焰开关量 | 光敏电阻 / 光敏模块（AO 模拟量或 DO 开关量） |
| 上报触发 | 周期 + 按键 + 火焰状态变化 | **周期自动上报（入网即开始）** + 按键 |
| 业务逻辑 | 采集与展示 | 采集 + **自动开关灯决策** + 网关下行控制 |
| 手动控制 | 无 | **KEY1 手动开 / 关路灯**，终端本地立即生效，网关远程同步，状态常驻 |
| 下行链路 | 无 | 簇号 2 控制命令（AUTO / FORCE_ON / FORCE_OFF） |
| 数据帧 | temp / hum / flame | lux / lamp / level / adc / mode |
| 编译配置 | Coordinator / Router / SensorTerminal / FlameTerminal | Coordinator / Router / LightTerminal / Light2Terminal |
