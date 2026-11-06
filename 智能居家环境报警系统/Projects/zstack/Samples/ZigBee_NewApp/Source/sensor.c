/**************************************************************************************************
  Filename:       sensor.c
  Revised:        $Date: 2026-10-20 $

  Description:    温湿度 / 火焰传感器驱动实现
                  SENSOR_SIMULATE = 1 时全部输出模拟值，不依赖任何外部硬件。
**************************************************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "OSAL.h"
#include "hal_defs.h"
#include "hal_mcu.h"

#include "sensor.h"

/*********************************************************************
 * LOCAL VARIABLES
 */
#if SENSOR_SIMULATE
static int16  sensorSimTemp = 256;      /* 25.6℃ */
static uint16 sensorSimHum  = 605;      /* 60.5%RH */
#endif

/*********************************************************************
 * LOCAL FUNCTIONS
 */
#if !SENSOR_SIMULATE
static void   Sensor_DelayTicks( uint16 ticks );
static void   Sensor_DelayMs( uint16 ms );
static uint8  Sensor_Dht11ReadByte( uint8 *value );
static uint8  Sensor_Dht11Read( sensorTempHum_t *th );
#endif

/*********************************************************************
 * @fn      Sensor_Init
 *
 * @brief   传感器引脚初始化
 *
 * @return  TRUE
 */
uint8 Sensor_Init( void )
{
#if !SENSOR_SIMULATE
  /* DHT11：数据线先设为输入，靠外部上拉保持高电平 */
  SENSOR_DHT11_SEL  &= (uint8)( ~SENSOR_DHT11_BIT );
  SENSOR_DHT11_DIR  &= (uint8)( ~SENSOR_DHT11_BIT );
  SENSOR_DHT11_PORT |= SENSOR_DHT11_BIT;

  /* 火焰传感器：数字量输入 */
  SENSOR_FLAME_SEL &= (uint8)( ~SENSOR_FLAME_BIT );
  SENSOR_FLAME_DIR &= (uint8)( ~SENSOR_FLAME_BIT );
#endif

  return TRUE;
}

/*********************************************************************
 * @fn      Sensor_ReadTempHum
 *
 * @brief   读取一次温湿度
 *
 * @param   th - 输出：温度（0.1℃）/ 湿度（0.1%RH）
 *
 * @return  TRUE 成功
 */
uint8 Sensor_ReadTempHum( sensorTempHum_t *th )
{
  if ( th == 0 )
  {
    return FALSE;
  }

#if SENSOR_SIMULATE
  /* 模拟值缓慢漂移，方便观察网关输出 */
  sensorSimTemp += 1;
  if ( sensorSimTemp > 300 )
  {
    sensorSimTemp = 240;
  }

  sensorSimHum -= 3;
  if ( sensorSimHum < 450 )
  {
    sensorSimHum = 650;
  }

  th->temperature = sensorSimTemp;
  th->humidity    = sensorSimHum;

  return TRUE;
#else
  return Sensor_Dht11Read( th );
#endif
}

/*********************************************************************
 * @fn      Sensor_ReadFlame
 *
 * @brief   读取火焰传感器数字输出
 *
 * @return  1 检测到火焰；0 无火焰
 */
uint8 Sensor_ReadFlame( void )
{
#if SENSOR_SIMULATE
  /* 模拟：每 30s 中有 5s 处于“有火”状态，便于演示自动上报 */
  uint32 t = osal_GetSystemClock() % 30000UL;

  return (uint8)( t >= 25000UL );
#else
  uint8 level;

  level = ( SENSOR_FLAME_PORT & SENSOR_FLAME_BIT ) ? 1 : 0;

  #if ( SENSOR_FLAME_ACTIVE_HIGH == 1 )
  return level;
  #else
  return (uint8)( level ? 0 : 1 );
  #endif
#endif
}

