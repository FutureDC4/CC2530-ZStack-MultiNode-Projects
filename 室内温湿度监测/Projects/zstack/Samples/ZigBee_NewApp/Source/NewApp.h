/**************************************************************************************************
  Filename:       NewApp.h
  Revised:        $Date: 2026-10-22 $

  Description:    ZigBee_NewApp 应用层头文件
                  项目：基于 ZigBee 的室内温湿度监测系统
                  网络形态：1 个协调器网关 + 1 个温湿度终端，星型单跳

                  终端上电入网后自动按周期采集空气温湿度并单播上报网关；
                  网关解析后通过串口输出供上位机显示，同时用 LED 指示环境是否舒适。

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR（协调器网关）
                    SensorTerminalEB  -> NEWAPP_ROLE_SENSOR_TERM（温湿度终端）
                    RouterEB          -> NEWAPP_ROLE_SENSOR_TERM（可选，兼作中继或第二节点）

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

/* ------------------------------------------------------------------
 * 上报数据帧：固定 10 字节，多字节字段小端序
 *
 *   偏移   字段                说明
 *   ---------------------------------------------------------------
 *    0     nodeType            1 = 温湿度终端
 *   1-2    终端短地址
 *   3-4    上报次数（从 1 递增，可用于统计丢包）
 *   5-6    空气温度            单位 0.1℃，int16，可表示负温度
 *   7-8    空气湿度            单位 0.1%RH，uint16
 *    9     保留                固定 0，便于后续扩展（如电量、报警位）
 * ------------------------------------------------------------------ */
#define NEWAPP_REPORT_LEN             10
#define NEWAPP_RPT_NODETYPE           0        /* 节点类型 */
#define NEWAPP_RPT_ADDR_LO            1        /* 终端短地址低字节 */
#define NEWAPP_RPT_ADDR_HI            2        /* 终端短地址高字节 */
#define NEWAPP_RPT_COUNT_LO           3        /* 上报次数低字节 */
#define NEWAPP_RPT_COUNT_HI           4        /* 上报次数高字节 */
#define NEWAPP_RPT_TEMP_LO            5        /* 温度低字节（单位 0.1℃） */
#define NEWAPP_RPT_TEMP_HI            6        /* 温度高字节 */
#define NEWAPP_RPT_HUM_LO             7        /* 湿度低字节（单位 0.1%RH） */
#define NEWAPP_RPT_HUM_HI             8        /* 湿度高字节 */
#define NEWAPP_RPT_RESERVED           9        /* 保留 */

/* 室内舒适区间（与上位机判定阈值保持一致） */
#define NEWAPP_TEMP_LOW               150      /* 15.0℃：低于此值偏冷 */
#define NEWAPP_TEMP_HIGH              300      /* 30.0℃：高于此值偏热 */
#define NEWAPP_HUM_LOW                450      /* 45.0%RH：低于此值偏干 */
#define NEWAPP_HUM_HIGH               850      /* 85.0%RH：高于此值偏湿 */

/* 协调器短地址：终端一律单播到该地址 */
#define NEWAPP_COORDINATOR_ADDR       0x0000

/* 网关最多记忆的终端个数，用于打印 T1 / T2（单节点时只用 T1） */
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
