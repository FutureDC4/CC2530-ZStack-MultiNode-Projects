/**************************************************************************************************
  Filename:       sensor.h
  Revised:        $Date: 2026-10-20 $

  Description:    光敏传感器驱动接口（路灯控制节点）

                  默认 SENSOR_LIGHT_SIMULATE = 1：输出模拟的昼夜光照曲线（60s 一个完整昼夜），
                  不接任何传感器即可完成组网、上报、串口显示的完整演示。
                  接真实光敏模块后把 SENSOR_LIGHT_SIMULATE 改成 0，并按实际接线选择
                  模拟量（AO，可读具体光照值）或开关量（DO，只能判亮/暗）。
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

/* 1：模拟光照数据；0：读取真实光敏模块
   模拟曲线：0~8s 夜晚  8~14s 黎明  14~40s 白天  40~46s 黄昏  46~60s 夜晚 */
#ifndef SENSOR_LIGHT_SIMULATE
#define SENSOR_LIGHT_SIMULATE         1
#endif

/* 真实模块接法：1 = 模拟量 AO 接 ADC；0 = 开关量 DO 接普通 IO
   绝大多数光敏电阻模块同时引出 AO 与 DO，建议用 AO，能拿到连续的光照值 */
#ifndef SENSOR_LIGHT_USE_ADC
#define SENSOR_LIGHT_USE_ADC          1
#endif

/* ---------- 模拟量（AO）接线：默认 P0.5，对应 CC2530 ADC 通道 AIN5 ----------
   注意：摇杆方向识别占用了 AIN6（P0.6），不要和光敏复用同一路 */
#define SENSOR_LIGHT_ADC_PORT         P0
#define SENSOR_LIGHT_ADC_SEL          P0SEL
#define SENSOR_LIGHT_ADC_DIR          P0DIR
#define SENSOR_LIGHT_ADC_INP          P0INP
#define SENSOR_LIGHT_ADC_BIT          BV(5)
#define SENSOR_LIGHT_ADC_CHN          HAL_ADC_CHANNEL_5

/* ---------- 开关量（DO）接线：默认 P0.4 ---------- */
#define SENSOR_LIGHT_DO_PORT          P0
#define SENSOR_LIGHT_DO_SEL           P0SEL
#define SENSOR_LIGHT_DO_DIR           P0DIR
#define SENSOR_LIGHT_DO_INP           P0INP
#define SENSOR_LIGHT_DO_BIT           BV(4)

/* 1：DO 高电平表示“有光/白天”；0：低电平表示“有光/白天” */
#define SENSOR_LIGHT_DO_ACTIVE_HIGH   1

/* ADC 满量程对应的光照值（lx）。光敏电阻的阻值与光照本身不是严格线性关系，
   这里做线性近似，只为了让串口能输出一个可读的光照数值 */
#define SENSOR_LIGHT_LUX_MAX          2000

/* 1：ADC 采样值越大越亮；0：ADC 采样值越大越暗
   （取决于模块上光敏电阻与分压电阻的上下位置，接反了把这个宏翻转即可） */
#define SENSOR_LIGHT_ADC_RISING       1

/*********************************************************************
 * TYPEDEFS
 */
typedef struct
{
  uint16 lux;                    /* 光照强度，单位 lx（0 ~ SENSOR_LIGHT_LUX_MAX） */
  uint8  adc;                    /* 8 位原始采样值，便于观察模块输出是否正常 */
} sensorLight_t;

/*********************************************************************
 * FUNCTIONS
 */

/* 传感器引脚与采样通道初始化 */
extern uint8 Sensor_Init( void );

/* 读取一次光照强度；ls 为输出参数，返回 TRUE 表示读取成功 */
extern uint8 Sensor_ReadLight( sensorLight_t *ls );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_H */