#if !SENSOR_SIMULATE
/*********************************************************************
 * 真实 DHT11 驱动
 * 说明：单总线时序对延时敏感度较高，Sensor_DelayTicks() 的循环次数
 *       需要在 32MHz 主频下按实测校准（示波器或逐次试读）。
 */

/* 约 1us / 8 ticks 的粗略忙等，按实测调整循环次数 */
static void Sensor_DelayTicks( uint16 ticks )
{
  volatile uint16 i;

  for ( i = 0; i < ticks; i++ )
  {
  }
}

/* 毫秒级忙等：32MHz 下约 1ms 对应 1000 个内层 tick，需按实测校准 */
static void Sensor_DelayMs( uint16 ms )
{
  while ( ms-- )
  {
    Sensor_DelayTicks( 1000 );
  }
}

/* 读一个字节：8 位，高位在前 */
static uint8 Sensor_Dht11ReadByte( uint8 *value )
{
  uint8  i;
  uint8  b = 0;
  uint16 guard;

  for ( i = 0; i < 8; i++ )
  {
    /* 等待 50us 低电平结束 */
    guard = 0;
    while ( ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT ) == 0 )
    {
      if ( ++guard > 3000 )
      {
        return FALSE;
      }
    }

    /* 高电平持续 26~28us 为 0，约 70us 为 1 */
    Sensor_DelayTicks( 30 );

    b <<= 1;
    if ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT )
    {
      b |= 0x01;
      guard = 0;
      while ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT )
      {
        if ( ++guard > 3000 )
        {
          return FALSE;
        }
      }
    }
  }

  *value = b;

  return TRUE;
}

/* 完整读一帧：湿度整数/小数、温度整数/小数、校验和 */
static uint8 Sensor_Dht11Read( sensorTempHum_t *th )
{
  uint8  data[5];
  uint8  i;
  uint8  chk;
  uint16 guard;
  halIntState_t intState;

  data[0] = 0;
  data[1] = 0;
  data[2] = 0;
  data[3] = 0;
  data[4] = 0;

  /* 起始信号：主机拉低 >= 18ms */
  SENSOR_DHT11_DIR  |= SENSOR_DHT11_BIT;
  SENSOR_DHT11_PORT &= (uint8)( ~SENSOR_DHT11_BIT );
  Sensor_DelayMs( 20 );

  /* 释放总线，改为输入 */
  SENSOR_DHT11_PORT |= SENSOR_DHT11_BIT;
  SENSOR_DHT11_DIR  &= (uint8)( ~SENSOR_DHT11_BIT );

  HAL_ENTER_CRITICAL_SECTION( intState );

  /* DHT11 响应：80us 低 + 80us 高 */
  guard = 0;
  while ( ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT ) != 0 )
  {
    if ( ++guard > 3000 )
    {
      HAL_EXIT_CRITICAL_SECTION( intState );
      return FALSE;
    }
  }

  guard = 0;
  while ( ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT ) == 0 )
  {
    if ( ++guard > 3000 )
    {
      HAL_EXIT_CRITICAL_SECTION( intState );
      return FALSE;
    }
  }

  guard = 0;
  while ( ( SENSOR_DHT11_PORT & SENSOR_DHT11_BIT ) != 0 )
  {
    if ( ++guard > 3000 )
    {
      HAL_EXIT_CRITICAL_SECTION( intState );
      return FALSE;
    }
  }

  for ( i = 0; i < 5; i++ )
  {
    if ( !Sensor_Dht11ReadByte( &data[i] ) )
    {
      HAL_EXIT_CRITICAL_SECTION( intState );
      return FALSE;
    }
  }

  HAL_EXIT_CRITICAL_SECTION( intState );

  chk = (uint8)( data[0] + data[1] + data[2] + data[3] );
  if ( chk != data[4] )
  {
    return FALSE;
  }

  th->humidity    = (uint16)( ( data[0] * 10 ) + data[1] );
  th->temperature = (int16)( ( data[2] * 10 ) + data[3] );

  return TRUE;
}
#endif /* !SENSOR_SIMULATE */

/*********************************************************************
*********************************************************************/
