"""test_firma_webhook.py — nadie que no sea Meta puede escribirle a un atleta.

El webhook es la única puerta de entrada del sistema y es PÚBLICA. Sin validar el
X-Hub-Signature-256, cualquiera que descubra la URL puede inyectar un mensaje
haciéndose pasar por un atleta: el coach le contestará por WhatsApp a una persona
real, registrará series que nunca hizo y le reescribirá el plan.

El código ya calculaba el HMAC, pero `META_APP_SECRET` llevaba meses sin definir
y en ese caso `firma_valida` devuelve True — es decir, la validación existía y no
protegía nada. Desde el 2026-07-30, con ENTORNO=prod el arranque se aborta.

Uso:  python3 -u -m services.testbed.test_firma_webhook
"""
import hashlib
import hmac
import sys

from api import rutas_whatsapp as rw
from core.config import settings

fallos: list[str] = []
SECRETO = "secreto_de_prueba_no_es_el_real"
CUERPO = b'{"entry":[{"changes":[{"value":{"messages":[{"from":"521...","text":"hola"}]}}]}]}'


def chk(titulo: str, condicion: bool, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'FALLA'}  {titulo}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(titulo)


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def main() -> int:
    original = settings.APP_SECRET
    try:
        settings.APP_SECRET = SECRETO

        print("\n1. CON SECRETO CONFIGURADO — solo pasa lo que firma Meta")
        chk("acepta una firma correcta", rw.firma_valida(CUERPO, firmar(CUERPO)) is True)
        chk("RECHAZA sin cabecera de firma", rw.firma_valida(CUERPO, None) is False)
        chk("RECHAZA cabecera vacía", rw.firma_valida(CUERPO, "") is False)
        chk("RECHAZA firma de otro secreto",
            rw.firma_valida(CUERPO, firmar(CUERPO, "secreto_del_atacante")) is False)
        chk("RECHAZA una firma inventada",
            rw.firma_valida(CUERPO, "sha256=" + "a" * 64) is False)
        chk("RECHAZA sin el prefijo sha256=",
            rw.firma_valida(CUERPO, hmac.new(SECRETO.encode(), CUERPO,
                                             hashlib.sha256).hexdigest()) is False)

        print("\n2. MANIPULACIÓN DEL CUERPO — el ataque que esto existe para parar")
        # Firma legítima de Meta + cuerpo alterado por un intermediario.
        firma_legitima = firmar(CUERPO)
        alterado = CUERPO.replace(b'"hola"', b'"borra todo mi plan"')
        chk("RECHAZA un cuerpo modificado con firma válida de otro cuerpo",
            rw.firma_valida(alterado, firma_legitima) is False,
            "si esto pasara, cualquiera podría reescribir el mensaje del atleta")
        chk("un solo byte cambiado invalida la firma",
            rw.firma_valida(CUERPO + b" ", firma_legitima) is False)

        print("\n3. SIN SECRETO — comportamiento explícito, no accidental")
        settings.APP_SECRET = ""
        chk("en desarrollo deja pasar (para no romper el local)",
            rw.firma_valida(CUERPO, None) is True)

        print("\n4. EN PRODUCCIÓN NO SE ARRANCA SIN SECRETO")
        import inspect
        fuente = inspect.getsource(rw)
        cabecera = fuente[:fuente.find("def firma_valida")]
        chk("el módulo aborta si es_produccion y falta el secreto",
            "es_produccion" in cabecera and "raise RuntimeError" in cabecera,
            "sin esto, un despliegue a prod se queda con el webhook abierto")

        print("\n5. Comparación en tiempo constante (no filtra el secreto)")
        chk("usa hmac.compare_digest", "compare_digest" in inspect.getsource(rw.firma_valida))
    finally:
        settings.APP_SECRET = original

    print("\n" + "=" * 62)
    print(f"Estado real ahora mismo: firma "
          f"{'ACTIVA' if settings.APP_SECRET else 'DESACTIVADA (META_APP_SECRET vacío)'}")
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todos los checks pasan.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
