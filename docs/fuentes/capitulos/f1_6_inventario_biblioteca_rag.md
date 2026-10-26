# F1.6 — Inventario bibliográfico de la biblioteca científica del RAG

> Estado: **hecho** · Fecha: 2026-08-27
> Fuente: inspección directa de los PDFs en `scripts/resources/libros_cientificos/`
> con `pdfinfo`, `pdftotext` y hash MD5. Ningún dato de esta tabla se tomó del
> nombre de fichero sin verificar el contenido.

## Los cuatro ficheros únicos tras deduplicar (F1.5)

| # | Fichero | Páginas | Identidad verificada | Evidencia |
|---|---|---|---|---|
| 1 | `The-Muscle-and-Strength-Pyramid-Entrenamiento-espa.pdf` | 347 | **The Muscle and Strength Pyramid: Training** (2.ª ed.), Eric Helms PhD CSCS, Andrea Valdez MS, Andy Morgan BS. Traducción al español de José Carlos De Francisco, Víctor Reyes y Alberto Alvarez. © 2013-2019 Eric Helms. | Texto de portada extraído directamente (p. 1-3). |
| 2 | `toaz.info-science-and-development-of-muscle-hypertrophy-...pdf` | 368 | **Science and Development of Muscle Hypertrophy**, Brad Schoenfeld PhD CSCS CSPS FNSCA (Lehman College). Human Kinetics, © 2016. **ISBN 978-1-4925-1960-7.** | Metadatos PDF (`Title`, `Author`) + página de copyright con ISBN y ficha de catalogación LOC. |
| 3 | `renaissance-diet-2-0-mike-israetel-pdf-free.pdf` | 272 | **The Renaissance Diet 2.0**, equipo de Renaissance Periodization (autor principal Mike Israetel según nombre de fichero; portada es imagen, sin metadatos de texto). Prólogo de Rich Froning. | Índice extraído (p. 2): "I. Nutrition Principles and Priorities — Ch.1 The Diet Priorities, Ch.2 Calorie Balance, Ch.3 Macronutrients…". **Es un libro de nutrición, no de teoría del entrenamiento.** |
| 4 | `practical-programming-pr_22d999264dc45f731a67f6a64baf05ab.pdf` | 10 | **NO es el libro de Rippetoe.** Es un artículo/reflexión de blog titulado *"What I have learned from 'Practical Programming' book"*, escrito por **Mladen Jovanović** comentando el libro de Rippetoe y Kilgore. | Metadatos PDF: `Author: Mladen Jovanović`, `Title: Microsoft Word - Practical Programming`. Primer párrafo del texto: *"I have just finished reading the 'Practical Programming for Strenght Training' book by Mark Rippetoe and Lon Kilgore... the purpose of this article is to explain mine understanding of those principles..."* |

## Consecuencias para la bibliografía de la tesina

1. **No citar "Rippetoe, M." como si su libro estuviera indexado en el RAG.** El sistema
   puede recuperar como mucho la lectura que Jovanović hizo de ese libro — una fuente
   secundaria, no la primaria. La entrada correcta en `bibliografia.bib` es Jovanović, no
   Rippetoe (ver F0.2). Si en el futuro se quiere el libro real de Rippetoe & Kilgore como
   fuente primaria, hay que conseguir e indexar el PDF correcto.
2. **The Renaissance Diet 2.0 es nutrición, no periodización ni volumen.** Estaba mal
   clasificado en el borrador inicial de este plan bajo "Volumen, hipertrofia y
   periodización" (§6.3). El sistema Iron Progress no prescribe dieta, así que este libro
   amplía el RAG hacia un dominio que el coach no usa activamente hoy — no es un error,
   pero es una limitación de cobertura que vale la pena anotar en el capítulo de resultados
   (F8) si se mide qué fracción de las recuperaciones de `consultar_libro_cientifico` caen
   en él.
3. **Solo dos de los cuatro ficheros son literatura científica primaria con ISBN
   verificable**: Schoenfeld (2016) y Helms et al. (2.ª ed.). Los otros dos son un libro de
   divulgación sobre nutrición y un artículo de blog. La tesina debe describir la biblioteca
   del RAG con esta composición real, no como "cuatro libros científicos".

## Hallazgo crítico — dato personal de un tercero embebido en un PDF versionado

`The-Muscle-and-Strength-Pyramid-Entrenamiento-espa.pdf` contiene, en el pie de varias
páginas, la marca de agua de una distribución personalizada:

```
Preparado en exclusiva para Jesús Gustavo Aguirre Valenciana,
jgustavo.avalenciana1@hotmail.com #13299859
```

Es decir: **el nombre completo y el correo personal de un tercero ajeno al proyecto** están
incrustados en un fichero que `git ls-files` confirma como **versionado** en el repositorio
(no está en `.gitignore`), cuyo remoto es `github.com/AbiGuadarrama/Agente-Iron-Progress`
(README, cabecera). Si ese repositorio es o llega a ser público, esos datos personales
quedan expuestos y buscables, y además el fichero es una copia de distribución personalizada
(muy probablemente no autorizada para redistribuir) de un libro con derechos de autor.

Esto no se corrige en este plan de documentación por decisión unilateral — es una decisión
de producto/legal del propietario del repositorio. Queda señalado aquí y se traslada
explícitamente a **F10.4** (cumplimiento de terceros y propiedad intelectual) para que la
tesina lo trate como lo que es: un hallazgo de auditoría, no un detalle a callar.

> **Acción recomendada (pendiente de decisión del usuario):** retirar el fichero del
> historial de Git (no basta con borrarlo en un commit nuevo, porque el blob persigue en el
> historial) o, como mínimo, dejar de indexarlo/distribuirlo y sustituirlo por una copia sin
> marca de agua personal si se dispone de licencia legítima.

## Cifras verificadas (sustituyen a las estimadas)

| Métrica | Valor verificado | Método |
|---|---|---|
| Páginas únicas indexadas | 997 | `pdfinfo` sobre los 4 ficheros tras dedup por MD5 |
| Fragmentos indexados | 3.811 | Ejecución real de `crear_base_conocimiento.py` (2026-08-27) |
| Colección `biblioteca_cientifica` antes de esta sesión | 0 fragmentos | `chromadb.PersistentClient(...).get_collection(...).count()` |
| Colecciones `perfil_dinamico_atleta` y `memoria_historica_atletas` | 0 fragmentos (sin tocar por esta tarea) | idem |
