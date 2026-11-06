/**************************************************************************************************
  Filename:       NewApp.c
  Revised:        $Date: 2026-10-20 $

  Description:    ZigBee_NewApp 应用层主程序
                  网络形态：1 个协调器网关 + 2 个终端（温湿度终端、火焰报警终端）

                  角色由编译配置中的宏决定：
                    CoordinatorEB     -> NEWAPP_ROLE_COORDINATOR
                    RouterEB          -> NEWAPP_ROLE_SENSOR_TERM（可选中继节点）
                    SensorTerminalEB  -> NEWAPP_ROLE_SENSOR_TERM
                    FlameTerminalEB   -> NEWAPP_ROLE_FLAME_TERM

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
#elif defined ( NEWAPP_ROLE_SENSOR_TERM )
  #define NEWAPP_ROLE            NEWAPP_NODE_SENSOR
  #define NEWAPP_IS_TERMINAL     1
#elif defined ( NEWAPP_ROLE_FLAME_TERM )
  #define NEWAPP_ROLE            NEWAPP_NODE_FLAME
  #define NEWAPP_IS_TERMINAL     1
#else
  #error "please define NEWAPP_ROLE_COORDINATOR / NEWAPP_ROLE_SENSOR_TERM / NEWAPP_ROLE_FLAME_TERM"
#endif

/*********************************************************************
 * LOCAL VARIABLES
 */
#define NEWAPP_TX_BUF_LEN   96

uint8      NewApp_TaskID;                 /* 本任务 ID */
devStates_t NewApp_NwkState;              /* 网络状态 */
uint8      NewApp_TransID;                /* AF 发送序号 */

#if ( NEWAPP_IS_TERMINAL == 1 )
static uint16 NewApp_ReportCount = 0;     /* 终端：上报次数 */
#endif

#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
static uint8  NewApp_FlameState = 0;      /* 火焰终端：上一次采到的火焰状态 */
#endif

static uint16 NewApp_TerminalAddr[NEWAPP_MAX_TERMINALS];   /* 网关：终端短地址表 */
static uint8  NewApp_TerminalUsed[NEWAPP_MAX_TERMINALS];   /* 网关：表项是否有效 */

static uint8 NewApp_TxBuf[NEWAPP_TX_BUF_LEN];              /* 串口发送缓冲区（xdata） */
static uint8 NewApp_ReportFrame[NEWAPP_REPORT_LEN];        /* 无线上报数据帧 */

const cId_t NewApp_ClusterList[NEWAPP_MAX_CLUSTERS] =
{
  NEWAPP_REPORT_CLUSTERID
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
static uint8  NewApp_AppendStr( uint8 pos, const char *str );
static uint8  NewApp_AppendDec( uint8 pos, uint16 value );
static uint8  NewApp_AppendHex4( uint8 pos, uint16 value );
static uint8  NewApp_AppendTemp( uint8 pos, int16 t10 );
static uint8  NewApp_AppendHum( uint8 pos, uint16 h10 );
static void   NewApp_UartSend( uint8 len );
#if ( NEWAPP_IS_TERMINAL == 1 )
static uint8  NewApp_IsJoined( void );
#endif
static uint8  NewApp_FindTerminal( uint16 addr );
static void   NewApp_NwkStateChange( void );
static void   NewApp_HandleKeys( uint8 shift, uint8 keys );
static void   NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt );
#if ( NEWAPP_IS_TERMINAL == 1 )
static void   NewApp_SendReport( uint8 nodeType );
#endif
#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
static void   NewApp_FlamePoll( void );
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

  NewApp_TaskID      = task_id;
  NewApp_NwkState    = DEV_INIT;
  NewApp_TransID     = 0;

#if ( NEWAPP_IS_TERMINAL == 1 )
  NewApp_ReportCount = 0;
#endif

#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
  NewApp_FlameState  = 0;
#endif

  /* 串口初始化：协调器用它把数据送到上位机，终端也可用来看调试信息 */
  MT_UartInit();
  MT_UartRegisterTaskID( task_id );

  /* 传感器只由终端使用，网关不需要 */
#if ( NEWAPP_IS_TERMINAL == 1 )
  Sensor_Init();
#endif

  /* 上报目的地址：协调器 0x0000，地址模式为 16 位短地址（点对点单播） */
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

