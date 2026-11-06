/**************************************************************************************************
  Filename:       sensor.c
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动实现（大棚环境监测）
                  温湿度：DHT11 单总线；光照：光敏电阻（ADC 模拟量 / IO 开关量）
                  SENSOR_SIMULATE / SENSOR_LIGHT_SIMULATE 为 1 时输出模拟值，
                  不依赖任何外部硬件。
**************************************************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "OSAL.h"
#include "hal_defs.h"
#include "hal_mcu.h"
#include "hal_adc.h"

#include "sensor.h"

/*********************************************************************
 * LOCAL CONSTANTS
 */

/* 光照模拟曲线：60 秒走完一个昼夜，方便演示大棚光照由暗到亮再到暗的过程 */
#define SENSOR_SIM_DAY_MS        60000UL

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
 * @brief   传感器引脚与采样通道初始化
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

#if ( SENSOR_LIGHT_SIMULATE == 0 )

  #if ( SENSOR_LIGHT_USE_ADC == 1 )
    /* 模拟量输入：关数字功能、方向设为输入、置三态交给 ADC 采样 */
    SENSOR_LIGHT_ADC_SEL &= (uint8)( ~SENSOR_LIGHT_ADC_BIT );
    SENSOR_LIGHT_ADC_DIR &= (uint8)( ~SENSOR_LIGHT_ADC_BIT );
    SENSOR_LIGHT_ADC_INP |= SENSOR_LIGHT_ADC_BIT;

    /* 空读一次，让 ADC 通道与引脚建立稳定的电平 */
    (void)HalAdcRead( SENSOR_LIGHT_ADC_CHN, HAL_ADC_RESOLUTION_8 );
  #else
    /* 开关量输入：数字功能关闭、方向设为输入、打开上拉 */
    SENSOR_LIGHT_DO_SEL &= (uint8)( ~SENSOR_LIGHT_DO_BIT );
    SENSOR_LIGHT_DO_DIR &= (uint8)( ~SENSOR_LIGHT_DO_BIT );
    SENSOR_LIGHT_DO_INP &= (uint8)( ~SENSOR_LIGHT_DO_BIT );
  #endif

#endif

  return TRUE;
}

/*********************************************************************
 * @fn      Sensor_ReadTempHum
 *
 * @brief   读取一次空气温湿度
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
  /* 模拟值在大棚常见区间内缓慢漂移，方便观察网关与上位机的变化：
     温度 24.0 ~ 30.0℃，湿度 45.0 ~ 65.0%RH */
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
 * @fn      Sensor_ReadLight
 *
 * @brief   读取一次光照强度
 *
 * @param   ls - 输出：光照值（lx）与原始采样值
 *
 * @return  TRUE 成功
 */
uint8 Sensor_ReadLight( sensorLight_t *ls )
{
  uint32 lux = 0;
  uint32 t;
  uint8  adc = 0;

  if ( ls == 0 )
  {
    return FALSE;
  }

#if ( SENSOR_LIGHT_SIMULATE == 1 )

  /* 按开机时间推算当前处于一天中的哪个阶段，输出连续变化的光照值 */
  t = osal_GetSystemClock() % SENSOR_SIM_DAY_MS;

  if ( t < 8000UL )
  {
    /* 夜晚：8 ~ 20 lx，大棚光照不足，需要补光 */
    lux = 8UL + ( t * 12UL ) / 8000UL;
  }
  else if ( t < 14000UL )
  {
    /* 黎明：20 ~ 1000 lx，快速变亮 */
    lux = 20UL + ( ( t - 8000UL ) * 980UL ) / 6000UL;
  }
  else if ( t < 40000UL )
  {
    /* 白天：1200 ~ 1800 lx，缓慢升高 */
    lux = 1200UL + ( ( t - 14000UL ) * 600UL ) / 26000UL;
  }
  else if ( t < 46000UL )
  {
    /* 黄昏：1800 ~ 60 lx，快速变暗 */
    lux = 1800UL - ( ( t - 40000UL ) * 1740UL ) / 6000UL;
  }
  else
  {
    /* 夜晚：60 ~ 15 lx */
    lux = 60UL - ( ( t - 46000UL ) * 45UL ) / 14000UL;
  }

  /* 叠加一点抖动，避免每次上报的数字完全一样 */
  lux += osal_GetSystemClock() % 8UL;

  adc = (uint8)( ( lux * 255UL ) / SENSOR_LIGHT_LUX_MAX );

#else /* 真实传感器 */

  #if ( SENSOR_LIGHT_USE_ADC == 1 )
    /* 模拟量方式：ADC 采样后线性换算成光照值 */
    adc = (uint8)HalAdcRead( SENSOR_LIGHT_ADC_CHN, HAL_ADC_RESOLUTION_8 );

    #if ( SENSOR_LIGHT_ADC_RISING == 0 )
      adc = (uint8)( 255 - adc );
    #endif

    lux = ( (uint32)adc * SENSOR_LIGHT_LUX_MAX ) / 255UL;
  #else
  {
    /* 开关量方式：只有“亮 / 暗”两档，用独立作用域声明 lit，
       模拟量与 ADC 量方式下不会产生“声明未使用”的告警 */
    uint8 lit;

    lit = ( SENSOR_LIGHT_DO_PORT & SENSOR_LIGHT_DO_BIT ) ? 1 : 0;

    #if ( SENSOR_LIGHT_DO_ACTIVE_HIGH == 0 )
      lit = (uint8)( lit ? 0 : 1 );
    #endif

    if ( lit )
    {
      adc = 255;
      lux = 1200UL;
    }
    else
    {
      adc = 0;
      lux = 5UL;
    }
  }
  #endif

#endif /* SENSOR_LIGHT_SIMULATE */

  if ( lux > 65535UL )
  {
    lux = 65535UL;
  }

  ls->lux = (uint16)lux;
  ls->adc = adc;

  return TRUE;
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
