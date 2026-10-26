# Cap. 15 · Verificación y calidad

> Estado: **hecho (borrador)** · Tarea: F6.11 · Fecha: 2026-08-27
> Ver también: `docs/fuentes/capitulos/f1_2_cifras_del_sistema.md`,
> `docs/fuentes/capitulos/qa-banco-casos-uso` (memoria del proyecto)

---

## 15.1 La pirámide de pruebas del proyecto

El sistema combina tres niveles de prueba, cada uno diseñado para un riesgo distinto —no son
tres formas redundantes de comprobar lo mismo:

```
                    ╱╲
                   ╱  ╲     72 aserciones de humo (smoke_v8.py)
                  ╱ 72 ╲    Riesgo: que el módulo real, con sus dependencias
                 ╱──────╲   verdaderas sustituidas, ni siquiera cargue o se
                ╱        ╲  rompa un contrato básico entre módulos.
               ╱ 74 casos ╲
              ╱  22 bloques╲ Riesgo: que el orquestador COMPLETO, conversando
             ╱──────────────╲ como un atleta real, tome la decisión equivocada
            ╱                ╲ ante un mensaje ambiguo del lenguaje natural.
           ╱   32 suites       ╲
          ╱   deterministas     ╲ Riesgo: que un componente aislado (un parser,
         ╱────────────────────────╲ un regex, una fórmula) falle en un caso
                                     de borde reproducible en milisegundos.
```

Verificado en vivo en esta sesión (F1.2): **32 suites deterministas, 74 casos de uso en 22
bloques, 72 aserciones de humo** — las tres cifras coinciden exactamente con lo que documentaba
el PDF v9.1, a diferencia de otras cifras del mismo documento que sí habían quedado
desactualizadas (F1.2).

## 15.2 Por qué tres niveles y no uno

Un agente conversacional sobre un LLM no se puede verificar solo con pruebas unitarias
deterministas: la salida del modelo no es reproducible byte a byte entre ejecuciones, así que
una prueba unitaria clásica (entrada fija → salida exacta esperada) es inaplicable al
comportamiento del modelo en sí, aunque sí es perfectamente aplicable a los componentes
deterministas que lo rodean (parseo, interceptores, redes de validación) — que son
precisamente donde se concentran las 32 suites.

El banco de casos de uso (`qa_casos_uso.py`) cubre el hueco que las suites deterministas no
pueden cubrir: **comportamiento del sistema completo, incluido el modelo real**, frente a
mensajes que un atleta escribiría. Su veredicto no compara texto exacto (imposible con un
modelo generativo) sino condiciones verificables por código: qué herramientas se dispararon
(`tools_esperadas`/`tools_prohibidas`), y qué expresiones deben o no deben aparecer en la
respuesta (`debe_decir`/`no_debe_decir`, expresiones regulares). Es una forma de aserción de
comportamiento, no de contenido literal — coherente con la imposibilidad de aserciones exactas
sobre texto generado.

## 15.3 El ruido como propiedad medida, no como defecto oculto

El proyecto documenta explícitamente —y esta tesina adopta como principio metodológico (Cap.
4 §4.3)— que el banco de casos de uso tiene ruido inherente: **±1 acierto entre corridas es
normal**, atribuible a la variabilidad propia de un modelo generativo con temperatura > 0 en
los turnos de razonamiento. La recomendación operativa documentada es repetir la ejecución
antes de atribuir una variación de resultado a un cambio de código — un principio análogo al
de repetición experimental en cualquier medición con componente estocástico, aplicado aquí a
la evaluación de software.

Es importante para la tesina distinguir esto de "el banco de pruebas no es fiable": el ruido
está acotado, medido, y su fuente es conocida (el muestreo del modelo, no un defecto del
arnés de pruebas). La alternativa —fijar la temperatura a 0 en todos los turnos para eliminar
el ruido— degradaría la fidelidad del banco respecto al comportamiento real en producción, así
que el proyecto opta por medir con ruido conocido en vez de eliminar el ruido a costa de
medir un sistema distinto del que corre en producción.

## 15.4 Un banco de pruebas que también puede mentir: dos defectos propios detectados

El propio proyecto documenta (memoria de trabajo, "un banco de pruebas que miente es peor que
no tenerlo") dos defectos del **instrumental de prueba en sí**, no del sistema bajo prueba,
detectados durante su desarrollo:

1. El detector de incidencias marcaba una señal que el registro del sistema emite en **todas**
   las llamadas, señalando el 100 % de los turnos como sospechosos — vaciando de significado la
   alerta.
2. Varios casos del banco estaban mal planteados, pidiendo un ejercicio que no correspondía a
   la sesión del día: el coach respondía correctamente que ese ejercicio no tocaba, y el caso
   de prueba medía otra cosa distinta de lo que su nombre sugería.

Ambos son hallazgos que refuerzan, en vez de contradecir, la disciplina de verificación
transversal a todo este plan de documentación: un instrumento de medición (sea un banco de
pruebas o, como en F1.2, un script de recuento de métricas) puede tener sus propios defectos, y
descubrirlos exige el mismo tipo de reconciliación cruzada aplicada en F1.2 —comparar contra un
segundo método o una ejecución real—, no confiar en el resultado de la primera pasada.
