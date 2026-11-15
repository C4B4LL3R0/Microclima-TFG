# -*- coding: utf-8 -*-
"""Abstracción de hardware del Monitor de Microclima Edge.

De momento contiene el buzzer: un protocolo común (`BuzzerDriver`) con
implementaciones GPIO, consola y nula, más un controlador con hilo propio que
reproduce los patrones sin bloquear el servidor web ni el hilo de sensores.
"""
