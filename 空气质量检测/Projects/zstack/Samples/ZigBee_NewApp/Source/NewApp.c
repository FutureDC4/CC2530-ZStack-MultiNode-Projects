/**************************************************************************************************
  Filename:       NewApp.c
  Revised:        $Date: 2026-10-22 $

  Description:    ZigBee_NewApp 应用层主程序
                  项目：基于 ZigBee 的空气质量检测系统（烟雾传感器）
                  网络形态：1 个协调器网关 + 1 个烟雾终端，星型

                  工作方式：
                    烟雾终端上电后先自检一次并把传感器状态打印到串口（不依赖组网），
                    入网后按周期采集烟雾浓度并单播上报网关；
                    按终端 KEY1 可以随时手动补报一帧（SRC=KEY）；
                    按网关 KEY1 会通过控制簇请求终端立即上报（SRC=REMOTE）；
                    网关解析上报帧后打印到串口，并按空气质量等级驱动 LED 报警；
                    浓度进入"严重"档时终端与网关的 LED1 连续闪烁，回到正常后自动停止。

                  串口输出示例：
                    [APP] ZigBee_NewApp gateway started
                    [APP] network formed, gateway ready
                    [APP] smoke sensor ready, SMOKE=132 ADC=34 LEVEL=NORMAL
                    [GW] S1 addr=0x796F cnt=1 SMOKE=132 ADC=34 LEVEL=NORMAL SRC=PERIODIC
                    [GW] KEY1 report request -> 1 terminal(s)
                    [GW] -> S1 addr=0x796F cmd=REPORT_NOW sent
                    [GW] S1 addr=0x796F cnt=2 SMOKE=742 ADC=189 LEVEL=DANGER SRC=REMOTE

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR（协调器网关）
                    SmokeTerminalEB   -> NEWAPP_ROLE_SMOKE_TERM （烟雾终端）
                    RouterEB          -> NEWAPP_ROLE_SMOKE_TERM （可选中继）

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
#elif defined ( NEWAPP_ROLE_SMOKE_TERM )
  #define NEWAPP_ROLE            NEWAPP_NODE_SMOKE
  #define NEWAPP_IS_TERMINAL     1
#else
  #error "please define NEWAPP_ROLE_COORDINATOR / NEWAPP_ROLE_SMOKE_TERM"
#endif

/*********************************************************************
 * LOCAL VARIABLES
 */
#define NEWAPP_TX_BUF_LEN   128

uint8      NewApp_TaskID;                 /* 本任务 ID */
devStates_t NewApp_NwkState;              /* 网络状态 */
uint8      NewApp_TransID;                /* AF 发送序号 */

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint16 NewApp_ReportCount = 0;     /* 终端：累计上报次数 */
static uint8  NewApp_LastLevel   = 0xFF;  /* 终端：上一次的等级，用于只在变化时报警 */
#else
static uint16 NewApp_TerminalAddr[NEWAPP_MAX_TERMINALS];   /* 网关：终端短地址表 */
static uint8  NewApp_TerminalUsed[NEWAPP_MAX_TERMINALS];   /* 网关：表项是否有效 */
static uint8  NewApp_GwLevel     = 0xFF;  /* 网关：当前报警等级 */
#endif

static uint8 NewApp_TxBuf[NEWAPP_TX_BUF_LEN];              /* 串口发送缓冲区 */

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint8 NewApp_ReportFrame[NEWAPP_REPORT_LEN];        /* 无线上报数据帧 */
#endif

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
static const char *NewApp_SrcName( uint8 src );
static const char *NewApp_CmdName( uint8 cmd );
static uint8  NewApp_AppendStr( uint8 pos, const char *str );
static uint8  NewApp_AppendDec( uint8 pos, uint16 value );
static uint8  NewApp_AppendHex4( uint8 pos, uint16 value );
static void   NewApp_UartSend( uint8 len );
static void   NewApp_NwkStateChange( void );
static void   NewApp_HandleKeys( uint8 shift, uint8 keys );
static void   NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt );

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint8  NewApp_IsJoined( void );
static uint8  NewApp_SmokeLevel( uint16 density );
static void   NewApp_ApplyAlarm( uint8 level );
static void   NewApp_SendReport( uint8 src );
static void   NewApp_SelfCheck( void );
#else
static uint8  NewApp_FindTerminal( uint16 addr );
static void   NewApp_GwAlarm( uint8 level );
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

  NewApp_TaskID   = task_id;
  NewApp_NwkState = DEV_INIT;
  NewApp_TransID  = 0;

