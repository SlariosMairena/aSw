"""
Servicio centralizado de Beneficios VIP y Tarjeta de Sellos (Fidelidad).
Single source of truth para Panel, Mini-Menú Web, Bot de WhatsApp, POS y Delivery.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.saas_models import (
    BeneficioCliente,
    ClienteFrecuente,
    ConfigFidelidadComercio,
    FidelidadCliente,
    Pedido,
    SucursalComercio,
    TenantComercio,
)
from app.services.whatsapp_provider import clean_whatsapp_phone, send_whatsapp_text


def get_or_create_config_fidelidad(db: Session, tenant_id: int) -> ConfigFidelidadComercio:
    cfg = (
        db.query(ConfigFidelidadComercio)
        .filter(ConfigFidelidadComercio.tenant_id == tenant_id)
        .first()
    )
    if not cfg:
        cfg = ConfigFidelidadComercio(
            tenant_id=tenant_id,
            activo=False,
            sellos_meta=5,
            monto_minimo_sello=150.0,
            premio_descuento_pct=15.0,
        )
        db.add(cfg)
        db.flush()
    return cfg


def get_or_create_fidelidad_cliente(
    db: Session, tenant_id: int, cliente_id: int
) -> FidelidadCliente:
    fid = (
        db.query(FidelidadCliente)
        .filter(FidelidadCliente.cliente_id == cliente_id)
        .first()
    )
    if not fid:
        fid = FidelidadCliente(
            tenant_id=tenant_id,
            cliente_id=cliente_id,
            es_vip=False,
            descuento_pct=0.0,
            sellos_actuales=0,
            premios_disponibles=0,
        )
        db.add(fid)
        db.flush()
    return fid


def get_cliente_beneficios(
    db: Session, tenant_id: int, telefono: str
) -> Dict[str, Any]:
    """Devuelve el perfil completo de beneficios VIP + Tarjeta de Sellos del cliente."""
    cfg = get_or_create_config_fidelidad(db, tenant_id)
    tel = "".join(ch for ch in (telefono or "") if ch.isdigit())
    base = {
        "cliente_id": None,
        "envio_gratis": False,
        "es_vip": False,
        "descuento_pct": 0.0,
        "sellos_actuales": 0,
        "premios_disponibles": 0,
        "programa_sellos_activo": bool(cfg.activo),
        "sellos_meta": int(cfg.sellos_meta or 5),
        "monto_minimo_sello": float(cfg.monto_minimo_sello or 150.0),
        "premio_descuento_pct": float(cfg.premio_descuento_pct or 15.0),
        "descuento_efectivo_pct": 0.0,
        "usa_premio_sellos": False,
    }
    if not tel or tel == "00000000":
        return base

    cli = (
        db.query(ClienteFrecuente)
        .filter(
            ClienteFrecuente.tenant_id == tenant_id,
            ClienteFrecuente.telefono_whatsapp == tel,
        )
        .first()
    )
    if not cli:
        return base

    ben = (
        db.query(BeneficioCliente)
        .filter(BeneficioCliente.cliente_id == cli.id)
        .first()
    )
    fid = (
        db.query(FidelidadCliente)
        .filter(FidelidadCliente.cliente_id == cli.id)
        .first()
    )

    envio_gratis = bool(ben and ben.envio_gratis)
    descuento_vip = float(fid.descuento_pct or 0.0) if fid else 0.0
    es_vip = bool((fid and fid.es_vip) or envio_gratis or descuento_vip > 0)
    sellos = int(fid.sellos_actuales or 0) if fid else 0
    premios = int(fid.premios_disponibles or 0) if fid else 0

    premio_pct = float(cfg.premio_descuento_pct or 15.0) if (cfg.activo and premios > 0) else 0.0
    usa_premio = bool(premio_pct > 0 and premio_pct >= descuento_vip)
    descuento_efectivo = premio_pct if usa_premio else descuento_vip

    base.update(
        {
            "cliente_id": cli.id,
            "envio_gratis": envio_gratis,
            "es_vip": es_vip,
            "descuento_pct": descuento_vip,
            "sellos_actuales": sellos,
            "premios_disponibles": premios,
            "descuento_efectivo_pct": round(descuento_efectivo, 2),
            "usa_premio_sellos": usa_premio,
        }
    )
    return base


def calcular_descuento_cliente(
    subtotal: float, beneficios: Dict[str, Any]
) -> Tuple[float, str, bool]:
    """Calcula (monto_descuento, etiqueta_nota, consume_premio_sellos)."""
    pct = float(beneficios.get("descuento_efectivo_pct") or 0.0)
    if pct <= 0 or subtotal <= 0:
        return 0.0, "", False
    monto = round(subtotal * (pct / 100.0), 2)
    usa_premio = bool(beneficios.get("usa_premio_sellos"))
    label = (
        f"Premio Tarjeta de Sellos (-{pct:g}%)"
        if usa_premio
        else f"Descuento VIP (-{pct:g}%)"
    )
    return monto, label, usa_premio


def consumir_premio_sellos(db: Session, tenant_id: int, telefono: str) -> None:
    """Descuenta 1 premio de tarjeta de sellos cuando el cliente lo redime en un pedido."""
    tel = "".join(ch for ch in (telefono or "") if ch.isdigit())
    if not tel or tel == "00000000":
        return
    cli = (
        db.query(ClienteFrecuente)
        .filter(
            ClienteFrecuente.tenant_id == tenant_id,
            ClienteFrecuente.telefono_whatsapp == tel,
        )
        .first()
    )
    if not cli:
        return
    fid = (
        db.query(FidelidadCliente)
        .filter(FidelidadCliente.cliente_id == cli.id)
        .first()
    )
    if fid and (fid.premios_disponibles or 0) > 0:
        fid.premios_disponibles = max(0, int(fid.premios_disponibles) - 1)
        fid.actualizado_en = datetime.now(timezone.utc)


def otorgar_sello_por_entrega(db: Session, pedido: Pedido) -> Optional[Dict[str, Any]]:
    """
    Suma 1 sello al cliente cuando su pedido pasa a ENTREGADO (si el programa está activo
    y cumple el monto mínimo). Envía notificación de avance o premio por WhatsApp.
    """
    if not pedido or not pedido.tenant_id:
        return None
    cfg = get_or_create_config_fidelidad(db, pedido.tenant_id)
    if not cfg.activo:
        return None

    monto_base = float(pedido.subtotal or pedido.total or 0.0)
    if monto_base < float(cfg.monto_minimo_sello or 0.0):
        return None

    tel = clean_whatsapp_phone(pedido.telefono_cliente or "")
    if not tel or tel == "00000000":
        return None

    cli = (
        db.query(ClienteFrecuente)
        .filter(
            ClienteFrecuente.tenant_id == pedido.tenant_id,
            ClienteFrecuente.telefono_whatsapp == tel,
        )
        .first()
    )
    if not cli:
        return None

    fid = get_or_create_fidelidad_cliente(db, pedido.tenant_id, cli.id)
    meta = max(1, int(cfg.sellos_meta or 5))
    fid.sellos_actuales = int(fid.sellos_actuales or 0) + 1
    gano_premio = False
    if fid.sellos_actuales >= meta:
        fid.sellos_actuales = fid.sellos_actuales - meta
        fid.premios_disponibles = int(fid.premios_disponibles or 0) + 1
        gano_premio = True
    fid.actualizado_en = datetime.now(timezone.utc)
    db.flush()

    comercio = db.query(TenantComercio).filter(TenantComercio.id == pedido.tenant_id).first()
    nombre_comercio = comercio.nombre_comercial if comercio else "nuestra tienda"
    premio_pct = float(cfg.premio_descuento_pct or 15.0)

    if gano_premio:
        msg = (
            f"¡Felicidades {cli.nombre_cliente or 'cliente'}! 🎉\n"
            f"Completaste tu Tarjeta de Sellos en *{nombre_comercio}*.\n"
            f"Ganaste *{premio_pct:g}% de descuento automático* en tu próximo pedido."
        )
    else:
        faltan = max(1, meta - fid.sellos_actuales)
        msg = (
            f"⭐ *Tarjeta de Sellos — {nombre_comercio}*\n"
            f"¡Sumaste 1 sello con tu pedido {pedido.folio}!\n"
            f"Progreso: *{fid.sellos_actuales} de {meta} sellos* "
            f"(te faltan {faltan} para ganar *{premio_pct:g}% de descuento*)."
        )
    try:
        send_whatsapp_text(tel, msg)
    except Exception:
        pass

    return {
        "sellos_actuales": fid.sellos_actuales,
        "sellos_meta": meta,
        "premios_disponibles": fid.premios_disponibles,
        "gano_premio": gano_premio,
    }


def notificar_beneficio_vip_whatsapp(
    db: Session,
    tenant_id: int,
    cliente: ClienteFrecuente,
    envio_gratis: bool,
    descuento_pct: float,
    es_vip: bool,
) -> bool:
    """Envía WhatsApp automático al cliente cuando el dueño le activa ventajas VIP."""
    tel = clean_whatsapp_phone(cliente.telefono_whatsapp or "")
    if not tel or tel == "00000000":
        return False

    ventajas = []
    if envio_gratis:
        ventajas.append("• *Envío Gratis* en tus pedidos a domicilio")
    if descuento_pct > 0:
        ventajas.append(f"• *{descuento_pct:g}% de Descuento VIP* automático en todos tus pedidos")
    if es_vip and not ventajas:
        ventajas.append("• *Atención Prioritaria VIP*")

    if not ventajas:
        return False

    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    suc = (
        db.query(SucursalComercio)
        .filter(SucursalComercio.tenant_id == tenant_id, SucursalComercio.activa.is_(True))
        .first()
    )
    nombre_com = comercio.nombre_comercial if comercio else "nuestra tienda"
    base_url = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    menu_link = (
        f"\n\nPide aquí cuando gustes:\n{base_url}/m/{suc.codigo_whatsapp}?tel={tel}"
        if (base_url and suc and suc.codigo_whatsapp)
        else ""
    )

    msg = (
        f"🌟 *¡Hola {cliente.nombre_cliente or 'cliente'}!*\n"
        f"En *{nombre_com}* te activamos tus beneficios de *Cliente VIP*:\n"
        + "\n".join(ventajas)
        + f"{menu_link}"
    )
    try:
        send_whatsapp_text(tel, msg)
        return True
    except Exception:
        return False

