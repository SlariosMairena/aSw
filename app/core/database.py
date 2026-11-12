"""
Gestor de Base de Datos SQLAlchemy 2.0 optimizado para Neon.tech (PostgreSQL Serverless)
y compatible con SQLite local para desarrollo inmediato.
"""
from typing import Generator
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import settings

# Configuración especial para Neon.tech Serverless PostgreSQL:
# - pool_pre_ping=True: Verifica la conexión antes de usarla (despierta a Neon si entró en Scale-to-Zero)
# - pool_recycle=300: Recicla conexiones cada 5 minutos para evitar cortes SSL por inactividad
if settings.is_postgres:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    metadata_obj = MetaData(schema=settings.DB_SCHEMA if settings.DB_SCHEMA else None)
else:
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    metadata_obj = MetaData()

    # Activar claves foráneas estrictas en SQLite para garantizar integridad Multi-Tenant
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base(metadata=metadata_obj)


def get_db() -> Generator[Session, None, None]:
    """Dependencia FastAPI que entrega una sesión de BD segura y la cierra al finalizar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