#if ( NEWAPP_IS_TERMINAL == 1 )
  NewApp_ReportCount = 0;
  NewApp_LastLevel   = 0xFF;
#else
  NewApp_GwLevel     = 0xFF;
#endif

  /* 串口初始化：网关用它把数据送到上位机，终端也可用来看调试信息 */
  MT_UartInit();
  MT_UartRegisterTaskID( task_id );

  /* 传感器只由终端使用，网关不需要 */
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
  /* 上电立刻自检一次传感器：不依赖入网，单独接串口就能确认硬件是否正常 */
  NewApp_SelfCheck();
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
  /* 终端：周期采集并上报，入网后由入网事件启动 */
  if ( events & NEWAPP_REPORT_EVT )
  {
    NewApp_SendReport( NEWAPP_SRC_PERIODIC );

    if ( NewApp_IsJoined() )
    {
      osal_start_timerEx( NewApp_TaskID, NEWAPP_REPORT_EVT,
                          NEWAPP_REPORT_PERIOD );
    }

    return ( events ^ NEWAPP_REPORT_EVT );
  }
#endif

  return 0;
}

/*********************************************************************
 * @fn      NewApp_HandleKeys
 *
 * @brief   按键处理
 *          摇杆方向键 / 中键：只做本地 LED 提示，不发起任何无线通信
 *                             （LED1 留给报警，方向键只闪 LED3）
 *          板载 S1（SW6）   ：终端立即手动上报一帧；
 *                             网关向已入网终端请求立即上报
 *
 * @param   shift - 组合键状态（未使用）
 * @param   keys  - 按键位图
 *
 * @return  none
 */
static void NewApp_HandleKeys( uint8 shift, uint8 keys )
{
  (void)shift;

  if ( keys & HAL_KEY_UP )
  {
    HalLedBlink( HAL_LED_3, 1, 50, 200 );
  }

  if ( keys & HAL_KEY_DOWN )
  {
    HalLedBlink( HAL_LED_3, 2, 50, 200 );
  }

  if ( keys & HAL_KEY_LEFT )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 200 );
  }

  if ( keys & HAL_KEY_RIGHT )
  {
    HalLedBlink( HAL_LED_3, 4, 50, 150 );
  }

  if ( keys & HAL_KEY_CENTER )
  {
    /* 清掉本地提示灯，但不动报警灯 LED1 */
    HalLedSet( HAL_LED_2, HAL_LED_MODE_OFF );
    HalLedSet( HAL_LED_3, HAL_LED_MODE_OFF );
  }

  if ( keys & HAL_KEY_SW_6 )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    /* 终端 KEY1：不等周期，立刻采集并上报一帧 */
    NewApp_UartSend( NewApp_AppendStr( 0, "[APP] KEY1 manual report\r\n" ) );
    NewApp_SendReport( NEWAPP_SRC_KEY );
#else
    /* 网关 KEY1：请求所有已入网终端立即上报 */
    {
      uint8 cnt;

      cnt = NewApp_SendCtrl( NEWAPP_CMD_REPORT_NOW );

      if ( cnt > 0 )
      {
        uint8 n = 0;

        n = NewApp_AppendStr( n, "[GW] KEY1 report request -> " );
        n = NewApp_AppendDec( n, (uint16)cnt );
        n = NewApp_AppendStr( n, " terminal(s)\r\n" );
        NewApp_UartSend( n );
      }
    }