#if ( ( NEWAPP_ROLE == NEWAPP_NODE_SENSOR ) && ( NEWAPP_SENSOR_REPORT_PERIOD > 0 ) )
  /* 温湿度终端：周期上报，入网后由入网事件启动 */
  if ( events & NEWAPP_SENSOR_REPORT_EVT )
  {
    NewApp_SendReport( NEWAPP_NODE_SENSOR );

    if ( NewApp_IsJoined() )
    {
      osal_start_timerEx( NewApp_TaskID, NEWAPP_SENSOR_REPORT_EVT,
                          NEWAPP_SENSOR_REPORT_PERIOD );
    }

    return ( events ^ NEWAPP_SENSOR_REPORT_EVT );
  }
#endif

  /* 火焰终端：定时轮询火焰传感器，状态变化立即上报 */
  if ( events & NEWAPP_FLAME_POLL_EVT )
  {
#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
    NewApp_FlamePoll();
    if ( NewApp_IsJoined() )
    {
      osal_start_timerEx( NewApp_TaskID, NEWAPP_FLAME_POLL_EVT,
                          NEWAPP_FLAME_POLL_PERIOD );
    }
#endif
    return ( events ^ NEWAPP_FLAME_POLL_EVT );
  }

  return 0;
}

/*********************************************************************
 * @fn      NewApp_HandleKeys
 *
 * @brief   按键处理
 *          摇杆方向键 / 中键：只做本地 LED 提示，不发起任何无线通信
 *          板载 S1（SW6）：终端触发一次事件上报，网关只做本地提示
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
    HalLedBlink( HAL_LED_1, 2, 50, 500 );
  }

  if ( keys & HAL_KEY_DOWN )
  {
    HalLedBlink( HAL_LED_2, 2, 50, 500 );
  }

  if ( keys & HAL_KEY_LEFT )
  {
    HalLedBlink( HAL_LED_3, 2, 50, 500 );
  }

  if ( keys & HAL_KEY_RIGHT )
  {
    HalLedBlink( HAL_LED_1, 4, 50, 300 );
  }

  if ( keys & HAL_KEY_CENTER )
  {
    HalLedSet( HAL_LED_1, HAL_LED_MODE_OFF );
    HalLedSet( HAL_LED_2, HAL_LED_MODE_OFF );
    HalLedSet( HAL_LED_3, HAL_LED_MODE_OFF );
  }

  if ( keys & HAL_KEY_SW_6 )
  {
#if ( NEWAPP_IS_TERMINAL == 1 )
    /* 终端：读取传感器并上报一次 */
    NewApp_SendReport( NEWAPP_ROLE );
#else
    /* 网关：本地提示，不发起无线通信 */
    HalLedBlink( HAL_LED_2, 1, 50, 200 );
#endif
  }
}

/*********************************************************************
 * @fn      NewApp_MessageMSGCB
 *
 * @brief   收到无线数据的回调：解析上报帧并通过串口输出
 *
 * @param   pkt - 收到的数据包
 *
 * @return  none
 */
