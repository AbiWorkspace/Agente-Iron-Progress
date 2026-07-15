import sqlite3
import os
from services.db_service import ruta_db, inicializar_db
DB_PATH = ruta_db
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Borramos todo registro de alertas enviadas para forzar a que el bot crea que es una semana nueva
cursor.execute("DELETE FROM alertas_enviadas")
conn.commit()
conn.close()
print("¡Escudo anti-spam reseteado! El bot ahora tiene permiso de volver a enviar alertas.")