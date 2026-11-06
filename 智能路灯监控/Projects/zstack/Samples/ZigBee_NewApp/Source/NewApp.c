/**************************************************************************************************
  Filename:       NewApp.c
  Revised:        $Date: 2026-10-20 $

  Description:    路灯控制系统 应用层主程序
                  网络形态：1 个协调器网关 + 若干光敏路灯终端（星型）

                  工作方式：
                    光敏终端上电入网后，自动按周期采集光照并单播上报网关，
                    光照低于阈值时本地点亮路灯（LED1），高于阈值时关灯；
                    网关收到上报后解析并通过串口输出，同时用 LED1 镜像路灯状态。

                  按键（手动控制）：
                    KEY1（板载 S1 / SW6）
                        终端：手动切换本机路灯 开/关，并立即上报
                        网关：远程切换路灯 开/关（下发 FORCE_ON / FORCE_OFF）
                    摇杆中键
                        终端：放弃手动，回到按光照自动控制
                        网关：下发 AUTO，让终端回到自动控制

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR
                    LightTerminalEB   -> NEWAPP_ROLE_LIGHT_TERM
                    Light2TerminalEB  -> NEWAPP_ROLE_LIGHT_TERM
                    RouterEB          -> NEWAPP_ROLE_LIGHT_TERM

  Copyright (c) 2026 ZigBee_NewApp. All rights reserved.
**************************************************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "OSAL.h"
#include "ZGlobals.h"
#include "AF.h"
#include "NLMEDE.h"
#include "ZDApp.h"
#include "OnBoard.h"

#include "NewApp.h"
#include "sensor.h"

#include "hal_lcd.h"
#include "hal_led.h"
#include "hal_key.h"
#include "hal_uart.h"

#include "MT_UART.h"

/*********************************************************************
 * 角色定义（编译期确定）
 */
#if defined ( NEWAPP_ROLE_COORDINATOR )
  #define NEWAPP_ROLE            NEWAPP_NODE_COORD
  #define NEWAPP_IS_TERMINAL     0
#elif defined ( NEWAPP_ROLE_LIGHT_TERM )
  #define NEWAPP_ROLE            NEWAPP_NODE_LIGHT
  #define NEWAPP_IS_TERMINAL     1
#else
  #error "please define NEWAPP_ROLE_COORDINATOR / NEWAPP_ROLE_LIGHT_TERM"
#endif

/*********************************************************************
 * LOCAL VARIABLES
 */
#define NEWAPP_TX_BUF_LEN   96

uint8       NewApp_TaskID;                 /* 本任务 ID */
devStates_t NewApp_NwkState;               /* 网络状态 */
uint8       NewApp_TransID;                /* AF 发送序号 */

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint16 NewApp_ReportCount = 0;      /* 终端：累计上报次数 */
static uint8  NewApp_LampOn      = 0;      /* 终端：当前路灯状态 */
static uint8  NewApp_LampMode    = NEWAPP_CMD_AUTO;   /* 终端：0 自动 1 强制开 2 强制关 */
#else
static uint8  NewApp_GwLampOn    = 0;      /* 网关：最近一次由终端回传的路灯状态（用于手动切换的判据） */
static uint16 NewApp_TerminalAddr[NEWAPP_MAX_TERMINALS];  /* 网关：终端短地址表 */
static uint8  NewApp_TerminalUsed[NEWAPP_MAX_TERMINALS];  /* 网关：表项是否有效 */
#endif

static uint8 NewApp_TxBuf[NEWAPP_TX_BUF_LEN];      /* 串口发送缓冲区 */
static uint8 NewApp_ReportFrame[NEWAPP_REPORT_LEN];/* 无线上报数据帧 */

const cId_t NewApp_ClusterList[NEWAPP_MAX_CLUSTERS] =
{
  NEWAPP_REPORT_CLUSTERID,
  NEWAPP_CTRL_CLUSTERID
};

const SimpleDescriptionFormat_t NewApp_SimpleDesc =
{
  NEWAPP_ENDPOINT,                 /* 端点号 */
  NEWAPP_PROFID,                   /* Profile ID */
  NEWAPP_DEVICEID,                 /* 设备 ID */
  NEWAPP_DEVICE_VERSION,           /* 设备版本 */
  NEWAPP_FLAGS,                    /* 标志 */
  NEWAPP_MAX_CLUSTERS,             /* 输入簇个数 */
  (cId_t *)NewApp_ClusterList,     /* 输入簇列表 */
  NEWAPP_MAX_CLUSTERS,             /* 输出簇个数 */
  (cId_t *)NewApp_ClusterList      /* 输出簇列表 */
};