#endif
  }
}

/*********************************************************************
 * @fn      NewApp_MessageMSGCB
 *
 * @brief   收到无线数据的回调
 *          网关：解析上报帧并通过串口输出
 *          终端：解析控制帧，收到 REPORT_NOW 就立即上报一帧
 *
 * @param   pkt - 收到的数据包
 *
 * @return  none
 */
static void NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt )
{
#if ( NEWAPP_IS_TERMINAL == 1 )
  /* ---------------------------------------------------------- 终端侧 */
  {
    uint8 cmd;
    uint8 n;

    if ( pkt->clusterId != NEWAPP_CTRL_CLUSTERID )
    {
      return;
    }

    if ( pkt->cmd.DataLength < NEWAPP_CTRL_LEN )
    {
      return;
    }

    cmd = pkt->cmd.Data[0];

    n = 0;
    n = NewApp_AppendStr( n, "[APP] ctrl cmd=" );
    n = NewApp_AppendStr( n, NewApp_CmdName( cmd ) );
    n = NewApp_AppendStr( n, "\r\n" );
    NewApp_UartSend( n );

    if ( cmd == NEWAPP_CMD_REPORT_NOW )
    {
      /* 网关请求：立即补报一帧，帧内 SRC 标记为 REMOTE */
      NewApp_SendReport( NEWAPP_SRC_REMOTE );
    }
  }
#else
  /* ---------------------------------------------------------- 网关侧 */
  {
    uint8  *d;
    uint8   adc;
    uint8   level;
    uint8   src;
    uint8   term;
    uint8   n;
    uint16  addr;
    uint16  count;
    uint16  density;

    if ( pkt->clusterId != NEWAPP_REPORT_CLUSTERID )
    {
      return;
    }

    if ( pkt->cmd.DataLength < NEWAPP_REPORT_LEN )
    {
      return;
    }

    d = pkt->cmd.Data;

    if ( d[NEWAPP_RPT_NODETYPE] != NEWAPP_NODE_SMOKE )
    {
      return;
    }

    addr  = (uint16)d[NEWAPP_RPT_ADDR_LO] |
            (uint16)( (uint16)d[NEWAPP_RPT_ADDR_HI] << 8 );
    count = (uint16)d[NEWAPP_RPT_COUNT_LO] |
            (uint16)( (uint16)d[NEWAPP_RPT_COUNT_HI] << 8 );
    density = (uint16)d[NEWAPP_RPT_SMOKE_LO] |
              (uint16)( (uint16)d[NEWAPP_RPT_SMOKE_HI] << 8 );

    adc   = d[NEWAPP_RPT_ADC];
    level = d[NEWAPP_RPT_LEVEL];
    src   = d[NEWAPP_RPT_SRC];

    if ( level > NEWAPP_LEVEL_DANGER )
    {
      level = NEWAPP_LEVEL_DANGER;
    }

    /* 按终端短地址首次出现顺序编号 S1 / S2 */
    term = NewApp_FindTerminal( addr );

    /* [GW] S1 addr=0x796F cnt=3 SMOKE=132 ADC=34 LEVEL=NORMAL SRC=PERIODIC */
    n = 0;
    n = NewApp_AppendStr( n, "[GW] S" );
    n = NewApp_AppendDec( n, (uint16)term );
    n = NewApp_AppendStr( n, " addr=0x" );
    n = NewApp_AppendHex4( n, addr );
    n = NewApp_AppendStr( n, " cnt=" );
    n = NewApp_AppendDec( n, count );
    n = NewApp_AppendStr( n, " SMOKE=" );
    n = NewApp_AppendDec( n, density );
    n = NewApp_AppendStr( n, " ADC=" );
    n = NewApp_AppendDec( n, (uint16)adc );
    n = NewApp_AppendStr( n, " LEVEL=" );
    n = NewApp_AppendStr( n, NewApp_LevelName( level ) );
    n = NewApp_AppendStr( n, " SRC=" );
    n = NewApp_AppendStr( n, NewApp_SrcName( src ) );
    n = NewApp_AppendStr( n, "\r\n" );
    NewApp_UartSend( n );

    /* 网关 LED 报警：严重连续闪，超标常亮，其余熄灭 */
    NewApp_GwAlarm( level );

    /* 每收到一帧：LED2 闪一下，表示无线链路正常 */
    HalLedBlink( HAL_LED_2, 1, 50, 100 );
  }
#endif
}

