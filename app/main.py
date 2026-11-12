"""
Punto de Entrada Principal del Servidor SaaS (FastAPI).
Incluye:
- Inicialización automática de Base de Datos (Neon.tech PostgreSQL / SQLite local)
- Middlewares de Seguridad ISO/IEC 27001 y OWASP (Headers + Rate Limiting)
- Endpoint ultraligero /health para mantener activo Render sin despertar innecesariamente a Neon.tech
- Rutas de Autenticación y Estado de Arquitectura de Fase 1
"""
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.core.config import settings
from app.core.database import get_db
from app.core.init_db import init_database
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.security import build_whatsapp_branch_deep_link
from app.models.saas_models import (
    Pedido,
    PlanSaaS,
    ProductoPlato,
    SucursalComercio,
    TenantComercio,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "SaaS Multi-Tenant y Multi-Sucursal para Bot Único de Pedidos por WhatsApp "
        "con Panel Web de Restaurantes, Resumen de Chat con IA y Seguridad ISO 27001 / OWASP."
    ),
    lifespan=lifespan,
)

# Middlewares de Seguridad Empresarial
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

# Rutas de Autenticación
app.include_router(auth_router)


@app.get("/health", tags=["Infraestructura y Salud (Render / Docker)"])
def health_check():
    """
    Endpoint ultraligero para Render / UptimeRobot / Docker HEALTHCHECK.
    No ejecuta consultas pesadas para permitir que Neon.tech descanse cuando no hay pedidos.
    """
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
        "database_engine": "postgresql_neon" if settings.is_postgres else "sqlite_local",
        "whatsapp_provider": settings.WHATSAPP_PROVIDER,
        "payment_mode": settings.PAYMENT_OPERATION_MODE,
        "table_reservations_enabled": settings.ENABLE_TABLE_RESERVATIONS_WITH_PAYMENT,
    }


@app.get("/api/v1/system/overview", tags=["Estado de Fase 1"])
def system_overview(db: Session = Depends(get_db)):
    """Resumen de verificación de la estructura SaaS creada en la Fase 1."""
    planes = db.query(PlanSaaS).all()
    comercios = db.query(TenantComercio).all()
    sucursales = db.query(SucursalComercio).all()
    platos = db.query(ProductoPlato).all()
    pedidos = db.query(Pedido).all()

    return {
        "fase_1_completada": True,
        "planes_saas": [
            {
                "codigo": p.codigo,
                "nombre": p.nombre,
                "precio_mensual_usd": p.precio_mensual_usd,
                "limite_pedidos_mes": p.limite_pedidos_mes,
                "porcentaje_comision": p.porcentaje_comision_pedido,
                "max_sucursales": p.max_sucursales,
            }
            for p in planes
        ],
        "comercios_registrados": len(comercios),
        "sucursales_con_link_whatsapp_opcion_b": [
            {
                "sucursal": s.nombre_sucursal,
                "codigo_whatsapp": s.codigo_whatsapp,
                "ancho_ticket": s.ancho_ticket_termico,
                "deep_link_whatsapp": build_whatsapp_branch_deep_link(
                    settings.WHATSAPP_BOT_PUBLIC_NUMBER,
                    s.codigo_whatsapp,
                    s.comercio.nombre_comercial,
                ),
            }
            for s in sucursales
        ],
        "platos_con_ingredientes": [
            {
                "plato": pl.nombre,
                "precio_base": pl.precio_base,
                "ingredientes_removibles_sin_costo": [
                    ing.nombre
                    for ing in pl.ingredientes_opciones
                    if ing.tipo == "INGREDIENTE_BASE"
                ],
                "extras_y_variantes": [
                    {"nombre": ing.nombre, "tipo": ing.tipo, "precio": ing.precio_adicional}
                    for ing in pl.ingredientes_opciones
                    if ing.tipo in ("EXTRA", "VARIANTE")
                ],
            }
            for pl in platos
        ],
        "pedidos_registrados": [
            {
                "folio": ped.folio,
                "estado": ped.estado_pedido,
                "total": ped.total,
                "metodo_pago": ped.metodo_pago,
                "resumen_chat_ia": ped.resumen_chat_ia,
            }
            for ped in pedidos
        ],
    }

