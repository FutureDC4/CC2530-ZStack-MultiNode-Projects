/**************************************************************************************************
  Filename:       NewApp.h
  Revised:        $Date: 2026-10-20 $

  Description:    ZigBee_NewApp 应用层头文件
                  网络形态：1 个协调器网关 + 2 个终端（温湿度终端、火焰报警终端）

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
#define NEWAPP_NODE_FLAME             2        /* 火焰报警终端 */

/* 上报数据帧长度与字段偏移（小端） */
#define NEWAPP_REPORT_LEN             10
#define NEWAPP_RPT_NODETYPE           0        /* 节点类型 */
#define NEWAPP_RPT_ADDR_LO            1        /* 终端短地址低字节 */
#define NEWAPP_RPT_ADDR_HI            2        /* 终端短地址高字节 */
#define NEWAPP_RPT_COUNT_LO           3        /* 上报次数低字节 */
#define NEWAPP_RPT_COUNT_HI           4        /* 上报次数高字节 */
#define NEWAPP_RPT_TEMP_LO            5        /* 温度（0.1℃）低字节 */
#define NEWAPP_RPT_TEMP_HI            6        /* 温度（0.1℃）高字节 */
#define NEWAPP_RPT_HUM_LO             7        /* 湿度（0.1%RH）低字节 */
#define NEWAPP_RPT_HUM_HI             8        /* 湿度（0.1%RH）高字节 */
#define NEWAPP_RPT_FLAME              9        /* 火焰状态：1 检测到明火，0 安全 */

/* 协调器短地址：终端一律单播到该地址 */
#define NEWAPP_COORDINATOR_ADDR       0x0000

/* 网关最多记忆的终端个数，用于打印 T1/T2 */
#define NEWAPP_MAX_TERMINALS          2

/* 火焰终端轮询火焰传感器的周期（ms）与事件位 */
#define NEWAPP_FLAME_POLL_PERIOD      250
#define NEWAPP_FLAME_POLL_EVT         0x0001

/* 温湿度终端周期上报（ms）：入网后自动上报，按键仍可随时立即上报；
   设成 0 表示只由按键触发 */
#define NEWAPP_SENSOR_REPORT_PERIOD   5000
#define NEWAPP_SENSOR_REPORT_EVT      0x0002

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
