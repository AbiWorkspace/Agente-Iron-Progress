# Cap. 10 · Modelo de datos y persistencia

> Estado: **hecho (borrador)** · Tarea: F6.9 · Fecha: 2026-08-27
> Ficheros fuente: `data/iron_progress.db`, `data/iron_progress_telemetria.db`
> (esquema extraído en vivo con `sqlite3` — F1.2), `data/chroma_db/`

---

## 10.1 Esquema relacional verificado (DDL real, no reconstruido de memoria)

### `iron_progress.db` — perfil, marcas y auditoría (3 tablas)

```sql
CREATE TABLE atletas (
    numero_usuario   TEXT PRIMARY KEY,
    peso_kg          REAL,
    altura_m         REAL,
    edad             INTEGER,
    genero           TEXT,
    porcentaje_grasa REAL,
    dias_semana      INTEGER,
    meta             TEXT,
    creado_en        TEXT NOT NULL,
    actualizado_en   TEXT NOT NULL,
    incremento_kg    REAL
);

CREATE TABLE historial_perfil (      -- auditoría de cambios de ficha (F6.9)
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario TEXT NOT NULL,
    campo          TEXT NOT NULL,
    valor          TEXT,
    fecha          TEXT NOT NULL
);

CREATE TABLE marcas_rm (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario TEXT NOT NULL,
    ejercicio      TEXT NOT NULL,
    rm_estimado    REAL NOT NULL,
    peso_origen    REAL,
    reps_origen    INTEGER,
    fuente         TEXT DEFAULT 'chat',
    fecha          TEXT NOT NULL
);
```

### `iron_progress_telemetria.db` — series, readiness y eventos (4 tablas)

```sql
CREATE TABLE series_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario        TEXT    NOT NULL,
    fecha_hora            TEXT    NOT NULL,
    ejercicio             TEXT    NOT NULL,
    peso_kg               REAL    NOT NULL,
    repeticiones          INTEGER NOT NULL,
    rpe_reportado         REAL,
    vbt_velocidad_media   REAL,
    vbt_velocidad_pico    REAL,
    vbt_perdida_velocidad REAL,
    rom_cm                REAL,
    dolor_articular       TEXT,
    calidad_tecnica       TEXT
);

CREATE TABLE alertas_enviadas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario TEXT    NOT NULL,
    tipo           TEXT    NOT NULL,
    enviada_en     TEXT    DEFAULT (datetime('now')),
    semana_iso     TEXT
);

CREATE TABLE readiness (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario  TEXT NOT NULL,
    fecha           TEXT NOT NULL,       -- YYYY-MM-DD
    sueno_horas     REAL,
    sueno_calidad   INTEGER,             -- 1-5
    estres          INTEGER,             -- 1-5 (5 = "frito")
    dolor           TEXT,                -- zona o 'ninguno'
    score           REAL NOT NULL,       -- 0.0-1.0 consolidado
    fuente          TEXT DEFAULT 'botones',  -- botones | texto | inferido
    UNIQUE (numero_usuario, fecha)
);

CREATE TABLE eventos_autorregulacion (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_usuario TEXT NOT NULL,
    fecha          TEXT NOT NULL,        -- ISO datetime
    tipo           TEXT NOT NULL,        -- dup_ajuste | dup_descarga | mrv_alerta
    ejercicio      TEXT,
    detalle_json   TEXT                  -- payload completo del evento (ver Cap. 9, autorregulación)
);
```

**Nota de verificación:** este DDL se extrajo en vivo consultando
`sqlite_master` sobre las dos bases reales del entorno de desarrollo (comando en F1.2), no se
transcribió de memoria ni de la documentación anterior. Ambas bases suman **7 tablas**, la
cifra que el PDF v9.1 ya reportaba correctamente.

## 10.2 Diagrama entidad-relación (textual)

```
atletas (1) ──── (N) historial_perfil       [auditoría: quién cambió qué campo y cuándo]
atletas (1) ──── (N) marcas_rm              [una fila por marca registrada, con su fuente]
atletas (1) ──── (N) series_log             [el diario de entrenamiento — la tabla más
                                              consultada; alimenta e1RM, ACWR, MRV, VBT]
atletas (1) ──── (N) alertas_enviadas       [control de idempotencia de avisos proactivos]
atletas (1) ──── (0..1 por fecha) readiness [UNIQUE(numero_usuario, fecha): una sola
                                              respuesta de disponibilidad por día]
atletas (1) ──── (N) eventos_autorregulacion [bitácora de ajustes automáticos, auditable]
```

La relación entre `atletas` y las demás tablas es siempre 1:N por `numero_usuario`, sin
claves foráneas declaradas explícitamente en el DDL (SQLite no las impone por defecto) — la
integridad referencial es responsabilidad del código de la aplicación, no del motor de base
de datos. Es una limitación de robustez a nivel de esquema, no señalada en el PDF v9.1
original, que se traslada al inventario de deuda técnica (Cap. 19).

## 10.3 Esquema de las colecciones vectoriales

Verificado en vivo contra el servidor ChromaDB real (F1.2, F1.5):

| Colección | Contenido | Tamaño verificado (2026-08-27) |
|---|---|---|
| `biblioteca_cientifica` | Fragmentos de la literatura científica indexada (Cap. 2) | 3.811 fragmentos, embeddings `nomic-embed-text` |
| `perfil_dinamico_atleta` | Expediente del atleta: molestias, material disponible, horarios, hábitos | 0 en el entorno de esta sesión (chroma_db reciente/reseteado — no indica ausencia de código, ver nota) |
| `memoria_historica_atletas` | Bitácora narrativa: mesociclos cerrados, lesiones antiguas | 0 — coherente con el hallazgo de memoria previo del proyecto ("la bitácora narrativa no tiene escritor": la colección se lee pero ningún punto del sistema la escribe automáticamente, solo el script de siembra de demostración) |

## 10.4 Por qué cuatro almacenes: poliglotía justificada por patrón de acceso, no por preferencia

El PDF v9.1 (Cap. 11) ya justifica la elección de cada tecnología por su patrón de acceso —un
log de eventos consultado por ventana temporal pide SQL, un documento inmutable que se lee
completo pide fichero, una consulta por significado pide índice vectorial—; esta sección no
repite esa justificación, la confirma con el DDL real de arriba: `series_log` es exactamente
un log de eventos con fecha (consultado por ventana en `leer_series`, Cap. 9), y las
colecciones vectoriales son exactamente consultas por significado semántico (Cap. 2 §2.7), no
por clave exacta — no habría forma natural de expresar "¿me duele algo?" como una consulta SQL
sin antes decidir qué campo mirar, que es precisamente lo que el índice vectorial evita tener
que decidir de antemano.