#if ( NEWAPP_IS_TERMINAL == 1 )
/*********************************************************************
 * @fn      NewApp_SelfCheck
 *
 * @brief   上电自检：读一次传感器并把结果打印到串口
 *          这样不组网、只接串口也能确认传感器接线是否正常
 *
 * @return  none
 */
static void NewApp_SelfCheck( void )
{
  sensorSmoke_t sm;
  uint8         n;

  if ( Sensor_ReadSmoke( &sm ) )
  {
    n = 0;
    n = NewApp_AppendStr( n, "[APP] smoke sensor ready, SMOKE=" );
    n = NewApp_AppendDec( n, sm.density );
    n = NewApp_AppendStr( n, " ADC=" );
    n = NewApp_AppendDec( n, (uint16)sm.adc );
    n = NewApp_AppendStr( n, " LEVEL=" );
    n = NewApp_AppendStr( n, NewApp_LevelName( NewApp_SmokeLevel( sm.density ) ) );
    n = NewApp_AppendStr( n, "\r\n" );
    NewApp_UartSend( n );
  }
  else
  {
    n = NewApp_AppendStr( 0, "[APP] smoke sensor not ready\r\n" );
    NewApp_UartSend( n );
  }
}

/*********************************************************************
 * @fn      NewApp_SendReport
 *
 * @brief   采集烟雾浓度、组帧并通过 AF 层单播给协调器
 *
 * @param   src - 上报方式 NEWAPP_SRC_PERIODIC / KEY / REMOTE
 *
 * @return  none
 */
