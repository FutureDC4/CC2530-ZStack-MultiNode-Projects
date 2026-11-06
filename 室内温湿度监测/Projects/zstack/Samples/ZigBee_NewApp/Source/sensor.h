/**************************************************************************************************
  Filename:       sensor.h
  Revised:        $Date: 2026-10-22 $

  Description:    传感器驱动接口（室内温湿度监测）
                  温湿度：DHT11 单总线，接在温湿度终端上

                  默认输出模拟数据，不接任何硬件即可完成组网、上报、
                  串口显示、上位机显示的完整演示：
                    SENSOR_SIMULATE = 1  温湿度按 2 分钟一个循环缓慢升降

                  接真实 DHT11 时把 SENSOR_SIMULATE 改成 0，
                  并按实际接线修改下面的引脚宏。
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

/*********************************************************************
 * 编译选项
 */

/* 1：模拟数据；0：读取真实 DHT11 */
#ifndef SENSOR_SIMULATE
#define SENSOR_SIMULATE               1
#endif

/* DHT11 单总线数据引脚 —— 按实际接线修改
   注意：P0.6 / AIN6 被摇杆方向识别占用（hal_key.c 的 HAL_ADC_CHANNEL_6），
        所以这里默认用 P0.7；若模块已经接在 P0.6 上，把 BV(7) 改成 BV(6) 即可。 */
#define SENSOR_DHT11_PORT             P0
#define SENSOR_DHT11_SEL              P0SEL
#define SENSOR_DHT11_DIR              P0DIR
#define SENSOR_DHT11_BIT              BV(7)

/*********************************************************************
 * TYPEDEFS
 */
typedef struct
{
  int16  temperature;    /* 温度，单位 0.1℃（256 表示 25.6℃） */
  uint16 humidity;       /* 湿度，单位 0.1%RH（605 表示 60.5%RH） */
} sensorTempHum_t;

/*********************************************************************
 * FUNCTIONS
 */

/* 传感器引脚初始化 */
extern uint8 Sensor_Init( void );

/* 读取一次温湿度；th 为输出参数，返回 TRUE 表示读取成功 */
extern uint8 Sensor_ReadTempHum( sensorTempHum_t *th );

/*********************************************************************
*********************************************************************/

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_H */
