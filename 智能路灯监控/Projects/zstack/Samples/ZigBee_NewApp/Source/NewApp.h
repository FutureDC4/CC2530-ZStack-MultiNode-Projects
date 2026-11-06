/**************************************************************************************************
  Filename:       NewApp.h
  Revised:        $Date: 2026-10-20 $

  Description:    路灯控制系统 应用层头文件
                  网络形态：1 个协调器网关 + 若干光敏路灯终端（星型）

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR
                    LightTerminalEB   -> NEWAPP_ROLE_LIGHT_TERM
                    Light2TerminalEB  -> NEWAPP_ROLE_LIGHT_TERM（第二路段路灯节点）
                    RouterEB          -> NEWAPP_ROLE_LIGHT_TERM（可选中继）

                  按键：
                    KEY1（板载 S1 / SW6）  手动开 / 关路灯
                                           终端本地切换并上报；网关远程下发 FORCE_ON / FORCE_OFF
                    摇杆中键               回到按光照自动控制（网关下发 AUTO）
                    摇杆方向键             仅本地 LED3 闪烁提示，不通信
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

/* 端点、Profile 与簇号（沿用示例工程端点 20 / Profile 0x0F08） */
#define NEWAPP_ENDPOINT               20
#define NEWAPP_PROFID                 0x0F08
#define NEWAPP_DEVICEID               0x0001
#define NEWAPP_DEVICE_VERSION         0
#define NEWAPP_FLAGS                  0

#define NEWAPP_MAX_CLUSTERS           2
#define NEWAPP_REPORT_CLUSTERID       1        /* 终端 -> 网关：光照状态上报 */
#define NEWAPP_CTRL_CLUSTERID         2        /* 网关 -> 终端：路灯控制命令 */

/* 节点类型（上报帧第 0 字节） */
#define NEWAPP_NODE_COORD             0        /* 协调器网关 */
#define NEWAPP_NODE_LIGHT             1        /* 光敏路灯终端 */

/* 上报数据帧：固定 11 字节，多字节字段小端序 */
#define NEWAPP_REPORT_LEN             11
#define NEWAPP_RPT_NODETYPE           0        /* 节点类型 */
#define NEWAPP_RPT_ADDR_LO            1        /* 终端短地址低字节 */
#define NEWAPP_RPT_ADDR_HI            2        /* 终端短地址高字节 */
#define NEWAPP_RPT_COUNT_LO           3        /* 上报次数低字节 */
#define NEWAPP_RPT_COUNT_HI           4        /* 上报次数高字节 */
#define NEWAPP_RPT_LUX_LO             5        /* 光照值（lx）低字节 */
#define NEWAPP_RPT_LUX_HI             6        /* 光照值（lx）高字节 */
#define NEWAPP_RPT_LAMP               7        /* 路灯状态：1 开灯，0 关灯 */
#define NEWAPP_RPT_LEVEL              8        /* 光照等级：0 夜，1 微亮，2 白天 */
#define NEWAPP_RPT_ADC                9        /* 光敏原始采样值（0~255） */
#define NEWAPP_RPT_MODE               10       /* 工作模式：0 自动，1 手动开，2 手动关 */

/* 光照等级 */
#define NEWAPP_LEVEL_NIGHT            0        /* 夜晚：低于 NEWAPP_LUX_NIGHT */
#define NEWAPP_LEVEL_DUSK             1        /* 黄昏 / 清晨 */
#define NEWAPP_LEVEL_DAY              2        /* 白天 */

/* 自动开关灯的光照阈值（单位 lx） */
#define NEWAPP_LUX_NIGHT              80       /* 低于此值判为夜晚，自动开灯 */
#define NEWAPP_LUX_DUSK               250      /* 低于此值判为微亮 */

/* 路灯控制命令（网关 -> 终端，1 字节） */
#define NEWAPP_CTRL_LEN               1
#define NEWAPP_CMD_AUTO               0        /* 恢复按光照自动控制 */
#define NEWAPP_CMD_FORCE_ON           1        /* 强制开灯 */
#define NEWAPP_CMD_FORCE_OFF          2        /* 强制关灯 */

/* 协调器固定短地址，终端一律单播到此 */
#define NEWAPP_COORDINATOR_ADDR       0x0000

/* 网关最多为几个终端分配 L1/L2 编号 */
#define NEWAPP_MAX_TERMINALS          2

/* 光敏状态自动上报周期（ms），入网后自动启动，无需按键 */
#define NEWAPP_LIGHT_REPORT_PERIOD    3000
#define NEWAPP_LIGHT_REPORT_EVT       0x0001

/* 入网后多长时间上报第一帧（ms），让状态尽快出现在串口上 */
#define NEWAPP_FIRST_REPORT_DELAY     500

/* 说明：手动开关灯不需要定时回落，
   按 KEY1 手动设定的状态会一直保持，直到再按 KEY1 切换或按摇杆中键回到 AUTO。 */

/* 串口端口（底板 UART0，9600-8-N-1，在 Components\mt\MT_UART.h 中配置） */
#define NEWAPP_UART_PORT              0

/*********************************************************************
 * MACROS
 */

/*********************************************************************
 * FUNCTIONS
 */

/* 应用初始化，由 osalInitTasks() 调用 */
extern void NewApp_Init( uint8 task_id );

/* 应用事件处理函数，挂到 OSAL 主循环 */
extern UINT16 NewApp_ProcessEvent( uint8 task_id, uint16 events );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* NEWAPP_H */
