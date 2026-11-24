"""
Servicio de configuración personalizada del Bot de WhatsApp por comercio.
Usa la tabla dedicada `config_bot_comercio` (sin alterar tablas existentes en Neon).
"""
from typing import Any, Dict
from sqlalchemy.orm import Session

from app.models.saas_models import ConfigBotComercio

VALID_TONOS = ("CERCANO", "PROFESIONAL", "DIRECTO")


def _default_bot_config() -> Dict[str, Any]:
    return {
        "bot_activo": True,
        "nombre_asistente": "",
        "tono_bot": "CERCANO",
        "saludo_personalizado": "",
        "mensaje_cerrado": "",
        "permitir_fuera_horario": True,
        "promo_del_dia": "",
        "ubicacion_texto": "",
        "ubicacion_maps_url": "",
        "politicas_faq": "",
        "pedido_minimo": 0.0,
        "palabras_escalamiento": "",
        "reglas_extra": "",
    }


def get_bot_config_dict(db: Session, tenant_id: int) -> Dict[str, Any]:
    row = (
        db.query(ConfigBotComercio)
        .filter(ConfigBotComercio.tenant_id == int(tenant_id))
        .first()
    )
    if not row:
        return _default_bot_config()
    tono = (row.tono_bot or "CERCANO").upper().strip()
    if tono not in VALID_TONOS:
        tono = "CERCANO"
    return {
        "bot_activo": bool(row.bot_activo),
        "nombre_asistente": (row.nombre_asistente or "").strip(),
        "tono_bot": tono,
        "saludo_personalizado": (row.saludo_personalizado or "").strip(),
        "mensaje_cerrado": (row.mensaje_cerrado or "").strip(),
        "permitir_fuera_horario": bool(row.permitir_fuera_horario),
        "promo_del_dia": (row.promo_del_dia or "").strip(),
        "ubicacion_texto": (row.ubicacion_texto or "").strip(),
        "ubicacion_maps_url": (row.ubicacion_maps_url or "").strip(),
        "politicas_faq": (row.politicas_faq or "").strip(),
        "pedido_minimo": round(float(row.pedido_minimo or 0.0), 2),
        "palabras_escalamiento": (row.palabras_escalamiento or "").strip(),
        "reglas_extra": (row.reglas_extra or "").strip(),
    }


def save_bot_config(db: Session, tenant_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    row = (
        db.query(ConfigBotComercio)
        .filter(ConfigBotComercio.tenant_id == int(tenant_id))
        .first()
    )
    if not row:
        row = ConfigBotComercio(tenant_id=int(tenant_id))
        db.add(row)

    if "bot_activo" in data and data["bot_activo"] is not None:
        row.bot_activo = bool(data["bot_activo"])
    if "nombre_asistente" in data and data["nombre_asistente"] is not None:
        row.nombre_asistente = str(data["nombre_asistente"]).strip()[:80] or None
    if "tono_bot" in data and data["tono_bot"] is not None:
        t = str(data["tono_bot"]).upper().strip()
        row.tono_bot = t if t in VALID_TONOS else "CERCANO"
    if "saludo_personalizado" in data and data["saludo_personalizado"] is not None:
        row.saludo_personalizado = str(data["saludo_personalizado"]).strip()[:350] or None
    if "mensaje_cerrado" in data and data["mensaje_cerrado"] is not None:
        row.mensaje_cerrado = str(data["mensaje_cerrado"]).strip()[:350] or None
    if "permitir_fuera_horario" in data and data["permitir_fuera_horario"] is not None:
        row.permitir_fuera_horario = bool(data["permitir_fuera_horario"])
    if "promo_del_dia" in data and data["promo_del_dia"] is not None:
        row.promo_del_dia = str(data["promo_del_dia"]).strip()[:280] or None
    if "ubicacion_texto" in data and data["ubicacion_texto"] is not None:
        row.ubicacion_texto = str(data["ubicacion_texto"]).strip()[:300] or None
    if "ubicacion_maps_url" in data and data["ubicacion_maps_url"] is not None:
        row.ubicacion_maps_url = str(data["ubicacion_maps_url"]).strip()[:400] or None
    if "politicas_faq" in data and data["politicas_faq"] is not None:
        row.politicas_faq = str(data["politicas_faq"]).strip()[:600] or None
    if "pedido_minimo" in data and data["pedido_minimo"] is not None:
        try:
            row.pedido_minimo = max(0.0, round(float(data["pedido_minimo"]), 2))
        except Exception:
            row.pedido_minimo = 0.0
    if "palabras_escalamiento" in data and data["palabras_escalamiento"] is not None:
        row.palabras_escalamiento = str(data["palabras_escalamiento"]).strip()[:300] or None
    if "reglas_extra" in data and data["reglas_extra"] is not None:
        row.reglas_extra = str(data["reglas_extra"]).strip()[:600] or None

    db.commit()
    db.refresh(row)
    return get_bot_config_dict(db, tenant_id)
