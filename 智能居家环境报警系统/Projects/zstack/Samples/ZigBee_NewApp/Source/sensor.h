/**************************************************************************************************
  Filename:       sensor.h
  Revised:        $Date: 2026-10-20 $

  Description:    温湿度传感器 / 火焰传感器 驱动接口
                  默认使用模拟数据（SENSOR_SIMULATE=1），无需接传感器即可编译演示；
                  接上真实传感器后把 SENSOR_SIMULATE 改成 0，并按实际接线修改引脚宏。
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

/* 1：模拟数据（温湿度缓慢变化；火焰每 30s 有 5s 报警窗口，便于演示自动上报）
   0：读取真实传感器 */
#ifndef SENSOR_SIMULATE
#define SENSOR_SIMULATE               1
#endif

/* DHT11 单总线数据引脚（温湿度终端）—— 按实际接线修改 */
#define SENSOR_DHT11_PORT             P0
#define SENSOR_DHT11_SEL              P0SEL
#define SENSOR_DHT11_DIR              P0DIR
#define SENSOR_DHT11_BIT              BV(6)

/* 火焰传感器数字输出引脚（火焰终端）—— 按实际接线修改 */
#define SENSOR_FLAME_PORT             P0
#define SENSOR_FLAME_SEL              P0SEL
#define SENSOR_FLAME_DIR              P0DIR
#define SENSOR_FLAME_BIT              BV(5)

/* 1：高电平表示检测到火焰；0：低电平表示检测到火焰（多数火焰模块 DO 有火时输出低电平） */
#define SENSOR_FLAME_ACTIVE_HIGH      0

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
extern uint8 Sensor_Init( void );
extern uint8 Sensor_ReadTempHum( sensorTempHum_t *th );
extern uint8 Sensor_ReadFlame( void );

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_H */