endPointDesc_t NewApp_epDesc;                       /* 端点描述 */
static afAddrType_t NewApp_Report_DstAddr;          /* 上报目的地址：协调器 */

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static const char *NewApp_RoleName( void );
static const char *NewApp_LevelName( uint8 level );
static const char *NewApp_CmdName( uint8 cmd );
static const char *NewApp_ModeName( uint8 mode );
static uint8  NewApp_AppendStr( uint8 pos, const char *str );
static uint8  NewApp_AppendDec( uint8 pos, uint16 value );
static uint8  NewApp_AppendHex4( uint8 pos, uint16 value );
static void   NewApp_UartSend( uint8 len );
static void   NewApp_NwkStateChange( void );
static void   NewApp_HandleKeys( uint8 shift, uint8 keys );
static void   NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt );

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint8  NewApp_IsJoined( void );
static uint8  NewApp_LightLevel( uint16 lux );
static void   NewApp_ApplyLamp( uint8 on );
static void   NewApp_LocalToggle( void );
static void   NewApp_LocalAuto( void );
static void   NewApp_SendReport( void );
#else
static uint8  NewApp_FindTerminal( uint16 addr );
static void   NewApp_GwToggle( void );
static void   NewApp_GwAuto( void );
static uint8  NewApp_SendCtrl( uint8 cmd );
#endif

/*********************************************************************
 * @fn      NewApp_Init
 *
 * @brief   应用任务初始化：串口、传感器、端点、按键、目的地址
 *
 * @param   task_id - OSAL 分配的任务号
 *
 * @return  none
 */
void NewApp_Init( uint8 task_id )
{
  uint8 n = 0;

#if ( NEWAPP_IS_TERMINAL == 1 )
  sensorLight_t ls;
#endif

  NewApp_TaskID   = task_id;
  NewApp_NwkState = DEV_INIT;
  NewApp_TransID  = 0;

#if ( NEWAPP_IS_TERMINAL == 1 )
  NewApp_ReportCount = 0;
  NewApp_LampOn      = 0;
  NewApp_LampMode    = NEWAPP_CMD_AUTO;
#else
  NewApp_GwLampOn    = 0;
#endif

  /* 串口初始化：网关用它把光照数据送到上位机，终端也可用来看本地调试信息 */
  MT_UartInit();
  MT_UartRegisterTaskID( task_id );

  /* 光敏传感器只由终端使用，网关不需要 */
#if ( NEWAPP_IS_TERMINAL == 1 )
  Sensor_Init();
#endif

  /* 上报目的地址：协调器 0x0000，16 位短地址点对点单播 */
  NewApp_Report_DstAddr.addrMode       = (afAddrMode_t)Addr16Bit;
  NewApp_Report_DstAddr.endPoint       = NEWAPP_ENDPOINT;
  NewApp_Report_DstAddr.addr.shortAddr = NEWAPP_COORDINATOR_ADDR;

  /* 端点描述与注册 */
  NewApp_epDesc.endPoint   = NEWAPP_ENDPOINT;
  NewApp_epDesc.task_id    = &NewApp_TaskID;
  NewApp_epDesc.simpleDesc = (SimpleDescriptionFormat_t *)&NewApp_SimpleDesc;
  NewApp_epDesc.latencyReq = noLatencyReqs;
  afRegister( &NewApp_epDesc );

  /* 注册按键：按键动作会以 KEY_CHANGE 消息进入本任务 */
  RegisterForKeys( NewApp_TaskID );

  /* 上电打印本节点角色 */
  n = NewApp_AppendStr( n, "[APP] ZigBee_NewApp " );
  n = NewApp_AppendStr( n, NewApp_RoleName() );
  n = NewApp_AppendStr( n, " started\r\n" );
  NewApp_UartSend( n );

#if ( NEWAPP_IS_TERMINAL == 1 )
  /* 上电先打印一次光敏状态：不依赖入网，单独接串口就能确认传感器是否正常 */
  if ( Sensor_ReadLight( &ls ) )
  {
    n = 0;
    n = NewApp_AppendStr( n, "[APP] light sensor ready, LUX=" );
    n = NewApp_AppendDec( n, ls.lux );
    n = NewApp_AppendStr( n, " ADC=" );
    n = NewApp_AppendDec( n, (uint16)ls.adc );
    n = NewApp_AppendStr( n, " LEVEL=" );
    n = NewApp_AppendStr( n, NewApp_LevelName( NewApp_LightLevel( ls.lux ) ) );
    n = NewApp_AppendStr( n, "\r\n" );
    NewApp_UartSend( n );
  }
  else
  {
    n = NewApp_AppendStr( 0, "[APP] light sensor not ready\r\n" );
    NewApp_UartSend( n );
  }
#endif
}