static void NewApp_MessageMSGCB( afIncomingMSGPacket_t *pkt )
{
  uint8  *d;
  uint8   nodeType;
  uint8   flame;
  uint8   term;
  uint8   n;
  uint16  addr;
  uint16  count;
  uint16  hum;
  int16   temp;

  if ( pkt->clusterId != NEWAPP_REPORT_CLUSTERID )
  {
    return;
  }

  if ( pkt->cmd.DataLength < NEWAPP_REPORT_LEN )
  {
    return;
  }

  d        = pkt->cmd.Data;
  nodeType = d[NEWAPP_RPT_NODETYPE];
  addr     = (uint16)d[NEWAPP_RPT_ADDR_LO] |
             (uint16)( (uint16)d[NEWAPP_RPT_ADDR_HI] << 8 );
  count    = (uint16)d[NEWAPP_RPT_COUNT_LO] |
             (uint16)( (uint16)d[NEWAPP_RPT_COUNT_HI] << 8 );
  temp     = (int16)( (uint16)d[NEWAPP_RPT_TEMP_LO] |
                      (uint16)( (uint16)d[NEWAPP_RPT_TEMP_HI] << 8 ) );
  hum      = (uint16)d[NEWAPP_RPT_HUM_LO] |
             (uint16)( (uint16)d[NEWAPP_RPT_HUM_HI] << 8 );
  flame    = d[NEWAPP_RPT_FLAME];

  term = NewApp_FindTerminal( addr );

  n = 0;
  n = NewApp_AppendStr( n, "[GW] T" );
  n = NewApp_AppendDec( n, (uint16)term );
  n = NewApp_AppendStr( n, " addr=0x" );
  n = NewApp_AppendHex4( n, addr );
  n = NewApp_AppendStr( n, " cnt=" );
  n = NewApp_AppendDec( n, count );

  if ( nodeType == NEWAPP_NODE_SENSOR )
  {
    n = NewApp_AppendStr( n, " T=" );
    n = NewApp_AppendTemp( n, temp );
    n = NewApp_AppendStr( n, "C H=" );
    n = NewApp_AppendHum( n, hum );
    n = NewApp_AppendStr( n, "%RH" );

    /* 温湿度上报：LED1 闪两下 */
    HalLedBlink( HAL_LED_1, 2, 50, 200 );
  }
  else if ( nodeType == NEWAPP_NODE_FLAME )
  {
    n = NewApp_AppendStr( n, " FLAME=" );
    if ( flame )
    {
      n = NewApp_AppendStr( n, "FIRE" );
      /* 检测到明火：LED3 快闪报警 */
      HalLedBlink( HAL_LED_3, 6, 50, 120 );
    }
    else
    {
      n = NewApp_AppendStr( n, "SAFE" );
      HalLedBlink( HAL_LED_2, 2, 50, 200 );
    }
  }
  else
  {
    /* 未知节点类型，仅输出地址与次数 */
  }

  n = NewApp_AppendStr( n, "\r\n" );
  NewApp_UartSend( n );
}

#if ( NEWAPP_IS_TERMINAL == 1 )
/*********************************************************************
 * @fn      NewApp_SendReport
 *
 * @brief   组帧并通过 AF 层单播给协调器
 *
 * @param   nodeType - NEWAPP_NODE_SENSOR 或 NEWAPP_NODE_FLAME
 *
 * @return  none
 */
