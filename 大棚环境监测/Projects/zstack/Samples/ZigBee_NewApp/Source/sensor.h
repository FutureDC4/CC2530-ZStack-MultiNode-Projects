/**************************************************************************************************
  Filename:       sensor.h
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动接口（大棚环境监测）
                  温湿度：DHT11 单总线（温湿度终端）
                  光照：  光敏电阻 / 光敏模块（光敏终端）

                  两个传感器默认都输出模拟数据，不接任何硬件即可完成
                  组网、上报、串口显示、上位机显示的完整演示：
                    SENSOR_SIMULATE       = 1  温湿度用模拟值（缓慢漂移）
                    SENSOR_LIGHT_SIMULATE = 1  光照用模拟昼夜曲线（60s 一个昼夜）

                  接真实传感器时把对应的宏改成 0，并按实际接线修改引脚宏。
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

/* ---------- 温湿度 ---------- */
/* 1：模拟数据；0：读取真实 DHT11 */
#ifndef SENSOR_SIMULATE
#define SENSOR_SIMULATE               1
#endif

/* DHT11 单总线数据引脚 —— 按实际接线修改
   注意：P0.6 / AIN6 被摇杆方向识别占用，所以这里默认用 P0.7；
        如果模块已经接在 P0.6 上，把下面的 BV(7) 改成 BV(6) 即可。 */
#define SENSOR_DHT11_PORT             P0
#define SENSOR_DHT11_SEL              P0SEL
#define SENSOR_DHT11_DIR              P0DIR
#define SENSOR_DHT11_BIT              BV(7)

/* ---------- 光照 ---------- */
/* 1：模拟昼夜曲线；0：读取真实光敏模块 */
#ifndef SENSOR_LIGHT_SIMULATE
#define SENSOR_LIGHT_SIMULATE         1
#endif

/* 真实模块接法：1 = 模拟量 AO 接 ADC；0 = 开关量 DO 接普通 IO
   绝大多数光敏电阻模块同时引出 AO 与 DO，建议用 AO，能拿到连续的光照值 */
#ifndef SENSOR_LIGHT_USE_ADC
#define SENSOR_LIGHT_USE_ADC          1
#endif

/* 模拟量（AO）接线：默认 P0.5，对应 CC2530 ADC 通道 AIN5
   注意：摇杆方向识别占用了 AIN6（P0.6），不要和光敏复用同一路 */
#define SENSOR_LIGHT_ADC_SEL          P0SEL
#define SENSOR_LIGHT_ADC_DIR          P0DIR
#define SENSOR_LIGHT_ADC_INP          P0INP
#define SENSOR_LIGHT_ADC_BIT          BV(5)
#define SENSOR_LIGHT_ADC_CHN          HAL_ADC_CHANNEL_5

/* 开关量（DO）接线：默认 P0.4 */
#define SENSOR_LIGHT_DO_SEL           P0SEL
#define SENSOR_LIGHT_DO_DIR           P0DIR
#define SENSOR_LIGHT_DO_INP           P0INP
#define SENSOR_LIGHT_DO_BIT           BV(4)
#define SENSOR_LIGHT_DO_PORT          P0

/* 1：DO 高电平表示“有光”；0：低电平表示“有光” */
#define SENSOR_LIGHT_DO_ACTIVE_HIGH   1

/* ADC 满量程对应的光照值（lx）。光敏电阻阻值与光照不是严格线性关系，
   这里做线性近似，只为了让串口能输出一个可读的光照数值 */
#define SENSOR_LIGHT_LUX_MAX          2000

/* 1：ADC 采样值越大越亮；0：越大越暗（取决于模块分压电阻的上下位置） */
#define SENSOR_LIGHT_ADC_RISING       1

/*********************************************************************
 * TYPEDEFS
 */
typedef struct
{
  int16  temperature;    /* 温度，单位 0.1℃（256 表示 25.6℃） */
  uint16 humidity;       /* 湿度，单位 0.1%RH（605 表示 60.5%RH） */
} sensorTempHum_t;

typedef struct
{
  uint16 lux;            /* 光照强度，单位 lx（0 ~ SENSOR_LIGHT_LUX_MAX） */
  uint8  adc;            /* 8 位原始采样值，便于观察模块输出是否正常 */
} sensorLight_t;

/*********************************************************************
 * FUNCTIONS
 */

/* 传感器引脚与采样通道初始化 */
extern uint8 Sensor_Init( void );

/* 读取一次温湿度；th 为输出参数，返回 TRUE 表示读取成功 */
extern uint8 Sensor_ReadTempHum( sensorTempHum_t *th );

/* 读取一次光照；ls 为输出参数，返回 TRUE 表示读取成功 */
extern uint8 Sensor_ReadLight( sensorLight_t *ls );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_H */