/*********************************************************************
 * @fn      NewApp_ProcessEvent
 *
 * @brief   应用任务事件处理
 *
 * @param   task_id - 任务号（未使用）
 * @param   events  - 事件位图
 *
 * @return  未处理的事件
 */
uint16 NewApp_ProcessEvent( uint8 task_id, uint16 events )
{
  afIncomingMSGPacket_t *MSGpkt;

  (void)task_id;

  if ( events & SYS_EVENT_MSG )
  {
    MSGpkt = (afIncomingMSGPacket_t *)osal_msg_receive( NewApp_TaskID );

    while ( MSGpkt )
    {
      switch ( MSGpkt->hdr.event )
      {
        case KEY_CHANGE:                    /* 按键事件 */
          NewApp_HandleKeys( ((keyChange_t *)MSGpkt)->state,
                             ((keyChange_t *)MSGpkt)->keys );
          break;

        case AF_INCOMING_MSG_CMD:           /* 收到无线数据 */
          NewApp_MessageMSGCB( MSGpkt );
          break;

        case ZDO_STATE_CHANGE:              /* 网络状态变化 */
          NewApp_NwkState = (devStates_t)(MSGpkt->hdr.status);
          NewApp_NwkStateChange();
          break;

        default:
          break;
      }

      /* 释放消息内存，取下一个 */
      osal_msg_deallocate( (uint8 *)MSGpkt );
      MSGpkt = (afIncomingMSGPacket_t *)osal_msg_receive( NewApp_TaskID );
    }

    return ( events ^ SYS_EVENT_MSG );
  }

#if ( NEWAPP_IS_TERMINAL == 1 )
  /* 光敏终端：周期采集并自动上报，入网后由入网事件启动 */
  if ( events & NEWAPP_LIGHT_REPORT_EVT )
  {
    NewApp_SendReport();

    if ( NewApp_IsJoined() )
    {
      osal_start_timerEx( NewApp_TaskID, NEWAPP_LIGHT_REPORT_EVT,
                          NEWAPP_LIGHT_REPORT_PERIOD );
    }

    return ( events ^ NEWAPP_LIGHT_REPORT_EVT );
  }

  /* 说明：手动开/关灯一经按键设定就一直保持，
     直到再次按 KEY1 切换、或按摇杆中键回到 AUTO，不再按时间自动回落，
     否则手动点亮的路灯会在几十秒后被自动控制悄悄覆盖掉。 */
#endif

  return 0;
}

/*********************************************************************
 * @fn      NewApp_HandleKeys
 *
 * @brief   按键处理
 *          摇杆方向键：只让 LED3 闪几下做本地提示，不发起任何无线通信，
 *                      也不占用 LED1（路灯指示）和 LED2（入网/发送提示）
 *          KEY1（板载 S1 / SW6）：手动开 / 关路灯
 *                      终端 -> 切换本机路灯并立即上报
 *                      网关 -> 向所有已入网终端下发 FORCE_ON / FORCE_OFF
 *          摇杆中键：放弃手动，回到按光照自动控制
 *
 * @param   shift - 组合键状态（未使用）
 * @param   keys  - 按键位图
 *
 * @return  none
 */
static void NewApp_HandleKeys( uint8 shift, uint8 keys )
{
  (void)shift;

  /* 方向键：不同次数闪烁，仅作本地提示 */
  if ( keys & HAL_KEY_UP )
  {
    HalLedBlink( HAL_LED_3, 1, 60, 220 );
  }

  if ( keys & HAL_KEY_DOWN )
  {
    HalLedBlink( HAL_LED_3, 2, 60, 220 );
  }

  if ( keys & HAL_KEY_LEFT )
  {
    HalLedBlink( HAL_LED_3, 3, 60, 220 );
  }

  if ( keys & HAL_KEY_RIGHT )
  {
    HalLedBlink( HAL_LED_3, 4, 60, 220 );
  }

  /* 摇杆中键：回到自动控制 */
  if ( keys & HAL_KEY_CENTER )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    NewApp_LocalAuto();
#else
    NewApp_GwAuto();
#endif
  }

  /* KEY1 = 板载 S1（SW6 / P0.1）：手动开 / 关路灯 */
  if ( keys & HAL_KEY_SW_6 )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    NewApp_LocalToggle();
