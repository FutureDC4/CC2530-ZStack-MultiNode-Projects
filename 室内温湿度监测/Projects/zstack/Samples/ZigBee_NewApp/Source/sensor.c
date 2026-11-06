/**************************************************************************************************
  Filename:       sensor.c
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动实现（室内温湿度监测）
                  温湿度：DHT11 单总线
                  SENSOR_SIMULATE 为 1 时输出模拟值，不依赖任何外部硬件。
**************************************************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "OSAL.h"
#include "hal_defs.h"
#include "hal_mcu.h"

#include "sensor.h"

/*********************************************************************
 * LOCAL CONSTANTS
 */

/* 模拟曲线周期：2 分钟走完一轮升温 / 降温，方便观察数值连续变化 */
#define SENSOR_SIM_PERIOD_MS     120000UL

/*********************************************************************
 * LOCAL VARIABLES
 */

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
#endif

  return TRUE;
}

/*********************************************************************
 * @fn      Sensor_ReadTempHum
 *
 * @brief   读取一次室内空气温湿度
 *
 * @param   th - 输出：温度（0.1℃）/ 湿度（0.1%RH）
 *
 * @return  TRUE 成功
 */
uint8 Sensor_ReadTempHum( sensorTempHum_t *th )
{
#if SENSOR_SIMULATE
  uint32 t;
  uint32 k;
  uint32 half = SENSOR_SIM_PERIOD_MS / 2;

  if ( th == 0 )
  {
    return FALSE;
  }

  /* 三角波：0 -> 周期一半 -> 0，模拟室内温度缓慢升降 */
  t = osal_GetSystemClock() % SENSOR_SIM_PERIOD_MS;

  if ( t <= half )
  {
    k = t;
  }
  else
  {
    k = SENSOR_SIM_PERIOD_MS - t;
  }

  /* 温度 22.0 ~ 31.0℃：覆盖"适宜"与"偏高"两种状态 */
  th->temperature = (int16)( 220UL + ( k * 90UL ) / half );

  /* 湿度与温度反相 68.0 ~ 42.0%RH：覆盖"适宜"与"偏干"两种状态 */
  th->humidity = (uint16)( 680UL - ( k * 260UL ) / half );

  /* 轻微抖动，避免每次上报数字完全相同 */
  th->temperature += (int16)( osal_GetSystemClock() % 3 );
  th->humidity    += (uint16)( osal_GetSystemClock() % 5 );

  return TRUE;
#else
  if ( th == 0 )
  {
    return FALSE;
  }

  return Sensor_Dht11Read( th );
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
