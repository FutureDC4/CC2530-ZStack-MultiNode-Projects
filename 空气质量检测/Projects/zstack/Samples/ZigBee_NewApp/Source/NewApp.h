/**************************************************************************************************
  Filename:       NewApp.h
  Revised:        $Date: 2026-10-22 $

  Description:    ZigBee_NewApp 应用层头文件
                  项目：基于 ZigBee 的空气质量检测系统（烟雾传感器）
                  网络形态：1 个协调器网关 + 1 个烟雾终端，星型

                  终端上电入网后自动按周期采集烟雾浓度并单播上报网关，按 KEY1 可随时
                  手动补报一帧；网关按 KEY1 也能反过来请求终端立即上报。网关解析后
                  通过串口输出，并驱动 LED 报警，上位机据此显示空气质量。

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR（协调器网关）
                    SmokeTerminalEB   -> NEWAPP_ROLE_SMOKE_TERM （烟雾终端）
                    RouterEB          -> NEWAPP_ROLE_SMOKE_TERM （可选中继）

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

/* 端点与 Profile（沿用示例工程 20 / 0x0F08） */
#define NEWAPP_ENDPOINT               20
#define NEWAPP_PROFID                 0x0F08
#define NEWAPP_DEVICEID               0x0001
#define NEWAPP_DEVICE_VERSION         0
#define NEWAPP_FLAGS                  0

/* 两个簇：1 号上报（终端 -> 网关），2 号控制（网关 -> 终端） */
#define NEWAPP_MAX_CLUSTERS           2
#define NEWAPP_REPORT_CLUSTERID       1        /* 上报簇 */
#define NEWAPP_CTRL_CLUSTERID         2        /* 控制簇 */

/* 控制命令（1 字节） */
#define NEWAPP_CTRL_LEN               1
#define NEWAPP_CMD_REPORT_NOW         0        /* 请求终端立即上报一次 */

/* 节点类型：数据帧第 0 字节 */
#define NEWAPP_NODE_COORD             0        /* 协调器网关 */
#define NEWAPP_NODE_SMOKE             1        /* 烟雾终端 */

/* ------------------------------------------------------------------
 * 上报数据帧：固定 10 字节，多字节字段小端序
 *
 *   偏移  字段        说明
 *   ------------------------------------------------------------
 *    0    nodeType    节点类型，固定为 NEWAPP_NODE_SMOKE
 *   1-2   addr        终端 16 位短地址
 *   3-4   count       该终端累计上报次数
 *   5-6   density     烟雾浓度（0 ~ NEWAPP_SMOKE_MAX，等效 ppm）
 *    7    adc         传感器原始采样值 0~255
 *    8    level       空气质量等级 0 清新 / 1 正常 / 2 超标 / 3 严重
 *    9    src         上报方式 0 周期 / 1 本机手动 / 2 网关请求
 * ------------------------------------------------------------------ */
#define NEWAPP_REPORT_LEN             10
#define NEWAPP_RPT_NODETYPE           0        /* 节点类型 */
#define NEWAPP_RPT_ADDR_LO            1        /* 终端短地址低字节 */
#define NEWAPP_RPT_ADDR_HI            2        /* 终端短地址高字节 */
#define NEWAPP_RPT_COUNT_LO           3        /* 上报次数低字节 */
#define NEWAPP_RPT_COUNT_HI           4        /* 上报次数高字节 */
#define NEWAPP_RPT_SMOKE_LO           5        /* 烟雾浓度低字节 */
#define NEWAPP_RPT_SMOKE_HI           6        /* 烟雾浓度高字节 */
#define NEWAPP_RPT_ADC                7        /* 原始采样值 */
#define NEWAPP_RPT_LEVEL              8        /* 空气质量等级 */
#define NEWAPP_RPT_SRC                9        /* 上报方式 */

/* 空气质量等级（与上位机配色一一对应） */
#define NEWAPP_LEVEL_CLEAN            0        /* 清新：低于 NEWAPP_SMOKE_NORMAL */
#define NEWAPP_LEVEL_NORMAL           1        /* 正常：低于 NEWAPP_SMOKE_WARN */
#define NEWAPP_LEVEL_WARN             2        /* 超标：低于 NEWAPP_SMOKE_DANGER */
#define NEWAPP_LEVEL_DANGER           3        /* 严重：达到或超过 NEWAPP_SMOKE_DANGER */

/* 上报方式 */
#define NEWAPP_SRC_PERIODIC           0        /* 周期自动上报 */
#define NEWAPP_SRC_KEY                1        /* 终端 KEY1 手动上报 */
#define NEWAPP_SRC_REMOTE             2        /* 网关请求触发的上报 */

/* 烟雾浓度阈值（等效 ppm，0 ~ NEWAPP_SMOKE_MAX） */
#define NEWAPP_SMOKE_MAX              1000
#define NEWAPP_SMOKE_NORMAL           100      /* 高于此值：轻度异味 */
#define NEWAPP_SMOKE_WARN             300      /* 高于此值：明显烟雾，提示超标 */
#define NEWAPP_SMOKE_DANGER           700      /* 高于此值：严重，声光报警 */

/* 协调器短地址：终端一律单播到该地址 */
#define NEWAPP_COORDINATOR_ADDR       0x0000

/* 网关最多记忆的终端个数，用于打印 S1 / S2 */
#define NEWAPP_MAX_TERMINALS          2

/* 终端周期上报（ms）。手动上报不受此周期影响，按 KEY1 随时可报 */
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