#else
    NewApp_GwToggle();
#endif
  }
}

/*********************************************************************
 * @fn      NewApp_MessageMSGCB
 *
 * @brief   收到无线数据的回调
 *          网关：解析光敏上报帧并通过串口输出
 *          终端：解析网关下发的路灯控制命令
 *
 * @param   pkt - 收到的数据包
 *
 * @return  none
 */
static void NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt )
{
  if ( pkt->clusterId == NEWAPP_REPORT_CLUSTERID )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    /* 终端不处理上报帧 */
    return;
#else
    uint8  *d;
    uint8   nodeType;
    uint8   lamp;
    uint8   level;
    uint8   mode;
    uint8   adc;
    uint8   term;
    uint8   n;
    uint16  addr;
    uint16  count;
    uint16  lux;

    if ( pkt->cmd.DataLength < NEWAPP_REPORT_LEN )
    {
      return;
    }

    d        = pkt->cmd.Data;
    nodeType = d[NEWAPP_RPT_NODETYPE];

    if ( nodeType != NEWAPP_NODE_LIGHT )
    {
      return;
    }

    addr  = (uint16)d[NEWAPP_RPT_ADDR_LO] |
            (uint16)( (uint16)d[NEWAPP_RPT_ADDR_HI] << 8 );
    count = (uint16)d[NEWAPP_RPT_COUNT_LO] |
            (uint16)( (uint16)d[NEWAPP_RPT_COUNT_HI] << 8 );
    lux   = (uint16)d[NEWAPP_RPT_LUX_LO] |
            (uint16)( (uint16)d[NEWAPP_RPT_LUX_HI] << 8 );
    lamp  = d[NEWAPP_RPT_LAMP];
    level = d[NEWAPP_RPT_LEVEL];
    mode  = d[NEWAPP_RPT_MODE];
    adc   = d[NEWAPP_RPT_ADC];

    /* 按终端短地址首次出现顺序编号 L1 / L2 */
    term = NewApp_FindTerminal( addr );

    n = 0;
    n = NewApp_AppendStr( n, "[GW] L" );
    n = NewApp_AppendDec( n, (uint16)term );
    n = NewApp_AppendStr( n, " addr=0x" );
    n = NewApp_AppendHex4( n, addr );
    n = NewApp_AppendStr( n, " cnt=" );
    n = NewApp_AppendDec( n, count );
    n = NewApp_AppendStr( n, " LUX=" );
    n = NewApp_AppendDec( n, lux );
    n = NewApp_AppendStr( n, " ADC=" );
    n = NewApp_AppendDec( n, (uint16)adc );
    n = NewApp_AppendStr( n, " LEVEL=" );
    n = NewApp_AppendStr( n, NewApp_LevelName( level ) );
    n = NewApp_AppendStr( n, " LAMP=" );
    n = NewApp_AppendStr( n, lamp ? "ON" : "OFF" );
    n = NewApp_AppendStr( n, " MODE=" );
    n = NewApp_AppendStr( n, NewApp_ModeName( mode ) );
    n = NewApp_AppendStr( n, "\r\n" );
    NewApp_UartSend( n );

    /* 记住终端自报的真实路灯状态：
       下一次按 KEY1 就按这个状态取反，保证按一下一定换一个状态，
       不管路灯是被自动控制还是被终端自己的 KEY1 点亮的。 */
    NewApp_GwLampOn = lamp;

    /* 网关用 LED1 镜像路灯状态：亮 = 路灯开 */
    HalLedSet( HAL_LED_1, lamp ? HAL_LED_MODE_ON : HAL_LED_MODE_OFF );

    /* 其余灯闪一下表示收到一帧 */
    HalLedBlink( HAL_LED_2, 1, 50, 100 );
#endif
  }
  else if ( pkt->clusterId == NEWAPP_CTRL_CLUSTERID )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    uint8 cmd;
    uint8 n;

    if ( pkt->cmd.DataLength < NEWAPP_CTRL_LEN )
    {
      return;
    }

    cmd = pkt->cmd.Data[0];

    if ( cmd > NEWAPP_CMD_FORCE_OFF )
    {
      return;
    }

    NewApp_LampMode = cmd;

    n = 0;
    n = NewApp_AppendStr( n, "[APP] ctrl cmd=" );
    n = NewApp_AppendStr( n, NewApp_CmdName( cmd ) );
    n = NewApp_AppendStr( n, ( cmd == NEWAPP_CMD_AUTO ) ? " (auto by light)\r\n"
                                                       : " (manual)\r\n" );
    NewApp_UartSend( n );

    /* 手动命令一直保持，直到网关再下发 AUTO 或本机按 KEY1 切换 */
    NewApp_SendReport();
