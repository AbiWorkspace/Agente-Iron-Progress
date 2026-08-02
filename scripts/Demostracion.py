
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services.proactividad import enviar_readiness

NUMERO = "527291395298"
NOMBRE = "campeón"


async def main() -> None:
    numero = sys.argv[1] if len(sys.argv) > 1 else NUMERO
    nombre = sys.argv[2] if len(sys.argv) > 2 else NOMBRE

    print(f"Enviando cuestionario de readiness a {numero} ({nombre})...")
    resultado = await enviar_readiness(numero, nombre)
    print(resultado)


if __name__ == "__main__":
    asyncio.run(main())
