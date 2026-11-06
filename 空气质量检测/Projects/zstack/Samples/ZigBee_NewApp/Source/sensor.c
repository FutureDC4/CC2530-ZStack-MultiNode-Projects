/**************************************************************************************************
  Filename:       sensor.c
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动实现（空气质量检测）
                  烟雾传感器：模拟量 AO -> ADC，或开关量 DO -> 普通 IO
                  SENSOR_SMOKE_SIMULATE 为 1 时按开机时间输出模拟烟雾曲线，
                  不依赖任何外部硬件即可演示。
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

/* 模拟曲线一轮 90 秒：清新 -> 轻度异味 -> 明显烟雾 -> 重度 -> 通风回落，
   正好覆盖上位机的四档空气质量，方便演示报警状态切换 */
#define SENSOR_SIM_CYCLE_MS      90000UL

/*********************************************************************
 * @fn      Sensor_Init
 *
 * @brief   传感器引脚与采样通道初始化
 *
 * @return  TRUE
 */
uint8 Sensor_Init( void )
{
#if ( SENSOR_SMOKE_SIMULATE == 0 )

  #if ( SENSOR_SMOKE_USE_ADC == 1 )
    /* 模拟量输入：关数字功能、方向设为输入、置三态交给 ADC 采样 */
    SENSOR_SMOKE_ADC_SEL &= (uint8)( ~SENSOR_SMOKE_ADC_BIT );
    SENSOR_SMOKE_ADC_DIR &= (uint8)( ~SENSOR_SMOKE_ADC_BIT );
    SENSOR_SMOKE_ADC_INP |= SENSOR_SMOKE_ADC_BIT;

    /* 空读一次，让 ADC 通道与引脚建立稳定的电平 */
    (void)HalAdcRead( SENSOR_SMOKE_ADC_CHN, HAL_ADC_RESOLUTION_8 );
  #else
    /* 开关量输入：数字功能关闭、方向设为输入、关闭下拉 */
    SENSOR_SMOKE_DO_SEL &= (uint8)( ~SENSOR_SMOKE_DO_BIT );
    SENSOR_SMOKE_DO_DIR &= (uint8)( ~SENSOR_SMOKE_DO_BIT );
    SENSOR_SMOKE_DO_INP &= (uint8)( ~SENSOR_SMOKE_DO_BIT );
  #endif

#endif

  return TRUE;
}

/*********************************************************************
 * @fn      Sensor_ReadSmoke
 *
 * @brief   读取一次烟雾浓度
 *
 * @param   sm - 输出：浓度（等效 ppm）与原始采样值
 *
 * @return  TRUE 成功
 */
uint8 Sensor_ReadSmoke( sensorSmoke_t *sm )
{
  uint32 density = 0;
  uint8  adc = 0;

#if ( SENSOR_SMOKE_SIMULATE == 1 )
  /* 只给模拟分支用的变量，放在条件编译里，接真实传感器时不会产生
     “声明未使用”告警 */
  uint32 t;
  uint32 jitter;
#endif

  if ( sm == 0 )
  {
    return FALSE;
  }

#if ( SENSOR_SMOKE_SIMULATE == 1 )

  /* 按开机时间推算当前处于烟雾曲线的哪个阶段，输出连续变化的浓度值 */
  t = osal_GetSystemClock() % SENSOR_SIM_CYCLE_MS;

  if ( t < 15000UL )
  {
    /* 清新：40 ~ 60 ppm 缓慢上升 */
    density = 40UL + ( t * 20UL ) / 15000UL;
  }
  else if ( t < 28000UL )
  {
    /* 轻度异味：60 ~ 110 ppm */
    density = 60UL + ( ( t - 15000UL ) * 50UL ) / 13000UL;
  }
  else if ( t < 40000UL )
  {
    /* 明显烟雾：110 ~ 340 ppm，越过 300 进入超标档 */
    density = 110UL + ( ( t - 28000UL ) * 230UL ) / 12000UL;
  }
  else if ( t < 52000UL )
  {
    /* 重度：340 ~ 740 ppm，越过 700 进入严重档 */
    density = 340UL + ( ( t - 40000UL ) * 400UL ) / 12000UL;
  }
  else if ( t < 62000UL )
  {
    /* 峰值维持：740 ~ 770 ppm */
    density = 740UL + ( ( t - 52000UL ) * 30UL ) / 10000UL;
  }
  else if ( t < 78000UL )
  {
    /* 通风后快速回落：770 ~ 120 ppm */
    density = 770UL - ( ( t - 62000UL ) * 650UL ) / 16000UL;
  }
  else
  {
    /* 回到清新：120 ~ 40 ppm */
    density = 120UL - ( ( t - 78000UL ) * 80UL ) / 12000UL;
  }

  /* 叠加一点抖动，避免每次上报的数字完全一样，看起来更像真实采样 */
  jitter = osal_GetSystemClock() % 9UL;
  if ( jitter >= 4UL )
  {
    density += ( jitter - 4UL );
  }
  else
  {
    if ( density > ( 4UL - jitter ) )
    {
      density -= ( 4UL - jitter );
    }
    else
    {
      density = 0;
    }
  }

  if ( density > (uint32)SENSOR_SMOKE_FULL_SCALE )
  {
    density = (uint32)SENSOR_SMOKE_FULL_SCALE;
  }

  adc = (uint8)( ( density * 255UL ) / (uint32)SENSOR_SMOKE_FULL_SCALE );

#else /* 真实传感器 */

  #if ( SENSOR_SMOKE_USE_ADC == 1 )
    /* 模拟量方式：ADC 采样后线性换算成浓度 */
    adc = (uint8)HalAdcRead( SENSOR_SMOKE_ADC_CHN, HAL_ADC_RESOLUTION_8 );

    #if ( SENSOR_SMOKE_ADC_RISING == 0 )
      adc = (uint8)( 255 - adc );
    #endif

    density = ( (uint32)adc * (uint32)SENSOR_SMOKE_FULL_SCALE ) / 255UL;
  #else
  {
    /* 开关量方式：只有“超阈值 / 未超阈值”两档，用独立作用域声明 over，
       模拟量与 ADC 量方式下不会产生“声明未使用”的告警 */
    uint8 over;

    over = ( SENSOR_SMOKE_DO_PORT & SENSOR_SMOKE_DO_BIT ) ? 1 : 0;

    #if ( SENSOR_SMOKE_DO_ACTIVE_HIGH == 0 )
      over = (uint8)( over ? 0 : 1 );
    #endif

    if ( over )
    {
      adc     = 255;
      density = (uint32)SENSOR_SMOKE_FULL_SCALE;
    }
    else
    {
      adc     = 0;
      density = 30UL;
    }
  }
  #endif

#endif /* SENSOR_SMOKE_SIMULATE */

  if ( density > (uint32)SENSOR_SMOKE_FULL_SCALE )
  {
    density = (uint32)SENSOR_SMOKE_FULL_SCALE;
  }

  sm->density = (uint16)density;
  sm->adc     = adc;

  return TRUE;
}

/*********************************************************************
*********************************************************************/
