# Cap. 8 · Motor de planificación

> Estado: **hecho (borrador)** · Tareas: F3.3, F3.4 (heredadas), F6.7 · Fecha: 2026-08-27
> Ficheros fuente: `scripts/Entrenamiento.py:258-350, 691-770`

---

## 8.1 Del marco teórico al algoritmo

El Cap. 2 (§2.1.3 y §2.1.4) sitúa el marco MEV/MAV/MRV y la periodización ondulante diaria en
la literatura. Este capítulo documenta cómo se traducen a código.

## 8.2 Algoritmo de reparto de volumen semanal (pseudocódigo verificado)

```
para cada patrón de movimiento P y fase de bloque B:
    (min_vol, max_vol) ← VOLUMEN_SEMANAL_POR_PATRON[B][P]      # umbrales MEV→MAV
    curva ← interpolación_lineal(max_vol → min_vol, n_semanas)  # el bloque EMPIEZA
                                                                  # cerca del máximo
                                                                  # adaptativo y TERMINA
                                                                  # cerca del mínimo
                                                                  # efectivo
    volumen_semana ← techo(curva[semana_actual])

    # Punto 8: el volumen NO se reparte por igual entre los días DUP de la
    # semana — se pesa por tipo de sesión:
    peso_fase ← {Hipertrofia: 0.55, Fuerza: 0.225, Potencia: 0.175}[fase_día]
    peso_normalizado ← peso_fase / Σ(pesos de las fases presentes ESTA semana)
    series_día ← volumen_semana × peso_normalizado

    si fase_día ∈ {Potencia, Hipertrofia}: redondear hacia arriba (techo)
    si no:                                  redondear al entero más cercano
```

Dos decisiones de este algoritmo merecen justificación explícita:

1. **La interpolación va de máximo a mínimo, no al revés.** El bloque empieza cerca del
   volumen máximo adaptativo (MAV) y desciende hacia el mínimo efectivo (MEV) conforme avanza
   el mesociclo — el patrón inverso al que usan muchos programas de hipertrofia clásicos
   (donde el volumen sube progresivamente). Esta elección sigue la lógica de que la fatiga
   acumulada de un bloque hace necesario recortar el volumen hacia el final para permitir la
   realización del rendimiento (consistente con el concepto de bloques de realización de
   Issurin, 2010, §2.1.4), aunque el código no cita esta fuente explícitamente.
2. **El redondeo es asimétrico por tipo de fase**: Potencia e Hipertrofia siempre redondean
   hacia arriba (nunca menos series de las calculadas), mientras que Fuerza redondea al
   entero más cercano. El comentario del código no explica esta asimetría específica; se
   documenta aquí como observación para verificar con el autor del sistema, no como hallazgo
   cerrado.

## 8.3 Diseño "porcentajes en disco, kilos al leer"

El plan generado (`resources/rutinas_generadas/<atleta>/*.json`) **no almacena kilogramos: 
almacena porcentajes de 1RM**. Los kilos se calculan en el momento de la lectura
(`obtener_rutina_del_json`, Cap. 6 §6.1), multiplicando el porcentaje por la marca vigente del
atleta y redondeando al incremento real de su barra (`redondear_carga`,
`motor_predictivo.py:289`).

La consecuencia práctica de este diseño, ya señalada en el PDF v9.1 y confirmada aquí con
evidencia de código adicional: **cuando el atleta firma un récord, el mesociclo entero se
reajusta sin regenerar nada.** No hace falta recalcular el plan porque el plan nunca contuvo
cifras absolutas — contenía una fórmula relativa a una marca que acaba de cambiar. Esto evita
la clase de error que se vería en un sistema que sí guardara kilos absolutos: un plan
desactualizado que sigue prescribiendo cargas basadas en una marca antigua hasta que alguien
lo regenere manualmente.

Es la misma lógica de "fuente de verdad única, derivación en el punto de consumo" que rige la
separación cálculo/lenguaje del Cap. 1 — aplicada aquí a la relación entre el plan y las
marcas del atleta, no entre el modelo y el código.

## 8.4 La guardia del sistema nervioso central (SNC)

Está prohibido programar dos días de fuerza pesada en fechas de calendario consecutivas. Si la
cola de fases lo requeriría, la fase de fuerza se posterga y se adelanta la siguiente fase no
pesada, conservando el conteo total de sesiones del bloque (no se pierden sesiones, se
reordenan). El estado de esta guardia se comparte entre los grupos musculares Torso y Pierna
—`self._ultima_fase_global`, `self._ultimo_dia_absoluto`— porque, como documenta el propio
código, "la fatiga central es sistémica": no importa qué grupo muscular trabajó el día
anterior si ambos comparten el mismo sistema nervioso.
