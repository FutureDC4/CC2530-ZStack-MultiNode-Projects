/**************************************************************************************************
  Filename:       NewApp.h
  Revised:        $Date: 2026-10-22 $

  Description:    ZigBee_NewApp 应用层头文件
                  项目：基于 ZigBee 的大棚环境监测系统
                  网络形态：1 个协调器网关 + 2 个终端（温湿度终端、光敏终端）

                  终端上电入网后自动按周期采集并单播上报网关，
                  网关解析后通过串口输出，并驱动 LED 提示，上位机据此显示大棚环境。

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR
                    RouterEB          -> NEWAPP_ROLE_SENSOR_TERM（可选中继节点）
                    SensorTerminalEB  -> NEWAPP_ROLE_SENSOR_TERM（温湿度终端）
                    LightTerminalEB   -> NEWAPP_ROLE_LIGHT_TERM（光敏终端）

  Copyright (c) 2026 ZigBee_NewApp. All rights reserved.
**************************************************************************************************/

#ifndef NEWAPP_H
#define NEWAPP_H

#ifdef __cplusplus
extern "C"
{
#endif

/*********************************************************************
 * INCLUDES
 */
#include "ZComDef.h"

/*********************************************************************
 * CONSTANTS
 */

/* 端点、Profile 与簇号（沿用原示例工程 20 / 0x0F08，可按需修改） */
#define NEWAPP_ENDPOINT               20
#define NEWAPP_PROFID                 0x0F08
#define NEWAPP_DEVICEID               0x0001
#define NEWAPP_DEVICE_VERSION         0
#define NEWAPP_FLAGS                  0

#define NEWAPP_MAX_CLUSTERS           1
#define NEWAPP_REPORT_CLUSTERID       1        /* 终端 -> 协调器 上报簇 */

/* 节点类型：数据帧第 0 字节 */
#define NEWAPP_NODE_COORD             0        /* 协调器网关 */
#define NEWAPP_NODE_SENSOR            1        /* 温湿度终端 */
#define NEWAPP_NODE_LIGHT             2        /* 光敏终端 */

/* ------------------------------------------------------------------
 * 上报数据帧：固定 10 字节，多字节字段小端序
 *
 * 两种终端共用同一个 10 字节帧，靠第 0 字节 nodeType 区分载荷含义：
 *
 *   偏移   温湿度帧(nodeType=1)          光敏帧(nodeType=2)
 *   ------------------------------------------------------------
 *    0     nodeType = 1                 nodeType = 2
 *   1-2    终端短地址                    终端短地址
 *   3-4    上报次数                      上报次数
 *   5-6    温度（0.1℃，int16）           光照强度（lx，uint16）
 *   7      湿度低字节                    光敏原始采样值（0~255）
 *   8      湿度高字节                    光照等级：0 夜 / 1 微亮 / 2 白天
 *   9      保留                         保留
 * ------------------------------------------------------------------ */
#define NEWAPP_REPORT_LEN             10
#define NEWAPP_RPT_NODETYPE           0        /* 节点类型 */
#define NEWAPP_RPT_ADDR_LO            1        /* 终端短地址低字节 */
#define NEWAPP_RPT_ADDR_HI            2        /* 终端短地址高字节 */
#define NEWAPP_RPT_COUNT_LO           3        /* 上报次数低字节 */
#define NEWAPP_RPT_COUNT_HI           4        /* 上报次数高字节 */

/* 温湿度帧载荷（nodeType = NEWAPP_NODE_SENSOR） */
#define NEWAPP_RPT_TEMP_LO            5        /* 温度低字节（单位 0.1℃） */
#define NEWAPP_RPT_TEMP_HI            6        /* 温度高字节 */
#define NEWAPP_RPT_HUM_LO             7        /* 湿度低字节（单位 0.1%RH） */
#define NEWAPP_RPT_HUM_HI             8        /* 湿度高字节 */
#define NEWAPP_RPT_TH_RSV             9        /* 保留 */

/* 光敏帧载荷（nodeType = NEWAPP_NODE_LIGHT） */
#define NEWAPP_RPT_LUX_LO             5        /* 光照低字节（单位 lx） */
#define NEWAPP_RPT_LUX_HI             6        /* 光照高字节 */
#define NEWAPP_RPT_ADC                7        /* 光敏原始采样值 0~255 */
#define NEWAPP_RPT_LEVEL              8        /* 光照等级 0 夜 / 1 微亮 / 2 白天 */
#define NEWAPP_RPT_LIGHT_RSV          9        /* 保留 */

/* 光照等级（大棚补光判断用） */
#define NEWAPP_LEVEL_NIGHT            0        /* 严重不足：低于 NEWAPP_LUX_NIGHT */
#define NEWAPP_LEVEL_DUSK             1        /* 偏弱：介于两个阈值之间 */
#define NEWAPP_LEVEL_DAY              2        /* 充足：高于 NEWAPP_LUX_DUSK */

/* 大棚光照阈值（单位 lx） */
#define NEWAPP_LUX_NIGHT              80       /* 低于此值判为光照不足，需要补光 */
#define NEWAPP_LUX_DUSK               250      /* 低于此值判为光照偏弱 */

/* 协调器短地址：终端一律单播到该地址 */
#define NEWAPP_COORDINATOR_ADDR       0x0000

/* 网关最多记忆的终端个数，用于打印 T1 / T2 */
#define NEWAPP_MAX_TERMINALS          2

/* 终端周期上报（ms）：入网后自动上报，按键仍可随时立即上报一次；
   设成 0 表示只由按键触发 */
#define NEWAPP_REPORT_PERIOD          5000
#define NEWAPP_REPORT_EVT             0x0001

/* 入网后多长时间上报第一帧（ms），让数据尽快出现在串口上 */
#define NEWAPP_FIRST_REPORT_DELAY     500

/* 串口参数：与配套底板一致 9600-8-N-1（波特率在 Components\mt\MT_UART.h 中配置） */
#define NEWAPP_UART_PORT              0

/*********************************************************************
 * MACROS
 */

/*********************************************************************
 * FUNCTIONS
 */

/* 任务初始化：由 osalInitTasks() 调用 */
extern void NewApp_Init( uint8 task_id );

/* 任务事件处理：由 OSAL 主循环调用 */
extern UINT16 NewApp_ProcessEvent( uint8 task_id, uint16 events );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* NEWAPP_H */
