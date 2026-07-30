"""test_rectificar_serie.py — el atleta se corrige: UPDATE/DELETE, no un INSERT nuevo.

Ejercita `_rectificar_ultima_serie` (v13) contra una DB temporal, sin tocar la
del atleta. Cubre las tres decisiones que toma la función:

  1. ¿es un retiro, una corrección, o ninguna de las dos? (no rectificar de más)
  2. ¿QUÉ campo corrige? — no se adivina por el texto, se deduce contrastando el
     valor declarado erróneo con lo que hay guardado
  3. ¿qué se le devuelve al coach? — `bloque` se concatena tras "[SISTEMA — ", así
     que TIENE que cerrar el corchete o el prompt sale con un corchete huérfano

Uso:  python3 -u -m services.testbed.test_rectificar_serie
"""
import os
import sys
import tempfile

from services import db_service

# La DB de telemetría es una global de módulo: se redirige ANTES de importar
# llm_service para que ninguna escritura de la prueba caiga en data/.
_TMP = tempfile.mkdtemp(prefix="rectifica_")
db_service.ruta_db = os.path.join(_TMP, "telemetria_test.db")

from services import llm_service                                   # noqa: E402

USUARIO = "5215500000TEST"
fallos: list[str] = []


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def _sembrar(peso=100.0, reps=8, rpe=8.0, ejercicio="press banca") -> None:
    """Deja UNA serie conocida como la última del atleta."""
    db_service.inicializar_db()
    with db_service.conexion_db() as con:
        con.execute("DELETE FROM series_log WHERE numero_usuario = ?", (USUARIO,))
        con.execute(
            "INSERT INTO series_log (numero_usuario, fecha_hora, ejercicio, peso_kg,"
            " repeticiones, rpe_reportado) VALUES (?, datetime('now'), ?, ?, ?, ?)",
            (USUARIO, ejercicio, peso, reps, rpe))


def _ultima() -> dict | None:
    return db_service.obtener_ultima_serie(USUARIO)


def _n_series() -> int:
    with db_service.conexion_db(solo_lectura=True) as con:
        return con.execute("SELECT COUNT(*) FROM series_log WHERE numero_usuario = ?",
                           (USUARIO,)).fetchone()[0]


def main() -> int:
    # El recálculo del 1RM va contra la OTRA base (repositorio_atleta). Es
    # accesorio para lo que se prueba aquí y ya está envuelto en try/except.
    llm_service.guardar_rm = lambda *a, **k: None
    rect = llm_service._rectificar_ultima_serie

    print("\n1. Mensajes que NO son una rectificación (no tocar nada)")
    for texto in ["hice 100 kg por 8", "hoy me siento fatal", "ya termine mi sesion",
                  "que me toca mañana"]:
        _sembrar()
        chk(f"{texto!r} -> None", rect(USUARIO, texto) is None)
        chk("   la serie sigue intacta", (_ultima() or {}).get("peso_kg") == 100.0)

    print("\n2. Corrección de PESO con valor erróneo explícito")
    _sembrar(peso=100.0)
    r = rect(USUARIO, "perdón, no eran 100 kilos sino 90")
    chk("devuelve dict", isinstance(r, dict), str(r)[:70])
    chk("el peso quedó en 90", (_ultima() or {}).get("peso_kg") == 90.0,
        f"quedó {(_ultima() or {}).get('peso_kg')}")
    chk("NO insertó una serie nueva", _n_series() == 1, f"{_n_series()} filas")

    print("\n3. Corrección en el otro orden ('eran 90 no 100')")
    _sembrar(peso=100.0)
    r = rect(USUARIO, "espera, eran 90 kilos no 100")
    chk("devuelve dict", isinstance(r, dict))
    chk("el peso quedó en 90", (_ultima() or {}).get("peso_kg") == 90.0,
        f"quedó {(_ultima() or {}).get('peso_kg')}")

    print("\n4. El campo se deduce del VALOR, no de la unidad escrita")
    # 8 coincide con las reps, no con el peso: debe corregir repeticiones.
    _sembrar(peso=100.0, reps=8)
    r = rect(USUARIO, "me equivoqué, eran 6 no 8")
    u = _ultima() or {}
    chk("corrigió repeticiones", u.get("repeticiones") == 6, f"reps={u.get('repeticiones')}")
    chk("no tocó el peso", u.get("peso_kg") == 100.0, f"peso={u.get('peso_kg')}")

    print("\n5. Corrección de RPE")
    _sembrar(peso=100.0, reps=8, rpe=8.0)
    r = rect(USUARIO, "corrige el rpe, era 9 no 8")
    u = _ultima() or {}
    chk("corrigió el rpe", u.get("rpe_reportado") == 9.0, f"rpe={u.get('rpe_reportado')}")

    print("\n6. Valor erróneo que NO coincide con ningún campo -> no inventar")
    _sembrar(peso=100.0, reps=8, rpe=8.0)
    r = rect(USUARIO, "perdón, eran 55 no 42")
    u = _ultima() or {}
    chk("no rectifica a ciegas",
        u.get("peso_kg") == 100.0 and u.get("repeticiones") == 8 and u.get("rpe_reportado") == 8.0,
        f"quedó {u}")

    print("\n6b. Valor imposible -> no meterlo en el diario")
    _sembrar(peso=100.0, reps=8, rpe=8.0)
    r = rect(USUARIO, "perdón, no eran 100 kilos sino 900")
    chk("rechaza 900 kg", r is None and (_ultima() or {}).get("peso_kg") == 100.0,
        f"quedó {(_ultima() or {}).get('peso_kg')}")

    print("\n7. Retiro de la serie")
    _sembrar()
    r = rect(USUARIO, "olvídate de esa serie, no la hice")
    chk("devuelve dict", isinstance(r, dict))
    chk("la borró de verdad", _n_series() == 0, f"{_n_series()} filas")
    chk("etapa SERIE-RETIRO", (r or {}).get("etapa") == "SERIE-RETIRO")

    print("\n8. Retiro sin nada que retirar -> avisa, no miente")
    with db_service.conexion_db() as con:
        con.execute("DELETE FROM series_log WHERE numero_usuario = ?", (USUARIO,))
    r = rect(USUARIO, "borra esa última serie")
    chk("devuelve dict (no None)", isinstance(r, dict))
    chk("el bloque dice que NO se borró",
        "NO digas que la borraste" in (r or {}).get("bloque", ""))

    print("\n9. Contrato con el orquestador (así se consume en el turno)")
    _sembrar()
    r = rect(USUARIO, "perdón, no eran 100 kilos sino 90")
    for clave in ("etapa", "resumen", "bloque"):
        chk(f"trae '{clave}'", clave in (r or {}))
    # contenido_mensaje += "\n\n[SISTEMA — " + rectificacion["bloque"]
    inyectado = "[SISTEMA — " + (r or {}).get("bloque", "")
    chk("el bloque cierra el corchete de '[SISTEMA — '", inyectado.count("]") >= 1, inyectado[:60])

    print("\n" + "=" * 60)
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
