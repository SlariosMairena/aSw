"""
Servicio de configuración personalizada del Bot de WhatsApp por comercio.
Usa la tabla dedicada `config_bot_comercio` (sin alterar tablas existentes en Neon).
"""
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.core.demo_seeds import normalize_giro_code
from app.models.saas_models import ConfigBotComercio, TenantComercio

VALID_TONOS = ("CERCANO", "PROFESIONAL", "DIRECTO")


def get_giro_default_bot_presets(
    giro_raw: Optional[str], nombre_comercial: Optional[str] = ""
) -> Dict[str, str]:
    """
    Plantillas recomendadas de Políticas, Derivación a Humano y Reglas Extra
    para cada giro de negocio, listas para quedar guardadas desde el inicio.
    """
    g = normalize_giro_code(giro_raw)
    nom_low = (nombre_comercial or "").lower()

    if g == "SERVICIOS_CITAS":
        if "barber" in nom_low or "salon" in nom_low or "estetica" in nom_low:
            return {
                "politicas_faq": (
                    "• Llegar 5 a 10 minutos antes de tu cita para iniciar a tiempo.\n"
                    "• Si necesitas reprogramar o cancelar, avísanos con al menos 2 horas de anticipación.\n"
                    "• Tolerancia máxima de retraso: 10 minutos."
                ),
                "palabras_escalamiento": "evento, boda, grupo, factura, reclamo, queja, descuento especial",
                "reglas_extra": (
                    "• Confirmar siempre si el cliente desea solo corte, barba o combo completo antes de agendar.\n"
                    "• Si el cliente llega tarde o pregunta por atención sin cita, indicar amablemente que se atiende según disponibilidad en agenda."
                ),
            }
        return {
            "politicas_faq": (
                "• Llegar 10 minutos antes de su cita para registro previo.\n"
                "• Cancelaciones o reprogramaciones con al menos 3 horas de anticipación.\n"
                "• Si cuenta con radiografías o estudios previos, puede traerlos a su consulta."
            ),
            "palabras_escalamiento": "urgencia, dolor fuerte, sangrado, seguro medico, convenio, factura, ruc",
            "reglas_extra": (
                "• Nunca emitir diagnósticos médicos ni recetar medicamentos; indicar que el especialista lo evaluará en consulta.\n"
                "• Si el paciente reporta dolor intenso o urgencia, priorizar derivación inmediata a recepción humana."
            ),
        }

    if g == "FARMACIA":
        return {
            "politicas_faq": (
                "• Medicamentos controlados o antibióticos requieren presentar receta médica vigente.\n"
                "• Verificar siempre el producto al momento de recibir su despacho.\n"
                "• No se aceptan devoluciones en medicamentos refrigerados o empaques abiertos."
            ),
            "palabras_escalamiento": "receta, dosis, pediatria, embarazo, antibiotico, farmaceutico, factura, seguro",
            "reglas_extra": (
                "• Nunca diagnosticar enfermedades ni recomendar dosis personalizadas; derivar a un farmacéutico si piden orientación clínica.\n"
                "• Confirmar siempre la presentación deseada (blíster, caja o frasco) antes de cerrar el pedido."
            ),
        }

    if g == "TIENDA":
        return {
            "politicas_faq": (
                "• Cambios de talla o modelo dentro de los primeros 7 días con etiqueta y comprobante.\n"
                "• No se aceptan cambios ni devoluciones en accesorios, ropa interior o artículos en liquidación.\n"
                "• Envíos locales el mismo día en pedidos confirmados antes de las 4:00 PM."
            ),
            "palabras_escalamiento": "mayoreo, distribuidor, cambio de talla, garantia, factura, ruc, reclamo",
            "reglas_extra": (
                "• Confirmar siempre talla y color disponibles en el catálogo antes de confirmar el pedido.\n"
                "• Ofrecer empaque para regalo cuando el cliente mencione que es un obsequio."
            ),
        }

    if g == "SUPERMERCADO":
        return {
            "politicas_faq": (
                "• Revisar productos perecederos y cantidades al momento de recibir la entrega.\n"
                "• Tiempo estimado de entrega: 25 a 45 minutos según la zona.\n"
                "• Aceptamos efectivo (con vuelto) y transferencia bancaria."
            ),
            "palabras_escalamiento": "mayoreo, factura, ruc, producto danado, faltante, reclamo",
            "reglas_extra": (
                "• Si un producto o marca exacta no está disponible, preguntar siempre al cliente antes de sustituirlo.\n"
                "• Confirmar si desea bolsa térmica o hielo cuando pida bebidas frías o congelados."
            ),
        }

    if g == "CAFETERIA":
        return {
            "politicas_faq": (
                "• Contamos con opciones de leche entera, deslactosada y vegetales (almendra/avena).\n"
                "• Pasteles enteros o pedidos para eventos requieren mínimo 24 horas de anticipación.\n"
                "• Cambios en pedidos se aceptan antes de iniciar preparación en barra."
            ),
            "palabras_escalamiento": "pastel personalizado, evento, catering, factura, ruc, mayoreo, reclamo",
            "reglas_extra": (
                "• Al pedir café o bebida preparada, confirmar tamaño y si prefiere leche normal o vegetal.\n"
                "• Sugerir amablemente un acompañamiento de repostería cuando pidan solo bebida."
            ),
        }

    # RESTAURANTE (default)
    return {
        "politicas_faq": (
            "• Todos nuestros platillos se preparan al momento (tiempo promedio: 25 a 40 minutos).\n"
            "• Cambios o cancelaciones solo antes de que el pedido entre a cocina.\n"
            "• Pedidos grandes (más de 6 platos o eventos) requieren confirmación previa."
        ),
        "palabras_escalamiento": "factura, ruc, evento, catering, cumpleaños grande, queja, reclamo, pedido frio",
        "reglas_extra": (
            "• Confirmar siempre si desea retirar algún ingrediente o agregar extras antes de cerrar el pedido.\n"
            "• Si el cliente pide envío a domicilio, verificar siempre zona y referencia clara para el repartidor."
        ),
    }


def _default_bot_config(
    giro_raw: Optional[str] = None, nombre_comercial: Optional[str] = ""
) -> Dict[str, Any]:
    presets = get_giro_default_bot_presets(giro_raw, nombre_comercial)
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
        "politicas_faq": presets["politicas_faq"],
        "pedido_minimo": 0.0,
        "palabras_escalamiento": presets["palabras_escalamiento"],
        "reglas_extra": presets["reglas_extra"],
    }


def get_bot_config_dict(
    db: Session,
    tenant_id: int,
    giro: Optional[str] = None,
    nombre_comercial: Optional[str] = None,
) -> Dict[str, Any]:
    row = (
        db.query(ConfigBotComercio)
        .filter(ConfigBotComercio.tenant_id == int(tenant_id))
        .first()
    )
    if not row:
        if giro is None:
            com = (
                db.query(TenantComercio.giro, TenantComercio.nombre_comercial)
                .filter(TenantComercio.id == int(tenant_id))
                .first()
            )
            if com:
                giro, nombre_comercial = com[0], com[1]
        return _default_bot_config(giro, nombre_comercial or "")
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

