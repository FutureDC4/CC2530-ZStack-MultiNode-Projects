/**************************************************************************************************
  Filename:       sensor.h
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动接口（空气质量检测）
                  烟雾传感器：MQ-2 / MQ-135 一类模拟输出模块

                  默认输出模拟数据，不接任何硬件即可完成组网、上报、串口显示、
                  上位机显示的完整演示：
                    SENSOR_SMOKE_SIMULATE = 1  由开机时间推算 90 秒一轮的烟雾曲线

                  接真实传感器时把该宏改成 0，并按实际接线修改引脚宏。
**************************************************************************************************/

#ifndef SENSOR_H
#define SENSOR_H

#ifdef __cplusplus
extern "C"
{
#endif

/*********************************************************************
 * INCLUDES
 */
#include "ZComDef.h"
#include "hal_defs.h"
#include "hal_mcu.h"
#include "hal_adc.h"

/*********************************************************************
 * 编译选项
 */

/* 1：模拟数据；0：读取真实烟雾传感器 */
#ifndef SENSOR_SMOKE_SIMULATE
#define SENSOR_SMOKE_SIMULATE         1
#endif

/* 真实模块接法：1 = 模拟量 AO 接 ADC；0 = 开关量 DO 接普通 IO
   MQ 系列模块通常同时引出 AO 与 DO，用 AO 才能拿到连续的浓度值 */
#ifndef SENSOR_SMOKE_USE_ADC
#define SENSOR_SMOKE_USE_ADC          1
#endif

/* 模拟量（AO）接线：默认 P0.5，对应 CC2530 ADC 通道 AIN5
   注意：摇杆方向识别占用了 AIN6（P0.6），不要和烟雾传感器复用同一路 */
#define SENSOR_SMOKE_ADC_SEL          P0SEL
#define SENSOR_SMOKE_ADC_DIR          P0DIR
#define SENSOR_SMOKE_ADC_INP          P0INP
#define SENSOR_SMOKE_ADC_BIT          BV(5)
#define SENSOR_SMOKE_ADC_CHN          HAL_ADC_CHANNEL_5

/* 开关量（DO）接线：默认 P0.4 */
#define SENSOR_SMOKE_DO_SEL           P0SEL
#define SENSOR_SMOKE_DO_DIR           P0DIR
#define SENSOR_SMOKE_DO_INP           P0INP
#define SENSOR_SMOKE_DO_BIT           BV(4)
#define SENSOR_SMOKE_DO_PORT          P0

/* 1：DO 高电平表示“浓度超过电位器阈值”；0：低电平表示超过 */
#define SENSOR_SMOKE_DO_ACTIVE_HIGH   1

/* ADC 满量程对应的浓度值，与 NewApp.h 的 NEWAPP_SMOKE_MAX 保持一致 */
#define SENSOR_SMOKE_FULL_SCALE       1000

/* 1：ADC 采样值越大浓度越高；0：反相（取决于模块分压的上下位置） */
#define SENSOR_SMOKE_ADC_RISING       1

/*********************************************************************
 * TYPEDEFS
 */
typedef struct
{
  uint16 density;        /* 烟雾浓度，0 ~ SENSOR_SMOKE_FULL_SCALE（等效 ppm） */
  uint8  adc;            /* 8 位原始采样值，便于观察模块输出是否正常 */
} sensorSmoke_t;

/*********************************************************************
 * FUNCTIONS
 */

/* 传感器引脚与采样通道初始化 */
extern uint8 Sensor_Init( void );

/* 读取一次烟雾浓度；sm 为输出参数，返回 TRUE 表示读取成功 */
extern uint8 Sensor_ReadSmoke( sensorSmoke_t *sm );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_H */
