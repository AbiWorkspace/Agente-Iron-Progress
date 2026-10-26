# Cap. 11 · Visión por computadora: motor VBT

> Estado: **hecho (borrador)** · Tareas: F3.6 (heredada), F7.1 · Fecha: 2026-08-27
> Ficheros fuente: `services/Creardataset.py` (3.225 líneas — el módulo más grande
> del proyecto después del orquestador)

---

## 11.1 Por qué este capítulo no existía

El PDF v9.1 dedica media página al análisis de vídeo (Cap. 12), pese a que
`services/Creardataset.py` es, tras el orquestador, el segundo módulo más extenso del sistema.
Este capítulo lo documenta con el mismo nivel de detalle que el Cap. 6-9 dedican al
orquestador y a los motores de planificación/predicción.

## 11.2 Seguimiento del disco de la barra: calibración robusta

`RastreadorDisco` (líneas 692-880) encapsula la detección del disco con YOLO
(`modelo.track(..., tracker="bytetrack.yaml", persist=True)`) y la calibración de la escala
de píxeles a metros — el paso que convierte desplazamiento en la imagen en desplazamiento
físico real.

La calibración no usa el primer disco detectado como referencia. El propio código documenta
por qué (líneas 714-720): si ese primer fotograma trae la caja de detección recortada o a
medio aparecer, toda la serie queda escalada con un error sistemático. La solución implementada
siembra la escala con la **mediana** de los primeros `frames_semilla_escala` discos detectados
(no la media, menos sensible a un valor atípico aislado), y a partir de ahí suaviza con una
media móvil exponencial que **rechaza saltos físicamente imposibles** de tamaño de caja entre
fotogramas consecutivos (`_actualizar_escala`, líneas 714-737) — un disco no puede cambiar de
tamaño aparente de un fotograma al siguiente salvo que la detección esté capturando otra caja
distinta (una funda protectora, un segundo disco).

### El hallazgo medido de la versión 3.3

El README documenta una corrección concreta, medida sobre grabaciones propias del proyecto: el
filtro original que descartaba discos apilados (para no confundir un disco de calentamiento con
el de trabajo) también descartaba, en el ejercicio de peso muerto, **el propio disco de
trabajo** durante el primer tirón — YOLO detectaba el disco correcto en el 99,6 % de los
fotogramas evaluados, pero el motor, antes de la corrección, solo aprovechaba el 55,7 % de esas
detecciones válidas. Es la evidencia experimental más concreta que el proyecto reporta sobre la
calidad de su propia visión por computadora, y se cita aquí porque es exactamente el tipo de
cifra —medida, no estimada— que el Cap. 16 (Resultados) debe reportar como resultado
experimental real, no como afirmación de marketing de producto.

## 11.3 La máquina de estados de repetición

`MaquinaEstadosRep` (líneas 1098-1406) cuenta repeticiones y cronometra sus fases mediante una
máquina de estados explícita: `calibrando → reposo → excéntrica ↔ concéntrica → finalizado`.
Dos decisiones de diseño destacan:

1. **Separación entre el mínimo absoluto de la barra y el "tope del empuje".** `y_tope` sigue
   marcando el punto más bajo de la trayectoria (necesario para detectar cuándo la barra
   invierte de dirección y cierra la repetición), pero el tiempo y el rango de movimiento de la
   fase concéntrica se miden contra `y_tope_empuje`, una variable que se **congela** en cuanto
   la barra deja de ganar altura. Sin esa separación (documentada como corrección v3.4 en el
   propio código), el bloqueo final de la repetición —donde la barra ya no sube pero el atleta
   sigue en tensión— se contaba erróneamente como parte de la fase concéntrica, inflando su
   duración medida.
2. **Cronometraje de tres fases, no dos.** Además de excéntrica y concéntrica, se mide el
   tiempo de amortiguación en el punto más bajo (el tiempo entre que la barra deja de bajar y
   empieza a subir) — la señal que la literatura de VBT asocia a la capacidad de producir
   fuerza rápida tras un estiramiento (§2.3).

## 11.4 Estimadores derivados: RIR, %1RM, e1RM y pérdida de velocidad

A partir de la velocidad de la primera repetición de la serie y del perfil carga-velocidad del
ejercicio (`PerfilEjercicio`, con velocidad mínima umbral —MVT— por nivel de entrenamiento), el
motor deriva:

- **%1RM y e1RM del día** (`estimar_pct_1rm`, `estimar_e1rm_serie`, líneas 441-527): el e1RM
  final combina dos evidencias independientes —estimación por velocidad y un **piso por
  repeticiones** (`e1RM ≥ carga × (1 + n/30)`, la misma familia matemática que la fórmula de
  Epley, §2.1.1)— y toma el máximo de ambas, con una nota explícita de que si gana el piso por
  repeticiones en vez de la estimación por velocidad, hay que revisar la calibración de la
  serie: la discrepancia es en sí misma una señal de calidad del dato.
- **Repeticiones en reserva, con reconciliación honesta ante desacuerdo** (`estimar_reps_restantes`,
  líneas 528-573): el sistema mantiene **dos estimadores independientes** de RIR —uno por
  velocidad terminal de la última repetición, otro por pérdida de velocidad intra-serie— y los
  reconcilia tomando su intersección cuando coinciden. La corrección v3.4, documentada
  explícitamente en el código, es notable por seguir la misma filosofía que las redes de
  validación del Cap. 7: cuando los dos estimadores **no se solapan**, la versión anterior
  promediaba sus extremos y producía un rango que ningún estimador sostenía realmente,
  reportado con la misma confianza que un consenso genuino. La versión actual, ante desacuerdo,
  devuelve la **envolvente** de ambos rangos y lo marca explícitamente como `"incierto": True`
  — la misma decisión de "declarar la incertidumbre en vez de disfrazarla de precisión" que
  rige el resto del sistema.
- **Pérdida de velocidad (*velocity loss*)** (`calcular_velocity_loss`, línea 593): se calcula
  contra la repetición **más rápida** de la serie, no contra la primera —el README señala
  explícitamente que la primera repetición casi nunca es la más rápida—, siguiendo la práctica
  estándar de la literatura de VBT (§2.3, Sánchez-Medina & González-Badillo, 2011).

## 11.5 El objeto de estimación de pose no es reentrante: la guardia de concurrencia

MediaPipe Pose mantiene estado interno de seguimiento entre fotogramas. El PDF v9.1 (Cap. 12)
ya documenta que dos atletas enviando vídeo simultáneamente corrompían el grafo de seguimiento
si compartían la misma instancia sin protección, y que un cerrojo serializa la inferencia de
pose sin pérdida de rendimiento real porque es un trabajo limitado por CPU, no por
disponibilidad de GPU. Esta sección no repite esa evidencia; la conecta con la sección 14.3 de
este documento (Cap. 14): es un ejemplo adicional, a nivel del motor de visión, del mismo
patrón de exclusión por unidad de trabajo que gobierna el acceso al modelo de lenguaje.
