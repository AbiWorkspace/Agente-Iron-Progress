# Cap. 3 · Estado del arte

> Estado: **hecho (borrador)** · Tareas: F4.1, F4.2, F4.3, F4.4 · Fecha: 2026-08-27
>
> **Nota metodológica importante:** las descripciones de productos comerciales de este
> capítulo se redactaron sin acceso a búsqueda web durante esta sesión de documentación,
> a partir de conocimiento general del dominio disponible en el modelo que escribió el
> borrador. **Antes de dar este capítulo por cerrado (marcar F12.5), cada afirmación sobre
> un producto de terceros —qué hace, qué no hace, dónde vive el dato— debe verificarse
> contra la documentación oficial o la app real de cada producto**, con fecha de consulta.
> Se advierte esto explícitamente porque es precisamente el tipo de afirmación no verificada
> que el resto de este plan se ha tomado el trabajo de evitar.

---

## 3.1 Aplicaciones comerciales de entrenamiento de fuerza

| Producto | ¿Planifica o solo registra? | ¿Autorregula por esfuerzo? | ¿Lenguaje natural? | ¿Dónde viven los datos? |
|---|---|---|---|---|
| Strong | Registra; plantillas de rutina fijas, sin periodización adaptativa | No | No — formularios estructurados | Nube del proveedor |
| Hevy | Registra; plantillas y seguimiento social | No | No | Nube del proveedor |
| JuggernautAI | Planifica con periodización basada en reglas | Parcial — ajusta por rendimiento reportado en formulario | No — flujo guiado por formulario, sin conversación libre | Nube del proveedor |
| RP Hypertrophy App | Planifica según el marco MEV/MAV/MRV de Israetel et al. (2015) | Sí, por formulario estructurado de dolor/bombeo/rendimiento tras cada sesión | No — interacción por formulario | Nube del proveedor |
| Fitbod | Genera rutinas por disponibilidad de equipo y grupo muscular trabajado recientemente | Parcial | No | Nube del proveedor |
| Boostcamp | Ofrece programas de coaches reconocidos, ejecución guiada | No | No | Nube del proveedor |

**Patrón que emerge de la comparación:** ningún producto de esta categoría combina
planificación adaptativa real (más allá de plantillas o reglas fijas) con una interfaz de
lenguaje natural conversacional. La autorregulación, cuando existe, se captura mediante
formularios estructurados (escalas, botones), no mediante interpretación de texto libre como
*"me quedaron 2 en el tanque"*. Y en todos los casos revisados, el procesamiento y
almacenamiento de datos ocurre en la nube del proveedor, no en el dispositivo o infraestructura
del usuario.

## 3.2 Agentes conversacionales con LLM en salud y deporte

La literatura sobre asistentes de salud basados en LLM converge en una preocupación central:
la fiabilidad factual del consejo generado, precisamente porque el usuario de estos sistemas
—a diferencia de, por ejemplo, un usuario de un asistente de programación que puede ejecutar y
verificar el código sugerido— frecuentemente no tiene forma independiente de verificar un
consejo de salud (Ji et al., 2023; Huang et al., 2025, aplicados al dominio general; la
literatura específica de LLM en salud clínica documenta el mismo problema con consecuencias
potencialmente más graves). Este es exactamente el argumento estructural del Cap. 1 §1.2 de
este trabajo, trasladado del dominio clínico general al entrenamiento de fuerza.

**Relación con este trabajo:** la arquitectura de Iron Progress —separación estricta entre
cálculo determinista y generación de lenguaje, más verificación posterior contra una fuente
de verdad objetiva— es una respuesta de ingeniería concreta a esa preocupación documentada en
la literatura, aplicada a un dominio (fuerza y acondicionamiento) donde a diferencia de
muchos escenarios clínicos generales sí existe una fuente de verdad determinista disponible
(el historial de entrenamiento, el plan, las fórmulas de la ciencia del ejercicio), lo que
hace la verificación posterior más tratable que en dominios de diagnóstico médico general
donde la "verdad" no siempre es computable.

## 3.3 Sistemas de entrenamiento basado en velocidad del mercado

Los sistemas VBT comerciales se dividen en dos familias de hardware: **encoders lineales**
(dispositivos que miden desplazamiento de un cable sujeto a la barra, p. ej. GymAware,
Vitruve) y **acelerómetros/IMU** sujetos a la barra o la muñeca (p. ej. Enode, algunos modos
de Metric VBT). Ambas familias son la tecnología de referencia en la literatura de validación
de VBT (González-Badillo & Sánchez-Medina, 2010; Weakley et al., 2021) precisamente porque
ofrecen mediciones de velocidad de alta frecuencia y precisión conocida, contra las cuales
otros métodos —incluida la estimación por vídeo— suelen validarse por concordancia.

El motor VBT de este proyecto (Cap. 11) toma un enfoque distinto: **estimar velocidad y
trayectoria de barra a partir de vídeo de un teléfono móvil, sin ningún sensor adicional**,
mediante seguimiento del disco de la barra por visión por computadora. La contrapartida
honesta de este enfoque, declarada explícitamente como limitación del trabajo (§1.6, y
desarrollada con evidencia en el Cap. 16 §8.6), es que **no existe en este proyecto una
validación de concordancia contra un encoder de referencia** — la práctica estándar que
establecería si las velocidades estimadas por vídeo son intercambiables con las de un
instrumento validado. Lo que sí se reporta es evidencia indirecta de calidad de detección
(tasa de seguimiento del disco por fotograma, medida sobre grabaciones propias — Cap. 11) y
coherencia interna del conteo de repeticiones, que son garantías más débiles que la
concordancia con un patrón de oro.

## 3.4 Comparativa y hueco que ocupa este trabajo

| Capacidad | Apps de registro (3.1) | Sistemas VBT comerciales (3.3) | Asistentes de salud con LLM (3.2) | Iron Progress |
|---|---|---|---|---|
| Planificación adaptativa real | Parcial/nula | No aplica | No aplica | **Sí**, con periodización DUP y volumen por patrón |
| Interfaz de lenguaje natural conversacional | No | No | Sí | **Sí** |
| Autorregulación por lenguaje libre (no formulario) | No | No aplica | Variable | **Sí** |
| Métricas VBT | No | Sí, con hardware dedicado | No aplica | **Sí, sin hardware** (con la limitación de 3.3) |
| Defensas explícitas contra alucinación numérica | No aplica | No aplica | Variable, poco documentado por producto | **Sí**, seis redes documentadas con incidente de origen |
| Inferencia y datos en local | No | Sí (el hardware es local, aunque no siempre el análisis) | Generalmente no | **Sí**, modelo de lenguaje incluido |

Ningún producto o sistema de investigación revisado combina las seis columnas de esta tabla a
la vez. El hueco que este trabajo ocupa no es ninguna capacidad aislada —planificación
adaptativa, VBT sin hardware, o conversación en lenguaje natural existen por separado en
otros sistemas— sino su combinación, sostenida sobre una arquitectura que hace explícita y
auditable la separación entre lo que calcula el código y lo que comunica el modelo de
lenguaje. Esa combinación, y el patrón de ingeniería que la sostiene, es lo que se defiende
como aportación en el Cap. 17 (Discusión).