static void NewApp_SendReport( uint8 nodeType )
{
  sensorTempHum_t th;
  afStatus_t      status;
  int16           temp  = 0;
  uint16          hum   = 0;
  uint8           flame = 0;
  uint16          addr;
  uint8           n;

  /* 未入网不允许发送，避免无效数据 */
  if ( !NewApp_IsJoined() )
  {
    HalLedBlink( HAL_LED_3, 3, 50, 150 );
    n = NewApp_AppendStr( 0, "[APP] not joined, report dropped\r\n" );
    NewApp_UartSend( n );
    return;
  }

  if ( nodeType == NEWAPP_NODE_SENSOR )
  {
    if ( !Sensor_ReadTempHum( &th ) )
    {
      HalLedBlink( HAL_LED_3, 3, 50, 150 );
      n = NewApp_AppendStr( 0, "[APP] sensor read failed\r\n" );
      NewApp_UartSend( n );
      return;
    }
    temp = th.temperature;
    hum  = th.humidity;
  }
  else
  {
    flame = Sensor_ReadFlame();
  }

  NewApp_ReportCount++;
  addr = NLME_GetShortAddr();

  NewApp_ReportFrame[NEWAPP_RPT_NODETYPE]  = nodeType;
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_LO]   = (uint8)( addr & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_ADDR_HI]   = (uint8)( ( addr >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_LO]  = (uint8)( NewApp_ReportCount & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_COUNT_HI]  = (uint8)( ( NewApp_ReportCount >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_TEMP_LO]   = (uint8)( (uint16)temp & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_TEMP_HI]   = (uint8)( ( (uint16)temp >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_HUM_LO]    = (uint8)( hum & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_HUM_HI]    = (uint8)( ( hum >> 8 ) & 0x00FF );
  NewApp_ReportFrame[NEWAPP_RPT_FLAME]     = flame;

  status = AF_DataRequest( &NewApp_Report_DstAddr,
                           &NewApp_epDesc,
                           NEWAPP_REPORT_CLUSTERID,
                           NEWAPP_REPORT_LEN,
                           NewApp_ReportFrame,
                           &NewApp_TransID,
                           AF_DISCV_ROUTE,
                           AF_DEFAULT_RADIUS );

  if ( status == afStatus_SUCCESS )
  {
    /* 发送成功：LED1 闪一下表示无线数据已发出 */
    HalLedBlink( HAL_LED_1, 1, 50, 200 );
  }
  else
  {
    HalLedBlink( HAL_LED_3, 2, 50, 150 );
    n = NewApp_AppendStr( 0, "[APP] report send failed\r\n" );
    NewApp_UartSend( n );
  }
}
#endif /* NEWAPP_IS_TERMINAL */

#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
/*********************************************************************
 * @fn      NewApp_FlamePoll
 *
 * @brief   轮询火焰传感器：明火状态变化时本地提示并立即上报
 *
 * @return  none
 */
static void NewApp_FlamePoll( void )
{
  uint8 now = Sensor_ReadFlame();

  if ( now == NewApp_FlameState )
  {
    return;
  }

  NewApp_FlameState = now;

  if ( now )
  {
    /* 有火：LED3 持续快闪报警，并立即上报 */
    HalLedBlink( HAL_LED_3, 0, 50, 200 );
  }
  else
  {
    /* 火源消失：熄灭报警灯并上报恢复帧 */
    HalLedSet( HAL_LED_3, HAL_LED_MODE_OFF );
  }

  NewApp_SendReport( NEWAPP_NODE_FLAME );
}
#endif

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
#if ( ( NEWAPP_ROLE == NEWAPP_NODE_SENSOR ) && ( NEWAPP_SENSOR_REPORT_PERIOD > 0 ) )
      /* 入网后启动温湿度周期上报 */
      osal_start_timerEx( NewApp_TaskID, NEWAPP_SENSOR_REPORT_EVT,
                          NEWAPP_SENSOR_REPORT_PERIOD );
#endif
#if ( NEWAPP_ROLE == NEWAPP_NODE_FLAME )
      /* 入网后启动火焰轮询 */
      osal_start_timerEx( NewApp_TaskID, NEWAPP_FLAME_POLL_EVT,
                          NEWAPP_FLAME_POLL_PERIOD );
#endif
      break;

    default:
      break;
  }
}

/*********************************************************************
 * @fn      NewApp_IsJoined
 *
 * @brief   是否已经在网（可作为发送前置条件）
 *
 * @return  TRUE 已入网
 */
#if ( NEWAPP_IS_TERMINAL == 1 )
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
#endif

/*********************************************************************
 * @fn      NewApp_FindTerminal
 *
 * @brief   网关侧维护“终端短地址 -> T1/T2 编号”的映射
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
#elif ( NEWAPP_ROLE == NEWAPP_NODE_SENSOR )
  return "sensor-terminal";
#else
  return "flame-terminal";
#endif
}

/*********************************************************************
 * 串口输出辅助：全部写入 xdata 缓冲区 NewApp_TxBuf，再一次性发送
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

/* 温度：0.1℃ -> 25.6 */
static uint8 NewApp_AppendTemp( uint8 pos, int16 t10 )
{
  uint16 v;

  if ( t10 < 0 )
  {
    if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
    {
      NewApp_TxBuf[pos++] = (uint8)'-';
    }
    v = (uint16)( -t10 );
  }
  else
  {
    v = (uint16)t10;
  }

  pos = NewApp_AppendDec( pos, (uint16)( v / 10 ) );

  if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
  {
    NewApp_TxBuf[pos++] = (uint8)'.';
  }
  if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
  {
    NewApp_TxBuf[pos++] = (uint8)( '0' + ( v % 10 ) );
  }

  return pos;
}

/* 湿度：0.1%RH -> 60.5 */
static uint8 NewApp_AppendHum( uint8 pos, uint16 h10 )
{
  pos = NewApp_AppendDec( pos, (uint16)( h10 / 10 ) );

  if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
  {
    NewApp_TxBuf[pos++] = (uint8)'.';
  }
  if ( pos < (uint8)( NEWAPP_TX_BUF_LEN - 1 ) )
  {
    NewApp_TxBuf[pos++] = (uint8)( '0' + ( h10 % 10 ) );
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
