/**************************************************************************************************
  Filename:       sensor.c
  Revised:        $Date: 2026-10-20 $

  Description:    光敏传感器驱动实现

                  SENSOR_LIGHT_SIMULATE = 1 时输出模拟昼夜曲线，不依赖外部硬件；
                  = 0 时按 SENSOR_LIGHT_USE_ADC 选择 ADC 模拟量或 IO 开关量方式读取。
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

/* 模拟昼夜周期：60 秒走完一整天，方便演示自动开关灯 */
#define SENSOR_SIM_DAY_MS        60000UL

/*********************************************************************
 * @fn      Sensor_Init
 *
 * @brief   传感器引脚初始化
 *
 * @return  TRUE
 */
uint8 Sensor_Init( void )
{
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
  uint8  lit = 0;

  if ( ls == 0 )
  {
    return FALSE;
  }

#if ( SENSOR_LIGHT_SIMULATE == 1 )

  /* 按开机时间推算当前处于一天中的哪个阶段，输出连续变化的模拟光照值 */
  t = osal_GetSystemClock() % SENSOR_SIM_DAY_MS;

  if ( t < 8000UL )
  {
    /* 夜晚：8 ~ 20 lx */
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
    /* 开关量方式：只有“亮/暗”两档 */
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

/*********************************************************************
*********************************************************************/