static void NewApp_SendReport( uint8 src )
{
  sensorSmoke_t sm;
  afStatus_t    status;
  uint8         level;
  uint16        addr;
  uint8         n;

  /* 未入网不允许发送，避免无效数据 */
  if ( !NewApp_IsJoined() )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    n = NewApp_AppendStr( 0, "[APP] not joined, report dropped\r\n" );
    NewApp_UartSend( n );
    return;
  }

  if ( !Sensor_ReadSmoke( &sm ) )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    n = NewApp_AppendStr( 0, "[APP] smoke sensor read failed\r\n" );
    NewApp_UartSend( n );
    return;
  }

  level = NewApp_SmokeLevel( sm.density );

  NewApp_ReportCount++;
  addr = NLME_GetShortAddr();

  NewApp_ReportFrame[NEWAPP_RPT_NODETYPE] = NEWAPP_NODE_SMOKE;
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_LO]  = (uint8)( addr & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_HI]  = (uint8)( ( addr >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_LO] = (uint8)( NewApp_ReportCount & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_HI] = (uint8)( ( NewApp_ReportCount >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_SMOKE_LO] = (uint8)( sm.density & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_SMOKE_HI] = (uint8)( ( sm.density >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_ADC]      = sm.adc;
  NewApp_ReportFrame[NEWAPP_RPT_LEVEL]    = level;
  NewApp_ReportFrame[NEWAPP_RPT_SRC]      = src;

  status = AF_DataRequest( &NewApp_Report_DstAddr,
                           &NewApp_epDesc,
                           NEWAPP_REPORT_CLUSTERID,
                           NEWAPP_REPORT_LEN,
                           NewApp_ReportFrame,
                           &NewApp_TransID,
                           AF_DISCV_ROUTE,
                           AF_DEFAULT_RADIUS );

  /* 本地也打印一帧，串口直接接在终端上就能看到采集与发送结果 */
  n = 0;
  n = NewApp_AppendStr( n, "[APP] SMOKE=" );
  n = NewApp_AppendDec( n, sm.density );
  n = NewApp_AppendStr( n, " ADC=" );
  n = NewApp_AppendDec( n, (uint16)sm.adc );
  n = NewApp_AppendStr( n, " LEVEL=" );
  n = NewApp_AppendStr( n, NewApp_LevelName( level ) );
  n = NewApp_AppendStr( n, " cnt=" );
  n = NewApp_AppendDec( n, NewApp_ReportCount );
  n = NewApp_AppendStr( n, " src=" );
  n = NewApp_AppendStr( n, NewApp_SrcName( src ) );
  n = NewApp_AppendStr( n, ( status == afStatus_SUCCESS ) ? " tx=OK\r\n"
                                                          : " tx=FAIL\r\n" );
  NewApp_UartSend( n );

  /* 报警只在等级变化时提示一次，避免每帧都刷串口 */
  if ( level != NewApp_LastLevel )
  {
    NewApp_LastLevel = level;
    NewApp_ApplyAlarm( level );
  }

  if ( status == afStatus_SUCCESS )
  {
    HalLedBlink( HAL_LED_2, 1, 50, 200 );
  }
  else
  {
    HalLedBlink( HAL_LED_3, 2, 50, 150 );
  }
}

/*********************************************************************
 * @fn      NewApp_SmokeLevel
 *
 * @brief   按阈值把烟雾浓度换算成空气质量等级
 *
 * @param   density - 烟雾浓度（等效 ppm）
 *
 * @return  NEWAPP_LEVEL_CLEAN / NORMAL / WARN / DANGER
 */
static uint8 NewApp_SmokeLevel( uint16 density )
{
  if ( density >= NEWAPP_SMOKE_DANGER )
  {
    return NEWAPP_LEVEL_DANGER;
  }

  if ( density >= NEWAPP_SMOKE_WARN )
  {
    return NEWAPP_LEVEL_WARN;
  }

  if ( density >= NEWAPP_SMOKE_NORMAL )
  {
    return NEWAPP_LEVEL_NORMAL;
  }

  return NEWAPP_LEVEL_CLEAN;
}

/*********************************************************************
 * @fn      NewApp_ApplyAlarm
 *
 * @brief   终端本地声光报警
 *          DANGER：LED1 连续闪烁 + 串口告警
 *          WARN  ：LED1 常亮
 *          其余  ：LED1 熄灭
 *
 * @param   level - 空气质量等级
 *
 * @return  none
 */
static void NewApp_ApplyAlarm( uint8 level )
{
  if ( level == NEWAPP_LEVEL_DANGER )
  {
    /* numBlinks 传 0 表示无限闪烁（hal_led.c 中会置 FLASH 模式） */
    HalLedBlink( HAL_LED_1, 0, 50, 250 );
    NewApp_UartSend( NewApp_AppendStr( 0, "[APP] SMOKE ALARM! level=DANGER\r\n" ) );
  }
  else if ( level == NEWAPP_LEVEL_WARN )
  {
    HalLedSet( HAL_LED_1, HAL_LED_MODE_ON );
    NewApp_UartSend( NewApp_AppendStr( 0, "[APP] smoke level=WARN, please ventilate\r\n" ) );
  }
  else
  {
    HalLedSet( HAL_LED_1, HAL_LED_MODE_OFF );
  }
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
 * @fn      NewApp_FindTerminal
 *
 * @brief   网关侧维护“终端短地址 -> S1/S2 编号”的映射
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

/*********************************************************************
 * @fn      NewApp_GwAlarm
 *
 * @brief   网关 LED 报警，只在等级变化时动作，避免每帧重设 LED
 *
 * @param   level - 空气质量等级
 *
 * @return  none
 */
static void NewApp_GwAlarm( uint8 level )
{
  if ( level == NewApp_GwLevel )
  {
    return;
  }

  NewApp_GwLevel = level;

  if ( level == NEWAPP_LEVEL_DANGER )
  {
    HalLedBlink( HAL_LED_1, 0, 50, 250 );
    HalLedSet( HAL_LED_3, HAL_LED_MODE_ON );
  }
  else if ( level == NEWAPP_LEVEL_WARN )
  {
    HalLedSet( HAL_LED_1, HAL_LED_MODE_ON );
    HalLedSet( HAL_LED_3, HAL_LED_MODE_OFF );
  }
  else
  {
    HalLedSet( HAL_LED_1, HAL_LED_MODE_OFF );
    HalLedSet( HAL_LED_3, HAL_LED_MODE_OFF );
  }
}

/*********************************************************************
 * @fn      NewApp_SendCtrl
 *
 * @brief   向所有已入网终端下发控制命令（控制簇，1 字节）
 *
 * @param   cmd - NEWAPP_CMD_REPORT_NOW
 *
 * @return  发送成功的终端个数
 */
static uint8 NewApp_SendCtrl( uint8 cmd )
{
  afAddrType_t dst;
  afStatus_t   st;
  uint8        data[NEWAPP_CTRL_LEN];
  uint8        ok = 0;
  uint8        i;
  uint8        n;

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
    n = NewApp_AppendStr( n, "[GW] -> S" );
    n = NewApp_AppendDec( n, (uint16)( i + 1 ) );
    n = NewApp_AppendStr( n, " addr=0x" );
    n = NewApp_AppendHex4( n, NewApp_TerminalAddr[i] );
    n = NewApp_AppendStr( n, " cmd=" );
    n = NewApp_AppendStr( n, NewApp_CmdName( cmd ) );
    n = NewApp_AppendStr( n, ( st == afStatus_SUCCESS ) ? " sent\r\n"
                                                        : " failed\r\n" );
    NewApp_UartSend( n );

    if ( st == afStatus_SUCCESS )
    {
      ok++;
    }
  }

  if ( ok == 0 )
  {
    NewApp_UartSend( NewApp_AppendStr( 0,
                      "[GW] ctrl dropped, no terminal in table\r\n" ) );
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
  }

  return ok;
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
      /* 入网后启动周期上报：先 500ms 上第一帧，之后每 5s 一帧 */
      osal_start_timerEx( NewApp_TaskID, NEWAPP_REPORT_EVT,
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
  return "smoke-terminal";
#endif
}

/*********************************************************************
 * @fn      NewApp_LevelName
 *
 * @brief   空气质量等级转可读字符串
 *
 * @param   level - 等级
 *
 * @return  字符串常量指针
 */
static const char *NewApp_LevelName( uint8 level )
{
  switch ( level )
  {
    case NEWAPP_LEVEL_CLEAN:
      return "CLEAN";

    case NEWAPP_LEVEL_NORMAL:
      return "NORMAL";

    case NEWAPP_LEVEL_WARN:
      return "WARN";

    default:
      return "DANGER";
  }
}

/*********************************************************************
 * @fn      NewApp_SrcName
 *
 * @brief   上报方式转可读字符串（上位机据此区分周期上报与手动上报）
 *
 * @param   src - NEWAPP_SRC_PERIODIC / KEY / REMOTE
 *
 * @return  字符串常量指针
 */
static const char *NewApp_SrcName( uint8 src )
{
  switch ( src )
  {
    case NEWAPP_SRC_KEY:
      return "KEY";

    case NEWAPP_SRC_REMOTE:
      return "REMOTE";

    default:
      return "PERIODIC";
  }
}

/*********************************************************************
 * @fn      NewApp_CmdName
 *
 * @brief   控制命令转可读字符串
 *
 * @param   cmd - 命令码
 *
 * @return  字符串常量指针
 */
static const char *NewApp_CmdName( uint8 cmd )
{
  if ( cmd == NEWAPP_CMD_REPORT_NOW )
  {
    return "REPORT_NOW";
  }

  return "UNKNOWN";
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