#else
    /* 网关不处理控制命令回环 */
    return;
#endif
  }
}

#if ( NEWAPP_IS_TERMINAL == 1 )
/*********************************************************************
 * @fn      NewApp_SendReport
 *
 * @brief   采集光照、更新路灯状态并组帧单播给协调器
 *
 * @return  none
 */
static void NewApp_SendReport( void )
{
  sensorLight_t ls;
  afStatus_t    status;
  uint16        addr;
  uint8         level;
  uint8         lamp;
  uint8         n;

  /* 未入网不允许发送，避免无效数据 */
  if ( !NewApp_IsJoined() )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    NewApp_UartSend( NewApp_AppendStr( 0, "[APP] not joined, report dropped\r\n" ) );
    return;
  }

  if ( !Sensor_ReadLight( &ls ) )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    NewApp_UartSend( NewApp_AppendStr( 0, "[APP] light sensor read failed\r\n" ) );
    return;
  }

  level = NewApp_LightLevel( ls.lux );

  /* 路灯决策：强制命令优先，否则按光照等级自动判断 */
  if ( NewApp_LampMode == NEWAPP_CMD_FORCE_ON )
  {
    lamp = 1;
  }
  else if ( NewApp_LampMode == NEWAPP_CMD_FORCE_OFF )
  {
    lamp = 0;
  }
  else
  {
    lamp = ( level == NEWAPP_LEVEL_NIGHT ) ? 1 : 0;
  }

  NewApp_ApplyLamp( lamp );

  NewApp_ReportCount++;
  addr = NLME_GetShortAddr();

  NewApp_ReportFrame[NEWAPP_RPT_NODETYPE] = NEWAPP_NODE_LIGHT;
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_LO]  = (uint8)( addr & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_HI]  = (uint8)( ( addr >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_LO] = (uint8)( NewApp_ReportCount & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_HI] = (uint8)( ( NewApp_ReportCount >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_LUX_LO]   = (uint8)( ls.lux & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_LUX_HI]   = (uint8)( ( ls.lux >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_LAMP]     = lamp;
  NewApp_ReportFrame[NEWAPP_RPT_LEVEL]    = level;
  NewApp_ReportFrame[NEWAPP_RPT_MODE]     = NewApp_LampMode;
  NewApp_ReportFrame[NEWAPP_RPT_ADC]      = ls.adc;

  status = AF_DataRequest( &NewApp_Report_DstAddr,
                           &NewApp_epDesc,
                           NEWAPP_REPORT_CLUSTERID,
                           NEWAPP_REPORT_LEN,
                           NewApp_ReportFrame,
                           &NewApp_TransID,
                           AF_DISCV_ROUTE,
                           AF_DEFAULT_RADIUS );

  /* 本地打印一帧，串口直接接在终端上也能看到光敏状态 */
  n = 0;
  n = NewApp_AppendStr( n, "[APP] LUX=" );
  n = NewApp_AppendDec( n, ls.lux );
  n = NewApp_AppendStr( n, " ADC=" );
  n = NewApp_AppendDec( n, (uint16)ls.adc );
  n = NewApp_AppendStr( n, " LEVEL=" );
  n = NewApp_AppendStr( n, NewApp_LevelName( level ) );
  n = NewApp_AppendStr( n, " LAMP=" );
  n = NewApp_AppendStr( n, lamp ? "ON" : "OFF" );
  n = NewApp_AppendStr( n, " MODE=" );
  n = NewApp_AppendStr( n, NewApp_ModeName( NewApp_LampMode ) );
  n = NewApp_AppendStr( n, " cnt=" );
  n = NewApp_AppendDec( n, NewApp_ReportCount );
  n = NewApp_AppendStr( n, ( status == afStatus_SUCCESS ) ? " tx=OK\r\n" : " tx=FAIL\r\n" );
  NewApp_UartSend( n );

  if ( status == afStatus_SUCCESS )
  {
    HalLedBlink( HAL_LED_2, 1, 50, 150 );
  }
  else
  {
    HalLedBlink( HAL_LED_3, 2, 50, 150 );
  }
}

/*********************************************************************
 * @fn      NewApp_ApplyLamp
 *
 * @brief   更新路灯状态并驱动 LED1 作为路灯指示
 *
 * @param   on - 1 开灯，0 关灯
 *
 * @return  none
 */
static void NewApp_ApplyLamp( uint8 on )
{
  NewApp_LampOn = on;
  HalLedSet( HAL_LED_1, on ? HAL_LED_MODE_ON : HAL_LED_MODE_OFF );
}

/*********************************************************************
 * @fn      NewApp_LocalToggle
 *
 * @brief   终端按 KEY1：手动开 / 关本机路灯
 *          以当前路灯状态取反，所以按一下一定换一个状态；
 *          先本地生效（LED1 立刻变化），再上报给网关同步显示。
 *          未入网时也照常动作，方便单独验证按键与 LED。
 *
 * @return  none
 */
static void NewApp_LocalToggle( void )
{
  uint8 on = NewApp_LampOn ? 0 : 1;

  NewApp_LampMode = on ? NEWAPP_CMD_FORCE_ON : NEWAPP_CMD_FORCE_OFF;

  /* 先本地执行，保证按键与 LED 的响应不依赖网络 */
  NewApp_ApplyLamp( on );

  NewApp_UartSend( NewApp_AppendStr( 0,
                    on ? "[APP] KEY1 -> lamp ON (manual)\r\n"
                       : "[APP] KEY1 -> lamp OFF (manual)\r\n" ) );

  /* 再把新状态告诉网关 */
  NewApp_SendReport();
}

/*********************************************************************
 * @fn      NewApp_LocalAuto
 *
 * @brief   终端按摇杆中键：放弃手动，回到按光照自动控制
 *
 * @return  none
 */
static void NewApp_LocalAuto( void )
{
  NewApp_LampMode = NEWAPP_CMD_AUTO;

  NewApp_UartSend( NewApp_AppendStr( 0,
                    "[APP] KEY center -> back to AUTO\r\n" ) );

  NewApp_SendReport();
}

/*********************************************************************
 * @fn      NewApp_LightLevel
 *
 * @brief   按阈值把光照值换算成等级
 *
 * @param   lux - 光照值（lx）
 *
 * @return  NEWAPP_LEVEL_NIGHT / NEWAPP_LEVEL_DUSK / NEWAPP_LEVEL_DAY
 */
static uint8 NewApp_LightLevel( uint16 lux )
{
  if ( lux < NEWAPP_LUX_NIGHT )
  {
    return NEWAPP_LEVEL_NIGHT;
  }

  if ( lux < NEWAPP_LUX_DUSK )
  {
    return NEWAPP_LEVEL_DUSK;
  }

  return NEWAPP_LEVEL_DAY;
}

/*********************************************************************
 * @fn      NewApp_IsJoined
 *
 * @brief   是否已经在网（可作为发送前置条件）
 *
 * @return  TRUE 已入网
 */
static uint8 NewApp_IsJoined( void )
{
  if ( ( NewApp_NwkState == DEV_END_DEVICE ) ||
       ( NewApp_NwkState == DEV_ROUTER )     ||
       ( NewApp_NwkState == DEV_ZB_COORD ) )
  {
    return TRUE;
  }

  return FALSE;
}
#endif /* NEWAPP_IS_TERMINAL */

#if ( NEWAPP_IS_TERMINAL == 0 )
/*********************************************************************
 * @fn      NewApp_GwToggle
 *
 * @brief   网关按 KEY1：手动开 / 关所有路灯
 *          以“终端最近一次上报的路灯状态”取反，所以按一下一定换一个状态：
 *          只要还有路灯亮着就全部关掉，全都灭着就全部打开。
 *
 * @return  none
 */
static void NewApp_GwToggle( void )
{
  uint8 cmd = NewApp_GwLampOn ? NEWAPP_CMD_FORCE_OFF : NEWAPP_CMD_FORCE_ON;
  uint8 n;

  n = 0;
  n = NewApp_AppendStr( n, "[GW] KEY1 manual -> " );
  n = NewApp_AppendStr( n, ( cmd == NEWAPP_CMD_FORCE_ON ) ? "lamp ON\r\n"
                                                          : "lamp OFF\r\n" );
  NewApp_UartSend( n );

  if ( NewApp_SendCtrl( cmd ) > 0 )
  {
    /* 先按期望状态更新，终端回传的真实状态会覆盖它；
       这样即使某一帧上报丢了，连按 KEY1 也能一直切换下去 */
    NewApp_GwLampOn = ( cmd == NEWAPP_CMD_FORCE_ON ) ? 1 : 0;
    HalLedSet( HAL_LED_1, NewApp_GwLampOn ? HAL_LED_MODE_ON : HAL_LED_MODE_OFF );
  }
}

/*********************************************************************
 * @fn      NewApp_GwAuto
 *
 * @brief   网关按摇杆中键：下发 AUTO，让终端回到按光照自动控制
 *
 * @return  none
 */
static void NewApp_GwAuto( void )
{
  NewApp_UartSend( NewApp_AppendStr( 0, "[GW] KEY center -> AUTO\r\n" ) );

  NewApp_SendCtrl( NEWAPP_CMD_AUTO );
}

/*********************************************************************
 * @fn      NewApp_SendCtrl
 *
 * @brief   向所有已入网终端下发路灯控制命令
 *
 * @param   cmd - NEWAPP_CMD_AUTO / FORCE_ON / FORCE_OFF
 *
 * @return  成功下发的终端台数；0 表示没有终端或全部失败
 */
static uint8 NewApp_SendCtrl( uint8 cmd )
{
  afAddrType_t dst;
  afStatus_t   st;
  uint8        data[NEWAPP_CTRL_LEN];
  uint8        i;
  uint8        total = 0;
  uint8        sent  = 0;
  uint8        n;

  for ( i = 0; i < NEWAPP_MAX_TERMINALS; i++ )
  {
    if ( NewApp_TerminalUsed[i] )
    {
      total++;
    }
  }

  if ( total == 0 )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    NewApp_UartSend( NewApp_AppendStr( 0,
                      "[GW] ctrl dropped, no terminal in table\r\n" ) );
    return 0;
  }

  data[0] = cmd;

  for ( i = 0; i < NEWAPP_MAX_TERMINALS; i++ )
  {
    if ( !NewApp_TerminalUsed[i] )
    {
      continue;
    }

    dst.addrMode       = (afAddrMode_t)Addr16Bit;
    dst.endPoint       = NEWAPP_ENDPOINT;
    dst.addr.shortAddr = NewApp_TerminalAddr[i];

    st = AF_DataRequest( &dst,
                         &NewApp_epDesc,
                         NEWAPP_CTRL_CLUSTERID,
                         NEWAPP_CTRL_LEN,
                         data,
                         &NewApp_TransID,
                         AF_DISCV_ROUTE,
                         AF_DEFAULT_RADIUS );

    n = 0;
    n = NewApp_AppendStr( n, "[GW] -> L" );
    n = NewApp_AppendDec( n, (uint16)( i + 1 ) );
    n = NewApp_AppendStr( n, " addr=0x" );
    n = NewApp_AppendHex4( n, NewApp_TerminalAddr[i] );
    n = NewApp_AppendStr( n, " cmd=" );
    n = NewApp_AppendStr( n, NewApp_CmdName( cmd ) );
    n = NewApp_AppendStr( n, ( st == afStatus_SUCCESS ) ? " sent\r\n" : " failed\r\n" );
    NewApp_UartSend( n );

    if ( st == afStatus_SUCCESS )
    {
      sent++;
    }
  }

  if ( sent > 0 )
  {
    HalLedBlink( HAL_LED_2, 1, 50, 100 );
  }
  else
  {
    HalLedBlink( HAL_LED_3, 2, 50, 150 );
  }

  return sent;
}

/*********************************************************************
 * @fn      NewApp_FindTerminal
 *
 * @brief   网关侧维护“终端短地址 -> L1/L2 编号”的映射
 *
 * @param   addr - 终端短地址
 *
 * @return  1..NEWAPP_MAX_TERMINALS；0 表示表已满/未知
 */
static uint8 NewApp_FindTerminal( uint16 addr )
{
  uint8 i;

  for ( i = 0; i < NEWAPP_MAX_TERMINALS; i++ )
  {
    if ( NewApp_TerminalUsed[i] && ( NewApp_TerminalAddr[i] == addr ) )
    {
      return (uint8)( i + 1 );
    }
  }

  for ( i = 0; i < NEWAPP_MAX_TERMINALS; i++ )
  {
    if ( !NewApp_TerminalUsed[i] )
    {
      NewApp_TerminalUsed[i] = TRUE;
      NewApp_TerminalAddr[i] = addr;
      return (uint8)( i + 1 );
    }
  }

  return 0;
}
#endif /* NEWAPP_IS_TERMINAL == 0 */

/*********************************************************************
 * @fn      NewApp_NwkStateChange
 *
 * @brief   网络状态变化：建网/入网成功时给出 LED 与串口提示
 *
 * @return  none
 */
static void NewApp_NwkStateChange( void )
{
  uint8 n;

  switch ( NewApp_NwkState )
  {
    case DEV_ZB_COORD:                     /* 协调器建网成功 */
      HalLedBlink( HAL_LED_1, 3, 50, 300 );
      n = NewApp_AppendStr( 0, "[APP] network formed, gateway ready\r\n" );
      NewApp_UartSend( n );
      break;

    case DEV_ROUTER:
    case DEV_END_DEVICE:                   /* 终端入网成功 */
      HalLedBlink( HAL_LED_2, 3, 50, 300 );
      n = NewApp_AppendStr( 0, "[APP] joined network\r\n" );
      NewApp_UartSend( n );
#if ( NEWAPP_IS_TERMINAL == 1 )
      /* 入网后自动开始周期上报：先 500ms 上第一帧，之后每 3s 一帧 */
      osal_start_timerEx( NewApp_TaskID, NEWAPP_LIGHT_REPORT_EVT,
                          NEWAPP_FIRST_REPORT_DELAY );
#endif
      break;

    default:
      break;
  }
}

/*********************************************************************
 * @fn      NewApp_RoleName
 *
 * @brief   本节点角色名（用于上电打印）
 *
 * @return  字符串常量指针
 */
static const char *NewApp_RoleName( void )
{
#if ( NEWAPP_ROLE == NEWAPP_NODE_COORD )
  return "gateway";
#else
  return "light-terminal";
#endif
}

/*********************************************************************
 * @fn      NewApp_LevelName
 *
 * @brief   光照等级转可读字符串
 *
 * @param   level - 等级
 *
 * @return  字符串常量指针
 */
static const char *NewApp_LevelName( uint8 level )
{
  switch ( level )
  {
    case NEWAPP_LEVEL_NIGHT:
      return "NIGHT";

    case NEWAPP_LEVEL_DUSK:
      return "DUSK";

    default:
      return "DAY";
  }
}

/*********************************************************************
 * @fn      NewApp_CmdName
 *
 * @brief   控制命令转可读字符串
 *
 * @param   cmd - 命令
 *
 * @return  字符串常量指针
 */
static const char *NewApp_CmdName( uint8 cmd )
{
  switch ( cmd )
  {
    case NEWAPP_CMD_FORCE_ON:
      return "FORCE_ON";

    case NEWAPP_CMD_FORCE_OFF:
      return "FORCE_OFF";

    default:
      return "AUTO";
  }
}

/*********************************************************************
 * @fn      NewApp_ModeName
 *
 * @brief   路灯工作模式转可读字符串（串口用，上位机按这个字段判断手动/自动）
 *
 * @param   mode - NEWAPP_CMD_AUTO / FORCE_ON / FORCE_OFF
 *
 * @return  字符串常量指针
 */
static const char *NewApp_ModeName( uint8 mode )
{
  switch ( mode )
  {
    case NEWAPP_CMD_FORCE_ON:
      return "MANUAL_ON";

    case NEWAPP_CMD_FORCE_OFF:
      return "MANUAL_OFF";

    default:
      return "AUTO";
  }
}

/*********************************************************************
 * 串口输出辅助：全部写入缓冲区 NewApp_TxBuf，再一次性发送
 */

static uint8 NewApp_AppendStr( uint8 pos, const char *str )
{
  uint8 ch;

  while ( ( ch = (uint8)( *str++ ) ) != 0 )
  {
    if ( pos >= (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
    {
      break;
    }
    NewApp_TxBuf[pos++] = ch;
  }

  return pos;
}

static uint8 NewApp_AppendDec( uint8 pos, uint16 value )
{
  uint8 digits[5];
  uint8 n = 0;

  do
  {
    digits[n++] = (uint8)( value % 10 );
    value /= 10;
  } while ( value != 0 );

  while ( n > 0 )
  {
    n--;
    if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
    {
      NewApp_TxBuf[pos++] = (uint8)( '0' + digits[n] );
    }
  }

  return pos;
}

static uint8 NewApp_AppendHex4( uint8 pos, uint16 value )
{
  uint8 i;
  uint8 nib;

  for ( i = 0; i < 4; i++ )
  {
    nib = (uint8)( ( value >> ( 12 - ( i * 4 ) ) ) & 0x000F );
    if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
    {
      NewApp_TxBuf[pos++] = (uint8)( ( nib < 10 ) ? ( '0' + nib )
                                                   : ( 'A' + nib - 10 ) );
    }
  }

  return pos;
}

static void NewApp_UartSend( uint8 len )
{
  if ( len > 0 )
  {
    HalUARTWrite( NEWAPP_UART_PORT, NewApp_TxBuf, (uint16)len );
  }
}

/*********************************************************************
*********************************************************************/
