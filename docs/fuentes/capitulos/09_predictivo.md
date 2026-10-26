# Cap. 9 · Motor predictivo y analítica

> Estado: **hecho (borrador)** · Tareas: F3.1, F3.2, F3.5 (heredadas), F6.8 · Fecha: 2026-08-27
> Ficheros fuente: `services/motor_predictivo.py`

---

## 9.1 Formulación matemática completa del EWMA y el ACWR

La media móvil ponderada exponencialmente (EWMA) que el sistema usa tanto para suavizar el
e1RM como para calcular el ACWR sigue la forma estándar recursiva
(`motor_predictivo.py:322-329`):

```
α = 1 − 0,5^(1/h)                    (h = semivida en número de observaciones)
EWMA₀ = valor₀
EWMAₜ = α·valorₜ + (1−α)·EWMAₜ₋₁
```

Para el ACWR (`acwr_usuario`, líneas 367-388), la señal de entrada es el **tonelaje diario
total** del atleta (peso × repeticiones, sumado sobre todas las series del día, incluyendo
días sin entrenar como tonelaje cero), y se calculan dos EWMA en paralelo con semividas fijas
que corresponden a las ventanas clásicas de la literatura (Williams et al., 2017; ver §2.2):

```
λ_agudo  = 2/(7+1)  = 0,25       (ventana de 7 días)
λ_crónico = 2/(28+1) = 0,069      (ventana de 28 días)

ACWR = EWMA_agudo(tonelaje) / EWMA_crónico(tonelaje)
```

calculado iterando 56 días hacia atrás desde la fecha de consulta, con un mínimo de 6 series
registradas para producir un resultado (si no, devuelve `None` — "datos insuficientes", no un
cero engañoso). El propio código anota, en un comentario más explícito que el citado
originalmente en el PDF v9.1: *"Heurística con crítica metodológica en la literatura: se usa
como SEMÁFORO, nunca como veredicto"* — coherente con la discusión crítica del §2.2 de este
documento (Impellizzeri et al., 2020).

## 9.2 El piso de progresión determinista: un error real, corregido y documentado

El motor de predicción de carga combina el e1RM suavizado por EWMA (semivida de 4 sesiones)
con un componente bayesiano opcional (§9.3). El comentario del código en
`motor_predictivo.py:503-510` documenta un error real detectado y corregido, que merece
citarse con precisión porque es evidencia directa de por qué el sistema necesita mecanismos
deterministas incluso cuando ya usa un modelo estadístico:

> *"El e1RM va por EWMA de halflife 4 sesiones, o sea que persigue al atleta con retraso: con
> una progresión lineal limpia (100 → 102.5 → 105 → 107.5 kg x5 @RPE8) el EWMA se quedaba en
> 126 kg de e1RM y la recomendación salía 100 kg — mandarle MENOS de lo que acababa de
> levantar."*

Es decir: un atleta que progresa de forma perfectamente saludable recibía, por el simple
retraso estadístico inherente a una media móvil, una recomendación de carga **inferior** a la
que acababa de completar con éxito. La corrección es un piso determinista, no estadístico: si
la última carga de referencia se completó a un RPE igual o menor que el objetivo y la
disponibilidad reportada (`readiness`) es ≥ 0,6, esa carga pasa a ser el **suelo** de la
recomendación, nunca el techo — el modelo estadístico puede sugerir subir por encima de ese
suelo, pero nunca bajar de él salvo que el atleta reporte fatiga (`readiness < 0,6`), caso en
el que sí se permite bajar.

Este hallazgo —catalogado en la memoria de trabajo del proyecto entre "los seis errores
aritméticos graves del coach"— es la evidencia más directa de todo el sistema de que **ni
siquiera un componente puramente numérico y determinista está libre de producir una
recomendación perjudicial por defecto de diseño**, y de que la respuesta no fue "confiar más
en el modelo estadístico" sino añadir una regla de sentido común (nunca recomendar menos de
lo ya demostrado, con las salvedades de fatiga) por encima de él. Es la misma filosofía de
"un piso/techo determinista sobre una estimación variable" que aparece en el redondeo a
incremento de barra (`redondear_carga`) y en el clamp de la carga recomendada contra la
última carga de referencia (`CLAMP_BAJO`/`CLAMP_ALTO`, líneas 495-499).

## 9.3 El modelo bayesiano: variables de entrada y criterio de aceptación

Cuando hay historial suficiente (≥ 8 sesiones registradas), el motor complementa el piso
determinista con un modelo de regresión bayesiana (`_modelo_bayesiano`,
`motor_predictivo.py:412-430`) entrenado sobre las sesiones históricas del propio atleta. Las
variables de entrada (`_features_en`, líneas 395-409) son:

| Variable | Descripción |
|---|---|
| `ewma` | e1RM suavizado (semivida configurable, `HALFLIFE_E1RM`) |
| `pend` | Pendiente de la tendencia reciente del e1RM |
| `dias_desc` | Días de descanso desde la última sesión del ejercicio, acotado a 14 |
| `rpe_ult` | Esfuerzo percibido de la última serie de referencia |
| `acwr` | La ratio aguda/crónica del §9.1, con valor por defecto neutro (1,0) si no hay dato |
| `readiness` | Disponibilidad autorreportada del cuestionario matutino |

El modelo devuelve una media (`mu`) y una desviación estándar (`sd`) de la predicción. El
sistema **no adopta la predicción bayesiana automáticamente**: solo la mezcla con el piso
determinista (`e1_hat = 0,5·EWMA + 0,5·mu`) si la incertidumbre relativa es aceptable
(`sd <= SIGMA_MAX_REL * e1_ewma`); si la incertidumbre es demasiado alta, se descarta y el
sistema opera solo con el componente determinista. Por debajo de 8 sesiones registradas, el
componente bayesiano no se entrena en absoluto — el piso determinista actúa siempre solo.

Este patrón —un componente probabilístico que se activa condicionalmente y que nunca
sustituye por completo a un piso determinista— es coherente con la filosofía general del
proyecto (Cap. 1 §1.2): incluso donde el sistema sí modela incertidumbre explícitamente, esa
incertidumbre está acotada por reglas deterministas verificables, no se deja a la libre
extrapolación del modelo estadístico.

## 9.4 Clasificación por grupo muscular: una limitación de canonización

El análisis de volumen recuperable (Cap. 2 §2.1.3, `analizar_mrv`) necesita agrupar ejercicios
por grupo muscular. La canonización de nombres pierde información en casos concretos: el
curl de bíceps y el curl femoral (isquiotibiales) se reducen a la misma clave canónica pese a
ser grupos musculares opuestos. La resolución consulta primero el nombre completo del
ejercicio, donde una palabra discriminante ("femoral", "bíceps") desambigua, y solo cae a la
clave canónica genérica si esa palabra no está presente — una solución parcial, no una
corrección de fondo del esquema de canonización, documentada así en el PDF v9.1 (Cap. 10) y
confirmada aquí sin cambios adicionales.
