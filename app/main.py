"""
Punto de Entrada Principal del Servidor SaaS (FastAPI).
Incluye:
- Inicialización automática de Base de Datos (Neon.tech PostgreSQL / SQLite local)
- Middlewares de Seguridad ISO/IEC 27001 y OWASP (Headers + Rate Limiting)
- Panel Web Visual Unificado en "/" (Login + Panel SuperAdmin + Panel Restaurante/Cocina)
- Vista Previa / Impresión de Ticket Térmico 58mm/80mm en "/ticket/{pedido_id}"
- Endpoint ultraligero /health para Render / Docker
"""
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.api.operaciones_api import router as operaciones_router
from app.api.panel_api import router as panel_router
from app.api.payment_api import router as payment_router
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

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    description=(
        "SaaS Multi-Tenant y Multi-Sucursal para Bot Único de Pedidos por WhatsApp "
        "con Panel Web de Restaurantes, Resumen de Chat con IA y Seguridad ISO 27001 / OWASP."
    ),
    lifespan=lifespan,
)

# Middlewares de Seguridad Empresarial
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

# Servir archivos estáticos (CSS / JS modular)
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Rutas de API (Autenticación + Panel Web Operativo + Pasarela de Pagos)
app.include_router(auth_router)
app.include_router(panel_router)
app.include_router(operaciones_router)
app.include_router(payment_router)


@app.get("/", response_class=HTMLResponse, tags=["Panel Web Visual (Fase 2)"])
def web_panel_home(request: Request):
    """Carga el Panel Web SaaS Unificado (Login, Panel SuperAdmin y Panel de Restaurantes)."""
    response = templates.TemplateResponse(request, "index.html", {"cache_bust": int(time.time())})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get(
    "/ticket/{pedido_id}",
    response_class=HTMLResponse,
    tags=["Impresión de Ticket Térmico (58mm/80mm)"],
)
def ver_ticket_termico(
    pedido_id: int, request: Request, db: Session = Depends(get_db)
):
    """Renderiza el formato de Comanda para Impresora Térmica de 58mm o 80mm."""
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")

    comercio = (
        db.query(TenantComercio)
        .filter(TenantComercio.id == pedido.tenant_id)
        .first()
    )
    sucursal = pedido.sucursal

    items = []
    for det in pedido.detalles:
        try:
            sin_list = json.loads(det.sin_ingredientes_json or "[]")
        except Exception:
            sin_list = []
        try:
            extras_list = json.loads(det.extras_json or "[]")
        except Exception:
            extras_list = []
        items.append(
            {
                "nombre_plato": det.nombre_plato,
                "cantidad": det.cantidad,
                "subtotal_linea": det.subtotal_linea,
                "sin_ingredientes": sin_list,
                "extras": extras_list,
                "notas_plato": det.notas_plato,
            }
        )

    ancho_ticket = sucursal.ancho_ticket_termico if sucursal else "80mm"
    fecha_formateada = (
        pedido.creado_en.strftime("%d/%m/%Y %H:%M") if pedido.creado_en else ""
    )

    return templates.TemplateResponse(
        request,
        "ticket.html",
        {
            "pedido": pedido,
            "comercio": comercio,
            "sucursal": sucursal,
            "items": items,
            "ancho_ticket": ancho_ticket,
            "fecha_formateada": fecha_formateada,
        },
    )


@app.get(
    "/pagos/checkout/{session_id}",
    response_class=HTMLResponse,
    tags=["Simulador de Pasarela de Pago"],
)
def checkout_simulador_view(
    session_id: str, pedido_id: int, request: Request, db: Session = Depends(get_db)
):
    """Página de checkout simulada de pasarela bancaria."""
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    comercio = db.query(TenantComercio).filter(TenantComercio.id == pedido.tenant_id).first()
    return templates.TemplateResponse(
        request,
        "checkout_simulator.html",
        {
            "pedido": pedido,
            "comercio": comercio,
            "session_id": session_id,
        },
    )


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


@app.get("/api/v1/system/overview", tags=["Estado del Sistema"])
def system_overview(db: Session = Depends(get_db)):
    """Resumen de verificación de la estructura SaaS creada en la Fase 1 y Fase 2."""
    planes = db.query(PlanSaaS).all()
    comercios = db.query(TenantComercio).all()
    sucursales = db.query(SucursalComercio).all()
    platos = db.query(ProductoPlato).all()
    pedidos = db.query(Pedido).all()

    return {
        "fase_1_completada": True,
        "fase_2_panel_web_completado": True,
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
