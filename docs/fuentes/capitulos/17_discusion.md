# Cap. 17 · Discusión

> Estado: **hecho (borrador)** · Tareas: F9.1, F9.2 · Fecha: 2026-08-27

---

## 17.1 Discusión de resultados frente a los objetivos y al estado del arte (F9.1)

Los siete objetivos específicos del Cap. 1 §1.4 se contrastan aquí contra la evidencia del
Cap. 16:

**Objetivos 1-2 (separación cálculo/lenguaje e interceptores determinsitas)** se sostienen con
evidencia sólida: 32 suites deterministas verificadas, y el diseño arquitectónico documentado
en detalle en los Cap. 5-6 y 8-9. No se encontró, en ningún punto de esta sesión de
documentación, un caso donde el modelo calculara una cifra por su cuenta fuera del patrón
esperado — la separación se sostiene consistentemente en el código revisado.

**Objetivo 3 (eficacia de las redes de validación)** es el que queda con evidencia más débil,
y de forma reveladora: la única ejecución en vivo realizada en esta sesión (Cap. 16 §16.3)
encontró un falso positivo real en la primera muestra pequeña que se probó. Esto no invalida
el diseño de las redes —siguen siendo, conceptualmente, la estrategia correcta frente al
estado del arte de verificación posterior (Cap. 2 §2.6)—, pero sí demuestra que su tasa de
falsos positivos no es despreciable y que, sin instrumentación de activaciones en producción
(Cap. 16 §16.7), el proyecto no puede afirmar con evidencia cuantitativa qué tan bien
funcionan en agregado. Es una brecha honesta entre "el mecanismo existe y está bien
argumentado" y "el mecanismo está medido".

**Objetivos 4-5 (fundamento bibliográfico, cobertura multimodal)** se cumplen con evidencia
directa: el Cap. 2 conecta cada regla de negocio del motor de planificación y predictivo con
literatura específica, y el Cap. 12 aporta una evaluación reejecutada en vivo del clasificador
de ejercicios.

**Objetivo 6 (coste en latencia)** se apoya en evidencia histórica no reverificada (Cap. 16
§16.2) — una limitación explícita de esta sesión de documentación, no del sistema en sí.

**Frente al estado del arte (Cap. 3):** ningún producto comercial ni sistema de investigación
revisado combina planificación adaptativa, conversación en lenguaje natural, VBT sin hardware
y defensas explícitas contra alucinación a la vez (Cap. 3 §3.4). La evidencia de esta sesión
—incluido el incidente de §16.3— no contradice esa posición comparativa: ningún sistema
comparado tiene siquiera el mecanismo de verificación posterior que aquí falló parcialmente,
así que "falla a veces pero existe y es auditable" sigue comparando favorablemente contra "no
existe el mecanismo en absoluto".

## 17.2 La aportación central, formulada explícitamente (F9.2)

Esta tesina defiende una tesis concreta, no solo describe un sistema:

> **En cualquier dominio conversacional donde el usuario no pueda verificar por sí mismo la
> respuesta de un modelo de lenguaje, la arquitectura correcta no es confiar en que el modelo
> no alucine, ni es pedirle al modelo que se autoverifique: es hacer que el modelo nunca sea
> la fuente de la cifra o la afirmación de estado en primer lugar, y auditar lo que sí genera
> —redacción, tono, elección de palabras— contra una fuente de verdad externa y determinista
> que exista de forma independiente del propio modelo.**

Esta tesis se apoya en tres piezas de evidencia de este trabajo, no solo en argumento:

1. **Arquitectónica** (Cap. 1, 5-9): la separación cálculo/lenguaje se mantiene consistente en
   miles de líneas de código real, no es un principio de diseño incumplido en la práctica.
2. **De literatura** (Cap. 2 §2.6): la verificación posterior contra una fuente objetiva es
   estrictamente más fiable que la consistencia entre muestras del mismo modelo
   (SelfCheckGPT), precisamente porque no hereda los sesgos que produjeron la alucinación
   original — un argumento que la literatura general de alucinación no siempre puede dar,
   porque no todo dominio tiene una fuente de verdad determinista disponible como este sí la
   tiene (el historial de entrenamiento, el plan, el registro de herramientas ejecutadas).
3. **Empírica, con sus límites reconocidos** (Cap. 16 §16.3): cuando el mecanismo de
   verificación posterior falla —como se observó en vivo—, falla de una forma **específica y
   diagnosticable** (un falso positivo que dispara una reescritura innecesaria), no de una
   forma silenciosa e indetectable. Eso es, en sí mismo, una propiedad deseable frente a la
   alternativa de no tener ningún mecanismo de verificación.

**El límite honesto de esta aportación**, que la discusión no debe omitir: el patrón exige que
exista una fuente de verdad determinista y computable en el dominio de aplicación. Es
directamente trasladable a dominios como asesoría financiera transaccional o sistemas de
soporte técnico con estado verificable; es **menos** directamente trasladable a dominios donde
la "verdad" no es computable de la misma forma —diagnóstico médico complejo, asesoría legal
sobre casos sin precedente claro—, donde la verificación posterior tendría que apoyarse en
fuentes más débiles que un registro de base de datos. Esta limitación de generalización se
retoma en el Cap. 19.
