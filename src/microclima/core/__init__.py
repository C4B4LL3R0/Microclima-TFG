# -*- coding: utf-8 -*-
"""Núcleo de negocio del Monitor de Microclima Edge.

Agrupa el monitor periódico de sensores (`monitor`), el evaluador de umbrales y
patrones acústicos (`alarm`) y el orquestador que comparte estado con la web
(`runtime`). Ninguno de estos módulos habla directamente con Flask ni con GPIO:
reciben interfaces y devuelven modelos serializables, lo que permite probarlos
de forma determinista.
"""
