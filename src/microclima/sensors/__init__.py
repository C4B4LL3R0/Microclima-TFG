# -*- coding: utf-8 -*-
"""Sensores y backends de adquisición del Monitor de Microclima Edge.

`base` define el contrato `SensorReader` y el modelo `SensorReading`. Los
módulos `bme280`, `sgp30` y `ds3231` implementan los dispositivos I2C reales;
`rpi` los compone sobre un único bus y `simulator` ofrece el mismo contrato sin
hardware para Kubuntu y las pruebas automatizadas.
"""
