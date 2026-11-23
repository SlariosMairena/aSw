"""
Motor Conversacional Natural, Inteligente y Consciente del Pedido en Curso.
- Mensajes cortos, cálidos y cómodos en celular (una pregunta por mensaje).
- Interpretación tolerante a errores de escritura (typos y lenguaje cotidiano):
  ej. "no quiero pepinillas" -> "sin Pepinillos", "quitale la sebolla" -> "sin Cebolla",
  "dos", "efektivo", "domisilio", nombres aproximados de productos/extras.
- Conciencia del pedido activo (SIGUIENDO_PEDIDO):
  mantiene el chat enfocado en el pedido en curso hasta que se entregue (ENTREGADO)
  o cancele (CANCELADO) en el panel, cerrando el ciclo automáticamente al finalizar.
"""
import difflib
import json
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.demo_seeds import normalize_giro_code
from app.core.realtime import publish_tenant_event
from app.models.saas_models import (
    CategoriaMenu,
    ClienteFrecuente,
    DetallePedido,
    IngredienteOpcionPlato,
    MensajeChatWhatsApp,
    MesaZona,
    PagoTransaccion,
    Pedido,
    ProductoImagen,
    ProductoPlato,
    ReservacionMesa,
    SesionChatWhatsApp,
    StockSucursal,
    SucursalComercio,
    TenantComercio,
    ZonaEnvioSucursal,
)
from app.services.ai_router import (
    detect_escalation_or_complaint_ai,
    interpret_natural_order_message,
    run_full_ai_order_turn,
    validate_and_normalize_address_ai,
)
from app.services.payment_service import simulated_gateway
from app.services.whatsapp_provider import clean_whatsapp_phone

# Flag: habilitar pedidos completos en lenguaje libre con IA cuando cada cliente tenga número dedicado
ENABLE_AI_NATURAL_ORDER = False

# Palabras disparadoras para consultar a la IA solo ante posibles reclamos o casos especiales
# (evita consumir cuota de Gemini en respuestas simples como "1", "2", "Hola", etc.)
_COMPLAINT_TRIGGER_WORDS = [
    "reclamo", "queja", "molesto", "enojado", "pesimo", "horrible",
    "incompleto", "equivocado", "frio", "malo", "danado", "roto",
    "cobraron", "cobro", "reembolso", "devolucion", "devolver",
    "factura", "ruc", "cotizacion", "evento", "encargado", "gerente",
    "problema", "no llego", "nunca llego", "tardo mucho",
]


def _looks_like_complaint_or_special_case(norm_text: str) -> bool:
    if not norm_text or len(norm_text) < 10:
        return False
    return _fuzzy_contains_any(norm_text, _COMPLAINT_TRIGGER_WORDS, cutoff=0.82)

# Tiempo máximo de inactividad (en minutos) antes de dar por terminada
# una conversación abandonada (sin pedido en curso) para ahorrar memoria y BD.
SESSION_IDLE_TIMEOUT_MINUTES = 8
_LAST_SWEEP_AT: Optional[datetime] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _expire_session_if_idle(db: Session, sesion: SesionChatWhatsApp, now: datetime) -> bool:
    """
    Si la sesión lleva más de SESSION_IDLE_TIMEOUT_MINUTES sin respuesta y NO tiene
    un pedido activo en curso, la marca como terminada (EXPIRADA) y elimina mensajes
    abandonados si nunca generó un pedido. Retorna True si fue expirada.
    """
    if not sesion or not sesion.activa:
        return False
    if sesion.estado_conversacion == "SIGUIENDO_PEDIDO":
        return False

    last_ts = _as_utc(sesion.ultimo_mensaje_en)
    if not last_ts or (now - last_ts) <= timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        return False

    sesion.activa = False
    sesion.estado_conversacion = "EXPIRADA"
    sesion.carrito_json = "{}"

    # Si esta sesión nunca generó un pedido real, purgar sus mensajes temporales para no inflar la BD
    tiene_pedido = (
        db.query(Pedido.id).filter(Pedido.sesion_chat_id == sesion.id).first() is not None
    )
    if not tiene_pedido:
        db.query(MensajeChatWhatsApp).filter(
            MensajeChatWhatsApp.sesion_id == sesion.id
        ).delete(synchronize_session=False)
    return True


def cleanup_expired_whatsapp_sessions(db: Session, force: bool = False) -> int:
    """
    Cierra en lote sesiones abandonadas (> 8 min sin respuesta y sin pedido en curso).
    Se ejecuta de forma ligera (máximo 1 vez cada 2 min salvo force=True) sin hilos extra.
    """
    global _LAST_SWEEP_AT
    now = _utcnow()
    if not force and _LAST_SWEEP_AT and (now - _LAST_SWEEP_AT) < timedelta(minutes=2):
        return 0
    _LAST_SWEEP_AT = now

    cutoff = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
    candidatas = (
        db.query(SesionChatWhatsApp)
        .filter(
            SesionChatWhatsApp.activa.is_(True),
            SesionChatWhatsApp.estado_conversacion != "SIGUIENDO_PEDIDO",
            SesionChatWhatsApp.ultimo_mensaje_en < cutoff,
        )
        .limit(100)
        .all()
    )
    cerradas = 0
    for s in candidatas:
        if _expire_session_if_idle(db, s, now):
            cerradas += 1
    if cerradas:
        db.flush()
    return cerradas


def _normalize_text(text: str) -> str:
    """Quita tildes, pasa a minúsculas y limpia signos para comparar con tolerancia a typos."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    no_accents = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\s]", " ", no_accents).strip()


def _fuzzy_contains_any(norm_text: str, keywords: List[str], cutoff: float = 0.78) -> bool:
    """Detecta si el texto contiene alguna palabra clave incluso con errores leves de ortografía."""
    if not norm_text:
        return False
    tokens = norm_text.split()
    for kw in keywords:
        kw_n = _normalize_text(kw)
        if kw_n in norm_text:
            return True
        for tok in tokens:
            if len(tok) >= 3 and difflib.SequenceMatcher(None, tok, kw_n).ratio() >= cutoff:
                return True
    return False


def _fuzzy_match_options(query: str, names: List[str], cutoff: float = 0.62) -> Optional[int]:
    """Devuelve el índice de la opción cuyo nombre mejor coincide (exacto, substring o fuzzy)."""
    q = _normalize_text(query)
    if not q or not names:
        return None
    norm_names = [_normalize_text(n) for n in names]

    # 1. Coincidencia exacta o substring directo
    for idx, nn in enumerate(norm_names):
        if q == nn or (len(q) >= 3 and (q in nn or nn in q)):
            return idx

    # 2. Fuzzy contra frase completa y contra palabras individuales
    best_idx = None
    best_score = 0.0
    q_tokens = [t for t in q.split() if len(t) >= 3]
    for idx, nn in enumerate(norm_names):
        score = difflib.SequenceMatcher(None, q, nn).ratio()
        for qt in q_tokens:
            for nt in nn.split():
                if len(nt) >= 3:
                    score = max(score, difflib.SequenceMatcher(None, qt, nt).ratio())
        if score >= cutoff and score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


def _parse_natural_exclusions(
    raw_text: str, removibles: List[IngredienteOpcionPlato]
) -> List[str]:
    """
    Interpreta frases como:
    - 'no quiero pepinillas ni sebolla'
    - 'quitale el tomate'
    - 'sin mayoneza'
    y las mapea a los nombres exactos de los ingredientes del producto (o texto limpio si no está en la lista).
    """
    norm = _normalize_text(raw_text)
    if not norm:
        return []

    exclusion_triggers = [
        r"\bsin\b\s+(.+)",
        r"\bno\s+quiero\b\s+(.+)",
        r"\bno\s+le\s+pongas?\b\s+(.+)",
        r"\bno\s+ponga[ns]?\b\s+(.+)",
        r"\bquita(?:le|r)?\b\s+(.+)",
        r"\bsaca(?:le|r)?\b\s+(.+)",
        r"\bmenos\b\s+(.+)",
    ]
    fragment = None
    for pat in exclusion_triggers:
        m = re.search(pat, norm)
        if m:
            fragment = m.group(1).strip()
            break

    rem_names = [r.nombre for r in removibles]
    rem_norms = [_normalize_text(r.nombre) for r in removibles]

    # Si no usó palabra disparadora explícita, intentar detectar si mencionó un ingrediente removible directamente
    target_text = fragment if fragment else norm
    parts = [p.strip() for p in re.split(r"\b(?:ni|y|con)\b|,|\s{2,}", target_text) if p.strip()]

    matched: List[str] = []
    stop_words = {"el", "la", "los", "las", "un", "una", "por", "favor", "porfa", "nada", "de"}

    for part in parts:
        cleaned_tokens = [t for t in part.split() if t not in stop_words]
        if not cleaned_tokens:
            continue
        candidate = " ".join(cleaned_tokens)
        best_i = _fuzzy_match_options(candidate, rem_names, cutoff=0.64)
        if best_i is not None:
            if rem_names[best_i] not in matched:
                matched.append(rem_names[best_i])
        else:
            # Probar palabra por palabra por si escribieron "pepinillas tomate" juntos sin coma
            found_any_token = False
            for tok in cleaned_tokens:
                if len(tok) < 3:
                    continue
                idx_tok = _fuzzy_match_options(tok, rem_norms, cutoff=0.66)
                if idx_tok is not None:
                    found_any_token = True
                    if rem_names[idx_tok] not in matched:
                        matched.append(rem_names[idx_tok])
            if not found_any_token and fragment:
                # Si dijo explícitamente "sin X" / "no quiero X", guardar su preferencia limpia
                pretty = candidate.capitalize()
                if pretty not in matched:
                    matched.append(pretty)

    return matched


_WORD_NUMBERS = {
    "un": 1, "uno": 1, "una": 1,
    "dos": 2, "par": 2,
    "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}


def _extract_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    if m:
        try:
            return float(m.group(1))
        except Exception:
            pass
    norm = _normalize_text(text)
    for tok in norm.split():
        if tok in _WORD_NUMBERS:
            return float(_WORD_NUMBERS[tok])
    return None


def _extract_branch_code(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\[([^\]]{2,50})\]", text)
    return m.group(1).strip() if m else None


def _empty_state() -> Dict[str, Any]:
    return {
        "items": [],
        "categoria_id": None,
        "pending_item": None,
        "tipo_entrega": None,
        "direccion_entrega": None,
        "zona_envio_id": None,
        "zona_nombre": None,
        "costo_envio": 0.0,
        "metodo_pago": None,
        "paga_con": None,
        "pedido_activo_id": None,
        "reserva_draft": None,  # {cantidad_personas, mesa_id, mesa_nombre, anticipo, fecha_hora, notas}
    }


def _load_session_state(sesion: SesionChatWhatsApp) -> Dict[str, Any]:
    base = _empty_state()
    try:
        raw = json.loads(sesion.carrito_json or "{}")
        if isinstance(raw, list):
            base["items"] = raw
            return base
        if isinstance(raw, dict):
            base.update(raw)
            if not isinstance(base.get("items"), list):
                base["items"] = []
            return base
    except Exception:
        pass
    return base


def _save_session_state(sesion: SesionChatWhatsApp, state: Dict[str, Any]) -> None:
    sesion.carrito_json = json.dumps(state, ensure_ascii=False)


def _get_available_catalog(
    db: Session, tenant_id: int, sucursal_id: int
) -> Tuple[List[CategoriaMenu], List[ProductoPlato]]:
    agotados_ids = {
        row.plato_id
        for row in db.query(StockSucursal)
        .filter(
            StockSucursal.tenant_id == tenant_id,
            StockSucursal.sucursal_id == sucursal_id,
            StockSucursal.agotado_hoy.is_(True),
        )
        .all()
    }
    categorias = (
        db.query(CategoriaMenu)
        .filter(CategoriaMenu.tenant_id == tenant_id, CategoriaMenu.activa.is_(True))
        .order_by(CategoriaMenu.orden.asc(), CategoriaMenu.id.asc())
        .all()
    )
    platos = (
        db.query(ProductoPlato)
        .filter(
            ProductoPlato.tenant_id == tenant_id,
            ProductoPlato.disponible_global.is_(True),
        )
        .order_by(ProductoPlato.categoria_id.asc(), ProductoPlato.id.asc())
        .all()
    )
    return categorias, [p for p in platos if p.id not in agotados_ids]


def _giro_copy(giro_raw: Optional[str]) -> Dict[str, Any]:
    g = normalize_giro_code(giro_raw)
    if g == "SERVICIOS_CITAS":
        return {"catalog": "nuestro catálogo de servicios", "uses_tables": True, "prep_word": "atención en consultorio"}
    if g == "FARMACIA":
        return {"catalog": "nuestras secciones", "uses_tables": False, "prep_word": "despacho"}
    if g == "TIENDA":
        return {"catalog": "nuestras categorías", "uses_tables": False, "prep_word": "empaque"}
    if g == "SUPERMERCADO":
        return {"catalog": "nuestros pasillos", "uses_tables": False, "prep_word": "alistamiento"}
    if g == "CAFETERIA":
        return {"catalog": "nuestro menú", "uses_tables": True, "prep_word": "preparación"}
    return {"catalog": "nuestro menú", "uses_tables": True, "prep_word": "cocina"}


def _saludo_nombre(nombre: Optional[str]) -> str:
    if not nombre or nombre.lower().startswith("cliente"):
        return "¡Hola!"
    return f"¡Hola {nombre.strip().split()[0]}!"


def _calc_subtotal(items: List[Dict[str, Any]]) -> float:
    total = 0.0
    for it in items:
        unit = float(it.get("precio_base", 0.0)) + float(it.get("precio_extras", 0.0))
        total += unit * int(it.get("cantidad", 1))
    return round(total, 2)


def _format_welcome_categories(
    comercio: TenantComercio,
    sucursal: SucursalComercio,
    categorias: List[CategoriaMenu],
    platos: List[ProductoPlato],
    state: Dict[str, Any],
    nombre_cliente: Optional[str],
    is_returning: bool = False,
    telefono_cliente: Optional[str] = None,
) -> str:
    copy = _giro_copy(comercio.giro)
    cats = [c for c in categorias if any(p.categoria_id == c.id for p in platos)]

    lines = []
    if is_returning:
        lines.append(f"¿Qué otra sección de {copy['catalog']} deseas ver?")
    else:
        lines.append(f"{_saludo_nombre(nombre_cliente)} Qué gusto saludarte desde *{comercio.nombre_comercial}* ({sucursal.nombre_sucursal}).")
        if not sucursal.abierto_ahora:
            lines.append(f"Por ahora estamos cerrados (Horario: {sucursal.horario_apertura} a {sucursal.horario_cierre}), pero puedes revisar {copy['catalog']}.")
        base_url = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
        qs_parts = []
        if telefono_cliente:
            qs_parts.append(f"tel={quote(clean_whatsapp_phone(telefono_cliente))}")
        if nombre_cliente and not nombre_cliente.lower().startswith("cliente"):
            qs_parts.append(f"nombre={quote(nombre_cliente.strip())}")
        qs = ("?" + "&".join(qs_parts)) if qs_parts else ""
        menu_link = f"{base_url}/m/{quote(sucursal.codigo_whatsapp or '')}{qs}"
        g_code = normalize_giro_code(comercio.giro)
        if g_code == "SERVICIOS_CITAS":
            lines.append(f"\n*Opción 1 — Catálogo de Servicios Web* (ver tratamientos y precios):\n{menu_link}")
            lines.append("\n*Opción 2 — Consultar servicios aquí por chat:* responde con el número de la especialidad:")
        elif g_code in ("TIENDA", "FARMACIA", "SUPERMERCADO"):
            lines.append(f"\n*Opción 1 — Catálogo Web Interactivo* (ver productos con fotos y pedir directo):\n{menu_link}")
            lines.append("\n*Opción 2 — Comprar aquí por chat:* responde con el número de la categoría:")
        else:
            lines.append(f"\n*Opción 1 — Menú Web Interactivo* (ver menú, seleccionar y enviar tu pedido directo):\n{menu_link}")
            lines.append("\n*Opción 2 — Pedir aquí por chat:* responde con el número de la categoría:")

    for idx, cat in enumerate(cats, start=1):
        lines.append(f"*{idx}.* {cat.nombre}")

    if state["items"]:
        sym = comercio.simbolo_moneda or "C$"
        qty = sum(int(i["cantidad"]) for i in state["items"])
        lines.append(f"\nLlevas *{qty} art.* ({sym}{_calc_subtotal(state['items']):.2f}). Escribe *Listo* cuando quieras finalizar.")
    elif not is_returning:
        if copy["uses_tables"] and comercio.reservaciones_habilitadas and any(m.activa for m in sucursal.mesas):
            g_code = normalize_giro_code(comercio.giro)
            if g_code == "SERVICIOS_CITAS":
                lines.append("\n*Opción 3 — Agendar una cita:* escribe *cita* o *reservar*")
            else:
                lines.append("\n*Opción 3 — Reservar una mesa:* escribe *reservar*")
        lines.append("\n_Si prefieres hablar con una persona, escribe *asesor*._")

    return "\n".join(lines)


def _format_products_list(
    comercio: TenantComercio,
    categoria: CategoriaMenu,
    platos_cat: List[ProductoPlato],
    page: int = 0,
    page_size: int = 6,
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    total = len(platos_cat)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = platos_cat[start : start + page_size]

    header_pag = f" (Pág. {page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [f"*{categoria.nombre}*{header_pag}", "¿Cuál te gustaría? Responde con el número o el nombre:\n"]
    for idx, pl in enumerate(chunk, start=start + 1):
        lines.append(f"*{idx}.* {pl.nombre} — *{sym}{pl.precio_base:.2f}*")

    nav = []
    if page + 1 < total_pages:
        nav.append("*+* Ver más productos")
    if page > 0:
        nav.append("*-* Página anterior")
    nav.append("*0.* Volver a categorías")
    lines.append("\n" + "  |  ".join(nav))
    return "\n".join(lines)


def _get_product_public_image_url(db: Session, plato_id: Optional[int]) -> Optional[str]:
    """Devuelve la URL pública WebP del producto si tiene imagen guardada en producto_imagenes."""
    if not plato_id:
        return None
    row = (
        db.query(ProductoImagen.actualizado_en)
        .filter(ProductoImagen.plato_id == int(plato_id))
        .first()
    )
    if not row:
        return None
    base_url = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
    ver = int(row[0].timestamp()) if row[0] else 1
    return f"{base_url}/api/v1/public/img/{int(plato_id)}?v={ver}"


def _format_product_selected_qty_prompt(
    db: Session, comercio: TenantComercio, pl: ProductoPlato
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    lines = [f"¡Buena elección! *{pl.nombre}* ({sym}{pl.precio_base:.2f})."]
    if normalize_giro_code(comercio.giro) in ("RESTAURANTE", "CAFETERIA"):
        removibles, _ = _get_item_customization_options(db, pl.id)
        if removibles:
            ing_txt = ", ".join(r.nombre for r in removibles)
            lines.append(f"Trae: _{ing_txt}_.")
    lines.append("\n¿Cuántas unidades te gustaría llevar?")
    lines.append("_Responde *0* si prefieres cancelar este producto y elegir otro._")
    return "\n".join(lines)


def _format_ask_more(comercio: TenantComercio, state: Dict[str, Any], just_added_text: str) -> str:
    sym = comercio.simbolo_moneda or "C$"
    sub = _calc_subtotal(state["items"])
    lines = [
        f"¡Perfecto! Anoté *{just_added_text}*.",
        f"Llevas un subtotal de *{sym}{sub:.2f}*.",
        "\n¿Te gustaría agregar algo más?",
        "*1.* Sí, ver más del menú",
        "*2.* No, continuar con mi pedido",
        "*3.* Ver o vaciar mi carrito",
    ]
    return "\n".join(lines)


def _format_cart_view(comercio: TenantComercio, state: Dict[str, Any]) -> str:
    sym = comercio.simbolo_moneda or "C$"
    items = state.get("items") or []
    if not items:
        return "Tu carrito está vacío por ahora. Escribe *menú* para ver las opciones."
    lines = ["*Así va tu pedido:*"]
    for idx, it in enumerate(items, start=1):
        unit = float(it["precio_base"]) + float(it.get("precio_extras", 0.0))
        sub = unit * int(it["cantidad"])
        det = []
        if it.get("sin_ingredientes"):
            det.append(f"sin {', '.join(it['sin_ingredientes'])}")
        if it.get("extras"):
            det.append("+" + ", ".join(e["nombre"] for e in it["extras"]))
        det_txt = f" ({'; '.join(det)})" if det else ""
        lines.append(f"{idx}. {it['cantidad']}x {it['nombre']}{det_txt} — {sym}{sub:.2f}")
    lines.append(f"\nSubtotal: *{sym}{_calc_subtotal(items):.2f}*")
    lines.append("\n*1.* Agregar algo más\n*2.* Continuar con mi pedido\n*3.* Vaciar carrito")
    return "\n".join(lines)


def _format_delivery_options(comercio: TenantComercio, sucursal: SucursalComercio) -> Tuple[str, List[str]]:
    copy = _giro_copy(comercio.giro)
    lines = ["¡Genial! ¿Cómo prefieres recibir tu pedido?\n"]
    qr: List[str] = []
    if sucursal.permite_domicilio:
        lines.append("*1.* Envío a domicilio")
        qr.append("1")
    if sucursal.permite_recoger_local:
        lines.append("*2.* Pasar a recoger al local")
        qr.append("2")
    if copy["uses_tables"]:
        lines.append("*3.* Consumir en mesa")
        qr.append("3")
    return "\n".join(lines), qr


def _format_zones_options(comercio: TenantComercio, sucursal: SucursalComercio, zonas: List[ZonaEnvioSucursal]) -> Tuple[str, List[str]]:
    sym = comercio.simbolo_moneda or "C$"
    lines = ["¿En qué zona te encuentras? Responde con el número o el nombre:\n"]
    qr: List[str] = []
    for idx, z in enumerate(zonas, start=1):
        lines.append(f"*{idx}.* {z.nombre_zona} (+{sym}{z.costo_envio:.2f})")
        if idx <= 4:
            qr.append(str(idx))
    otra_idx = len(zonas) + 1
    lines.append(f"*{otra_idx}.* Otra zona (+{sym}{float(sucursal.costo_envio_base or 0.0):.2f})")
    return "\n".join(lines), qr


def _format_payment_options(comercio: TenantComercio) -> Tuple[str, List[str]]:
    lines = ["¿Cómo prefieres realizar tu pago?\n"]
    qr: List[str] = []
    if comercio.acepta_efectivo:
        lines.append("*1.* Efectivo")
        qr.append("1")
    if comercio.acepta_transferencia:
        lines.append("*2.* Transferencia bancaria")
        qr.append("2")
    return "\n".join(lines), qr


def _format_final_confirmation(comercio: TenantComercio, state: Dict[str, Any]) -> str:
    sym = comercio.simbolo_moneda or "C$"
    items = state.get("items") or []
    subtotal = _calc_subtotal(items)
    envio = float(state.get("costo_envio") or 0.0) if state.get("tipo_entrega") == "DOMICILIO" else 0.0
    total = round(subtotal + envio, 2)

    entrega_lbl = {
        "DOMICILIO": "Envío a domicilio",
        "RECOGER_LOCAL": "Recoger en local",
        "MESA": "Consumo en mesa",
    }.get(state.get("tipo_entrega") or "", "Recoger en local")

    lines = ["*Revisa tu pedido antes de confirmarlo:*\n"]
    for it in items:
        unit = float(it["precio_base"]) + float(it.get("precio_extras", 0.0))
        sub = unit * int(it["cantidad"])
        det = []
        if it.get("sin_ingredientes"):
            det.append(f"sin {', '.join(it['sin_ingredientes'])}")
        if it.get("extras"):
            det.append("+" + ", ".join(e["nombre"] for e in it["extras"]))
        det_txt = f" ({'; '.join(det)})" if det else ""
        lines.append(f"- {it['cantidad']}x {it['nombre']}{det_txt}: {sym}{sub:.2f}")

    lines.append(f"\nEntrega: *{entrega_lbl}*")
    if state.get("tipo_entrega") == "DOMICILIO" and state.get("direccion_entrega"):
        zona_txt = f" ({state['zona_nombre']})" if state.get("zona_nombre") else ""
        lines.append(f"Dirección: {state['direccion_entrega']}{zona_txt}")

    pago_lbl = "Efectivo" if state.get("metodo_pago") == "EFECTIVO" else "Transferencia"
    lines.append(f"Pago: *{pago_lbl}*")
    if state.get("metodo_pago") == "EFECTIVO" and state.get("paga_con"):
        paga = float(state["paga_con"])
        vuelto = round(max(0.0, paga - total), 2)
        lines.append(f"Pagas con {sym}{paga:.2f} (vuelto: {sym}{vuelto:.2f})")
    elif state.get("metodo_pago") == "TRANSFERENCIA" and comercio.datos_bancarios_texto:
        lines.append(f"Cuenta: {comercio.datos_bancarios_texto}")

    if envio > 0:
        lines.append(f"\nSubtotal: {sym}{subtotal:.2f} | Envío: {sym}{envio:.2f}")
    lines.append(f"*Total a pagar: {sym}{total:.2f}*")
    lines.append("\n¿Todo correcto?\n*1.* Sí, confirmar pedido\n*2.* Agregar o cambiar algo\n*3.* Cancelar")
    return "\n".join(lines)


def _format_active_order_status(
    comercio: TenantComercio, sucursal: SucursalComercio, pedido: Pedido
) -> str:
    estado_map = {
        "RECIBIDO": "Recibido (en cola)",
        "EN_PROCESO": "En preparación",
        "LISTO": "Listo para entregar / retirar",
    }
    estado_txt = estado_map.get(pedido.estado_pedido, pedido.estado_pedido)
    sym = comercio.simbolo_moneda or "C$"
    return (
        f"Tu pedido *#{pedido.folio}* sigue en curso.\n"
        f"Estado actual: *{estado_txt}* (Total: {sym}{pedido.total:.2f}).\n"
        f"Tiempo estimado: *{sucursal.tiempo_estimado_entrega_min} min*. Te avisaremos por aquí en cuanto avance.\n\n"
        "*1.* Ver detalle de mi pedido\n"
        "*2.* Hacer otro pedido adicional\n"
        "*3.* Hablar con un asesor"
    )


def _format_active_order_detail(comercio: TenantComercio, pedido: Pedido) -> str:
    sym = comercio.simbolo_moneda or "C$"
    lines = [f"*Detalle de tu pedido #{pedido.folio}:*"]
    for d in pedido.detalles:
        lines.append(f"- {d.cantidad}x {d.nombre_plato} ({sym}{d.subtotal_linea:.2f})")
    lines.append(f"\nTotal: *{sym}{pedido.total:.2f}* · Estado: *{pedido.estado_pedido}*")
    lines.append("\n*2.* Hacer otro pedido adicional\n*3.* Hablar con un asesor")
    return "\n".join(lines)


def _get_item_customization_options(
    db: Session, plato_id: int
) -> Tuple[List[IngredienteOpcionPlato], List[IngredienteOpcionPlato]]:
    opts = (
        db.query(IngredienteOpcionPlato)
        .filter(
            IngredienteOpcionPlato.plato_id == plato_id,
            IngredienteOpcionPlato.disponible_global.is_(True),
        )
        .order_by(IngredienteOpcionPlato.id.asc())
        .all()
    )
    removibles = [o for o in opts if o.tipo == "INGREDIENTE_BASE"]
    extras = [o for o in opts if o.tipo in ("EXTRA", "VARIANTE")]
    return removibles, extras


def _format_customization_prompt(
    comercio: TenantComercio,
    pending: Dict[str, Any],
    removibles: List[IngredienteOpcionPlato],
    extras: List[IngredienteOpcionPlato],
) -> Tuple[str, List[str]]:
    sym = comercio.simbolo_moneda or "C$"
    lines = [f"¿Cómo deseas tu *{pending['cantidad']}x {pending['nombre']}*?\n", "*1.* Así tal cual, está perfecto"]
    qr = ["1"]
    for idx, ex in enumerate(extras[:4], start=2):
        lines.append(f"*{idx}.* Agregar {ex.nombre} (+{sym}{ex.precio_adicional:.2f})")
        qr.append(str(idx))
    if removibles:
        nombres = ", ".join(r.nombre for r in removibles[:6])
        lines.append(f"\n_Si no quieres algún ingrediente ({nombres}), dímelo como quieras, ej: *no quiero {removibles[0].nombre.lower()}*._")
    return "\n".join(lines), qr


def _resolve_or_create_session(
    db: Session,
    telefono: str,
    nombre_cliente: str,
    texto: str,
    forced_sucursal_id: Optional[int] = None,
) -> Tuple[Optional[SesionChatWhatsApp], Optional[str]]:
    now = _utcnow()
    cleanup_expired_whatsapp_sessions(db)

    branch_code = _extract_branch_code(texto)
    target_suc: Optional[SucursalComercio] = None

    if forced_sucursal_id:
        target_suc = (
            db.query(SucursalComercio)
            .filter(SucursalComercio.id == forced_sucursal_id)
            .first()
        )
    elif branch_code:
        for s in db.query(SucursalComercio).all():
            if (s.codigo_whatsapp or "").strip().lower() == branch_code.lower():
                target_suc = s
                break

    if target_suc:
        sesion = (
            db.query(SesionChatWhatsApp)
            .filter(
                SesionChatWhatsApp.telefono_cliente == telefono,
                SesionChatWhatsApp.sucursal_id == target_suc.id,
                SesionChatWhatsApp.activa.is_(True),
            )
            .order_by(SesionChatWhatsApp.id.desc())
            .first()
        )
        if sesion and _expire_session_if_idle(db, sesion, now):
            sesion = None
        if not sesion:
            sesion = SesionChatWhatsApp(
                tenant_id=target_suc.tenant_id,
                sucursal_id=target_suc.id,
                telefono_cliente=telefono,
                nombre_cliente=nombre_cliente or "Cliente WhatsApp",
                modo_atencion="BOT_ACTIVO",
                estado_conversacion="EXPLORANDO_MENU",
                carrito_json=json.dumps(_empty_state(), ensure_ascii=False),
                como_se_realizo_resumen=f"Entrada por WhatsApp Opción B [{target_suc.codigo_whatsapp}]",
                activa=True,
            )
            db.add(sesion)
            db.flush()
        elif nombre_cliente and (not sesion.nombre_cliente or sesion.nombre_cliente == "Cliente WhatsApp"):
            sesion.nombre_cliente = nombre_cliente
        return sesion, None

    sesion = (
        db.query(SesionChatWhatsApp)
        .filter(
            SesionChatWhatsApp.telefono_cliente == telefono,
            SesionChatWhatsApp.activa.is_(True),
        )
        .order_by(SesionChatWhatsApp.ultimo_mensaje_en.desc())
        .first()
    )
    if sesion and _expire_session_if_idle(db, sesion, now):
        sesion = None
    if sesion:
        return sesion, None

    sucs = (
        db.query(SucursalComercio)
        .join(TenantComercio, TenantComercio.id == SucursalComercio.tenant_id)
        .filter(TenantComercio.activo.is_(True))
        .order_by(SucursalComercio.id.asc())
        .limit(10)
        .all()
    )
    if not sucs:
        return None, "Por el momento no hay comercios disponibles."

    chosen = None
    if texto.strip().isdigit():
        idx = int(texto.strip()) - 1
        if 0 <= idx < len(sucs):
            chosen = sucs[idx]
    else:
        idx_f = _fuzzy_match_options(texto, [f"{s.comercio.nombre_comercial} {s.nombre_sucursal}" for s in sucs], cutoff=0.65)
        if idx_f is not None:
            chosen = sucs[idx_f]

    if chosen:
        sesion = SesionChatWhatsApp(
            tenant_id=chosen.tenant_id,
            sucursal_id=chosen.id,
            telefono_cliente=telefono,
            nombre_cliente=nombre_cliente or "Cliente WhatsApp",
            modo_atencion="BOT_ACTIVO",
            estado_conversacion="EXPLORANDO_MENU",
            carrito_json=json.dumps(_empty_state(), ensure_ascii=False),
            como_se_realizo_resumen="Selección directa de comercio en WhatsApp",
            activa=True,
        )
        db.add(sesion)
        db.flush()
        return sesion, None

    lines = [f"{_saludo_nombre(nombre_cliente)} ¿En cuál de nuestros negocios deseas pedir hoy?\n"]
    for i, s in enumerate(sucs, start=1):
        lines.append(f"*{i}.* {s.comercio.nombre_comercial} — {s.nombre_sucursal}")
    return None, "\n".join(lines)


def _create_order_from_session(
    db: Session,
    sesion: SesionChatWhatsApp,
    comercio: TenantComercio,
    sucursal: SucursalComercio,
    state: Dict[str, Any],
    modo_bot: str,
) -> Pedido:
    items = state.get("items") or []
    subtotal = 0.0
    detalles: List[DetallePedido] = []
    resumen_items: List[str] = []

    for it in items:
        plato = (
            db.query(ProductoPlato)
            .filter(
                ProductoPlato.id == int(it["plato_id"]),
                ProductoPlato.tenant_id == comercio.id,
            )
            .first()
        )
        if not plato:
            continue
        qty = max(1, int(it.get("cantidad", 1)))
        sin_list = it.get("sin_ingredientes") or []
        extras_list = it.get("extras") or []
        extras_unit = round(sum(float(e.get("precio", 0.0)) for e in extras_list), 2)
        line_sub = round((float(plato.precio_base) + extras_unit) * qty, 2)
        subtotal += line_sub

        det_parts = []
        if sin_list:
            det_parts.append(f"sin {', '.join(sin_list)}")
        if extras_list:
            det_parts.append("+" + ", ".join(e["nombre"] for e in extras_list))
        det_txt = f" ({'; '.join(det_parts)})" if det_parts else ""
        resumen_items.append(f"{qty}x {plato.nombre}{det_txt}")

        detalles.append(
            DetallePedido(
                plato_id=plato.id,
                nombre_plato=plato.nombre,
                cantidad=qty,
                precio_unitario_base=plato.precio_base,
                sin_ingredientes_json=json.dumps(sin_list, ensure_ascii=False),
                extras_json=json.dumps(extras_list, ensure_ascii=False),
                precio_extras_unitario=extras_unit,
                subtotal_linea=line_sub,
                notas_plato=it.get("notas") or None,
            )
        )

    tipo_entrega = state.get("tipo_entrega") or "RECOGER_LOCAL"
    tel_clean = clean_whatsapp_phone(sesion.telefono_cliente) or "00000000"
    from app.services.fidelidad_service import (
        calcular_descuento_cliente,
        consumir_premio_sellos,
        get_cliente_beneficios,
    )

    beneficios_cli = get_cliente_beneficios(db, comercio.id, tel_clean)
    es_vip_envio_gratis = bool(beneficios_cli.get("envio_gratis"))
    desc_monto, desc_label, usa_premio_sello = calcular_descuento_cliente(subtotal, beneficios_cli)
    costo_envio = 0.0
    if tipo_entrega == "DOMICILIO":
        if es_vip_envio_gratis:
            costo_envio = 0.0
        elif state.get("zona_envio_id"):
            z = (
                db.query(ZonaEnvioSucursal)
                .filter(
                    ZonaEnvioSucursal.id == int(state["zona_envio_id"]),
                    ZonaEnvioSucursal.sucursal_id == sucursal.id,
                )
                .first()
            )
            costo_envio = float(z.costo_envio) if z else float(sucursal.costo_envio_base or 0.0)
        else:
            costo_envio = float(sucursal.costo_envio_base or 0.0)

    total = round(max(0.0, subtotal - desc_monto) + costo_envio, 2)
    if usa_premio_sello and desc_monto > 0:
        consumir_premio_sellos(db, comercio.id, tel_clean)
    nom_cli = sesion.nombre_cliente or "Cliente WhatsApp"

    cli = (
        db.query(ClienteFrecuente)
        .filter(
            ClienteFrecuente.tenant_id == comercio.id,
            ClienteFrecuente.telefono_whatsapp == tel_clean,
        )
        .first()
    )
    if not cli:
        cli = ClienteFrecuente(
            tenant_id=comercio.id,
            telefono_whatsapp=tel_clean,
            nombre_cliente=nom_cli,
            ultima_direccion=state.get("direccion_entrega"),
            ultima_zona_envio=state.get("zona_nombre"),
            total_pedidos_realizados=1,
        )
        db.add(cli)
        db.flush()
    else:
        cli.nombre_cliente = nom_cli
        cli.total_pedidos_realizados = (cli.total_pedidos_realizados or 0) + 1
        if state.get("direccion_entrega"):
            cli.ultima_direccion = state["direccion_entrega"]
        if state.get("zona_nombre"):
            cli.ultima_zona_envio = state["zona_nombre"]
        cli.actualizado_en = _utcnow()

    folio = f"WA-{_utcnow().strftime('%H%M')}-{random.randint(100, 999)}"
    metodo_pago = state.get("metodo_pago") or "EFECTIVO"
    paga_con = state.get("paga_con")
    cambio = None
    if metodo_pago == "EFECTIVO" and paga_con is not None and float(paga_con) >= total:
        cambio = round(float(paga_con) - total, 2)

    resumen_texto = (
        f"Pedido confirmado en WhatsApp: {', '.join(resumen_items)}. "
        f"Entrega: {tipo_entrega}. Pago: {metodo_pago}."
    )
    sesion.resumen_ia_chat = resumen_texto

    pedido = Pedido(
        folio=folio,
        tenant_id=comercio.id,
        sucursal_id=sucursal.id,
        sesion_chat_id=sesion.id,
        cliente_frecuente_id=cli.id,
        telefono_cliente=tel_clean,
        nombre_cliente=nom_cli,
        tipo_entrega=tipo_entrega,
        direccion_entrega=state.get("direccion_entrega")
        or ("Consumo en mesa / local" if tipo_entrega == "MESA" else "Retiro en mostrador"),
        estado_pedido="ESPERANDO_PAGO",
        subtotal=round(subtotal, 2),
        costo_envio=round(costo_envio, 2),
        total=total,
        resumen_chat_ia=resumen_texto,
        como_se_realizo_pedido=f"Bot WhatsApp ({sucursal.nombre_sucursal}) • {modo_bot}",
        metodo_pago=metodo_pago,
        estado_pago="PENDIENTE_PAGO_API",
        paga_con_monto=paga_con,
        cambio_a_entregar=cambio,
    )
    db.add(pedido)
    db.flush()

    for det in detalles:
        det.pedido_id = pedido.id
        db.add(det)

    comercio.pedidos_mes_actual = (comercio.pedidos_mes_actual or 0) + 1
    return pedido


def _has_reservation_conflict(
    db: Session, sucursal_id: int, mesa_id: Optional[int], fecha_hora: str
) -> bool:
    """Detecta si ya existe una cita/reserva activa en el mismo consultorio/mesa y horario."""
    fh = (fecha_hora or "").strip().lower()
    if not mesa_id or not fh or fh == "por confirmar":
        return False
    activas = (
        db.query(ReservacionMesa.fecha_hora_reserva)
        .filter(
            ReservacionMesa.sucursal_id == sucursal_id,
            ReservacionMesa.mesa_id == mesa_id,
            ReservacionMesa.estado_reserva.notin_(("CANCELADA", "COMPLETADA")),
        )
        .all()
    )
    return any((row[0] or "").strip().lower() == fh for row in activas)


def _create_reservation_from_session(
    db: Session,
    sesion: SesionChatWhatsApp,
    comercio: TenantComercio,
    sucursal: SucursalComercio,
    res_data: Dict[str, Any],
) -> ReservacionMesa:
    mesas_act = [m for m in sucursal.mesas if m.activa]
    personas = max(1, min(30, int(res_data.get("cantidad_personas") or 1)))
    fecha_hora = str(res_data.get("fecha_hora") or "Por confirmar").strip()[:48]
    mesa_obj = None
    if res_data.get("mesa_id"):
        mesa_obj = next((m for m in mesas_act if m.id == int(res_data["mesa_id"])), None)
    if not mesa_obj and mesas_act:
        aptas = sorted([m for m in mesas_act if m.capacidad_personas >= personas], key=lambda x: x.capacidad_personas)
        candidatas = aptas if aptas else sorted(mesas_act, key=lambda x: x.capacidad_personas, reverse=True)
        # Preferir un consultorio/mesa libre en ese horario
        libre = next((m for m in candidatas if not _has_reservation_conflict(db, sucursal.id, m.id, fecha_hora)), None)
        mesa_obj = libre or candidatas[0]

    if mesa_obj and _has_reservation_conflict(db, sucursal.id, mesa_obj.id, fecha_hora):
        raise ValueError(f"HORARIO_OCUPADO:{mesa_obj.nombre_mesa}:{fecha_hora}")

    anticipo = float(
        mesa_obj.monto_anticipo_reserva
        if (mesa_obj is not None and float(mesa_obj.monto_anticipo_reserva or 0.0) > 0)
        else (comercio.monto_anticipo_reserva_default or 0.0)
    )
    is_cita = normalize_giro_code(comercio.giro) == "SERVICIOS_CITAS"
    folio = f"{'CIT' if is_cita else 'RES'}-{_utcnow().strftime('%H%M')}-{random.randint(100, 999)}"
    tel_clean = clean_whatsapp_phone(sesion.telefono_cliente) or "00000000"
    nom_cli = str(res_data.get("nombre_cliente") or "").strip()[:80] or sesion.nombre_cliente or "Cliente WhatsApp"

    reserva = ReservacionMesa(
        folio_reserva=folio,
        tenant_id=comercio.id,
        sucursal_id=sucursal.id,
        mesa_id=mesa_obj.id if mesa_obj else None,
        sesion_chat_id=sesion.id,
        nombre_cliente=nom_cli,
        telefono_cliente=tel_clean,
        fecha_hora_reserva=fecha_hora,
        cantidad_personas=personas,
        monto_pago_requerido=round(anticipo, 2),
        estado_pago="PAGADO" if anticipo <= 0 else "PENDIENTE_PAGO_API",
        estado_reserva="CONFIRMADA" if anticipo <= 0 else "ESPERANDO_PAGO",
        notas=str(res_data.get("notas") or "").strip() or None,
    )
    db.add(reserva)
    db.flush()
    return reserva


def _validate_reserva_datetime(
    raw_text: str, sucursal: SucursalComercio
) -> Tuple[bool, str]:
    """Valida que la fecha/hora de reserva en Menú Guiado sea clara (incluye hora y AM/PM si aplica)."""
    txt = (raw_text or "").strip()
    horario = f"{sucursal.horario_apertura} a {sucursal.horario_cierre}"
    if len(txt) < 4:
        return (
            False,
            f"Indícame la fecha y hora por favor (ej: *Hoy 7:30 PM* o *30 de noviembre 7:00 PM*).\n_Horario de atención: {horario}._",
        )

    norm = _normalize_text(txt)
    has_time_words = any(
        w in norm
        for w in ("am", "pm", "a.m", "p.m", "manana", "tarde", "noche", "mediodia", "madrugada", "hrs", "horas")
    )
    # Buscar hora explícita tipo "19:00", "7:30", "a las 7", "a 7", "7 pm"
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|hrs|horas)?\b", norm)
    # Distinguir si el único número es el día del mes (ej: "30 de noviembre") sin hora
    all_nums = [int(m.group(1)) for m in re.finditer(r"\b(\d{1,2})(?::\d{2})?\b", norm)]
    has_colon_time = bool(re.search(r"\b\d{1,2}:\d{2}\b", norm))
    has_a_las = bool(re.search(r"\b(?:a\s+las?|a)\s+\d{1,2}\b", norm))

    if not has_colon_time and not has_a_las and not any(w in norm for w in ("am", "pm", "mediodia")):
        if len(all_nums) == 0 or (len(all_nums) == 1 and all_nums[0] > 12 and not has_colon_time):
            return (
                False,
                f"Por favor inclúyeme también la *hora* con *AM o PM* (ej: *30 de noviembre 7:00 PM*).\n_Nuestro horario es de {horario}._",
            )

    # Si mencionó una hora entre 1 y 12 sin aclarar AM/PM ni tarde/noche/mediodia
    ambiguous_hour = None
    m_a = re.search(r"\b(?:a\s+las?|a)\s+(\d{1,2})(?::\d{2})?\b", norm)
    if m_a:
        ambiguous_hour = int(m_a.group(1))
    elif has_colon_time:
        m_c = re.search(r"\b(\d{1,2}):\d{2}\b", norm)
        if m_c:
            ambiguous_hour = int(m_c.group(1))
    elif time_match and len(all_nums) >= 2:
        ambiguous_hour = all_nums[-1]

    if ambiguous_hour is not None and 1 <= ambiguous_hour <= 12:
        has_period = bool(
            re.search(r"\b(?:am|pm|a\.m\.|p\.m\.|tarde|noche|mediodia|madrugada)\b", norm)
        )
        # Nota: "mañana" puede referirse al día siguiente ("mañana a las 7"), así que NO desambigua AM/PM
        if not has_period:
            return (
                False,
                f"Para evitar confusiones, ¿sería a las *{ambiguous_hour}:00 AM* o *{ambiguous_hour}:00 PM*?\n"
                f"_Nuestro horario de atención es de {horario}. Escribe la fecha y hora completa con AM o PM._",
            )

    return True, txt[:48]


def _format_reserva_confirmation_summary(
    comercio: TenantComercio, draft: Dict[str, Any]
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    anticipo = float(draft.get("anticipo") or 0.0)
    lines = [
        "*Revisa los datos de tu reservación:*\n",
        f"- A nombre de: *{draft.get('nombre_cliente') or 'Cliente'}*",
        f"- Mesa: *{draft.get('mesa_nombre') or 'Asignada por sucursal'}*",
        f"- Personas: *{draft.get('cantidad_personas')}*",
        f"- Fecha y hora: *{draft.get('fecha_hora')}*",
    ]
    if anticipo > 0:
        lines.append(f"- Anticipo requerido: *{sym}{anticipo:.2f}*")
        if comercio.datos_bancarios_texto:
            lines.append(f"- Datos bancarios: {comercio.datos_bancarios_texto}")
        lines.append(
            "\n*Importante:* Tu reserva quedará *PENDIENTE DE PAGO* y *no estará confirmada* hasta realizar el pago del anticipo y enviar tu comprobante."
        )
        lines.append("\n¿Registramos tu solicitud de reservación?\n*1.* Sí, registrar solicitud\n*2.* Cancelar")
    else:
        lines.append("\n¿Confirmamos tu reservación?\n*1.* Sí, confirmar\n*2.* Cancelar")
    return "\n".join(lines)


def _format_reserva_created_reply(
    comercio: TenantComercio, reserva: ReservacionMesa, mesa_nombre: str
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    es_cita = normalize_giro_code(comercio.giro) == "SERVICIOS_CITAS"
    espacio_lbl = f"- Consultorio / Especialista: *{mesa_nombre}*" if es_cita else f"- Mesa: *{mesa_nombre}* · *{reserva.cantidad_personas} personas*"
    if float(reserva.monto_pago_requerido or 0.0) > 0:
        bank_line = (
            f"\n- Datos bancarios: {comercio.datos_bancarios_texto}"
            if comercio.datos_bancarios_texto
            else ""
        )
        titulo = "Solicitud de cita registrada" if es_cita else "Solicitud de reservación registrada"
        confirm_obj = "confirmar tu cita" if es_cita else "confirmar tu mesa"
        sust = "cita" if es_cita else "reservación"
        return (
            f"*{titulo}* — Folio: *#{reserva.folio_reserva}*\n"
            f"Estado: *PENDIENTE DE PAGO* _(aún no confirmada)_\n\n"
            f"- A nombre de: *{reserva.nombre_cliente}*\n"
            f"{espacio_lbl}\n"
            f"- Fecha y hora: *{reserva.fecha_hora_reserva}*\n"
            f"- Anticipo requerido: *{sym}{reserva.monto_pago_requerido:.2f}*{bank_line}\n\n"
            f"Para *{confirm_obj}*, realiza el pago del anticipo y envíanos el comprobante por este chat. "
            f"Tu {sust} quedará confirmada en cuanto validemos tu pago."
        )
    ok_title = "¡Cita confirmada!" if es_cita else "¡Reservación confirmada!"
    return (
        f"{ok_title} Folio: *#{reserva.folio_reserva}*\n"
        f"- A nombre de: *{reserva.nombre_cliente}*\n"
        f"{espacio_lbl}\n"
        f"- Fecha y hora: *{reserva.fecha_hora_reserva}*\n\n"
        "¡Te esperamos pronto!"
    )


def _handle_solo_ia_turn(
    db: Session,
    sesion: SesionChatWhatsApp,
    comercio: TenantComercio,
    sucursal: SucursalComercio,
    categorias: List[CategoriaMenu],
    platos: List[ProductoPlato],
    state: Dict[str, Any],
    telefono: str,
    texto: str,
) -> Optional[Dict[str, Any]]:
    """
    Ejecuta un turno completo 100% manejado por IA (SOLO_IA) respetando
    estrictamente el catálogo real, precios desde BD y confirmación obligatoria.
    Retorna None si la IA no está disponible para hacer fallback seguro.
    """
    copy = _giro_copy(comercio.giro)
    sym = comercio.simbolo_moneda or "C$"
    zonas_act = [z for z in sucursal.zonas_envio if z.activa]

    # Link personalizado al Mini-Menú Web
    base_url = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
    qs_parts = [f"tel={quote(clean_whatsapp_phone(telefono))}"]
    if sesion.nombre_cliente and not sesion.nombre_cliente.lower().startswith("cliente"):
        qs_parts.append(f"nombre={quote(sesion.nombre_cliente.strip())}")
    menu_link = f"{base_url}/m/{quote(sucursal.codigo_whatsapp or '')}?{'&'.join(qs_parts)}"

    # Revisar pedido activo si la sesión está en SIGUIENDO_PEDIDO
    pedido_activo_ctx = None
    if sesion.estado_conversacion == "SIGUIENDO_PEDIDO" and state.get("pedido_activo_id"):
        ped_act = db.query(Pedido).filter(Pedido.id == int(state["pedido_activo_id"])).first()
        if ped_act and ped_act.estado_pedido not in ("ENTREGADO", "CANCELADO"):
            pedido_activo_ctx = {
                "folio": ped_act.folio,
                "estado": ped_act.estado_pedido,
                "total": ped_act.total,
                "tiempo_estimado_min": sucursal.tiempo_estimado_entrega_min,
            }
        else:
            state.clear()
            state.update(_empty_state())
            sesion.estado_conversacion = "EXPLORANDO_MENU"

    cli_prev = (
        db.query(ClienteFrecuente)
        .filter(
            ClienteFrecuente.tenant_id == comercio.id,
            ClienteFrecuente.telefono_whatsapp == telefono,
        )
        .first()
    )

    catalogo_ctx = []
    for p in platos[:45]:
        rem, exs = _get_item_customization_options(db, p.id)
        item_ctx: Dict[str, Any] = {
            "plato_id": p.id,
            "categoria": p.categoria.nombre if p.categoria else "General",
            "nombre": p.nombre,
            "precio": float(p.precio_base),
        }
        if rem and copy["uses_tables"]:
            item_ctx["ingredientes"] = [r.nombre for r in rem]
        if exs:
            item_ctx["extras"] = [
                {"extra_id": e.id, "nombre": e.nombre, "precio": float(e.precio_adicional or 0.0)}
                for e in exs[:6]
            ]
        catalogo_ctx.append(item_ctx)

    context_payload = {
        "comercio": {
            "nombre": comercio.nombre_comercial,
            "giro": comercio.giro,
            "simbolo_moneda": sym,
            "acepta_efectivo": bool(comercio.acepta_efectivo),
            "acepta_transferencia": bool(comercio.acepta_transferencia),
            "datos_bancarios": comercio.datos_bancarios_texto or "",
            "usa_mesas": copy["uses_tables"],
            "menu_web_url": menu_link,
        },
        "sucursal": {
            "nombre": sucursal.nombre_sucursal,
            "abierto_ahora": bool(sucursal.abierto_ahora),
            "horario": f"{sucursal.horario_apertura} a {sucursal.horario_cierre}",
            "tiempo_estimado_min": sucursal.tiempo_estimado_entrega_min,
            "permite_domicilio": bool(sucursal.permite_domicilio),
            "permite_recoger_local": bool(sucursal.permite_recoger_local),
            "costo_envio_base": float(sucursal.costo_envio_base or 0.0),
            "zonas_envio": [
                {"zona_id": z.id, "nombre": z.nombre_zona, "costo": float(z.costo_envio or 0.0)}
                for z in zonas_act
            ],
            "reservaciones_habilitadas": bool(comercio.reservaciones_habilitadas),
            "mesas_disponibles": [
                {
                    "mesa_id": m.id,
                    "nombre": m.nombre_mesa,
                    "capacidad": m.capacidad_personas,
                    "anticipo": float(m.monto_anticipo_reserva or 0.0),
                }
                for m in sucursal.mesas
                if m.activa
            ] if copy["uses_tables"] else [],
        },
        "reserva_en_progreso": state.get("reserva_draft"),
        "cliente": {
            "nombre": sesion.nombre_cliente or "Cliente",
            "ultima_direccion": cli_prev.ultima_direccion if cli_prev else None,
            "beneficios_vip": __import__("app.services.fidelidad_service", fromlist=["get_cliente_beneficios"]).get_cliente_beneficios(db, comercio.id, telefono),
        },
        "pedido_activo_en_curso": pedido_activo_ctx,
        "carrito_actual": {
            "items": state.get("items") or [],
            "subtotal": _calc_subtotal(state.get("items") or []),
            "tipo_entrega": state.get("tipo_entrega"),
            "zona_nombre": state.get("zona_nombre"),
            "costo_envio": float(state.get("costo_envio") or 0.0),
            "direccion_entrega": state.get("direccion_entrega"),
            "metodo_pago": state.get("metodo_pago"),
            "paga_con": state.get("paga_con"),
        },
        "catalogo_disponible": catalogo_ctx,
    }

    # Últimos 8 mensajes previos de esta sesión (excluyendo el actual que ya va aparte)
    prev_msgs = (
        db.query(MensajeChatWhatsApp)
        .filter(MensajeChatWhatsApp.sesion_id == sesion.id)
        .order_by(MensajeChatWhatsApp.id.desc())
        .limit(9)
        .all()
    )
    history = [
        {"de": m.remitente, "texto": (m.contenido or "")[:350]}
        for m in reversed(prev_msgs[1:])
    ]

    ai_res = run_full_ai_order_turn(context_payload, history, texto)
    if not ai_res:
        return None

    ai_used = ai_res.get("_provider") or "gemini"
    reply_text = str(ai_res.get("respuesta") or "").strip()
    acciones = ai_res.get("acciones") or {}
    if not isinstance(acciones, dict):
        acciones = {}

    if acciones.get("vaciar_carrito"):
        ped_keep = state.get("pedido_activo_id")
        state.clear()
        state.update(_empty_state())
        if ped_keep:
            state["pedido_activo_id"] = ped_keep

    # Validar y aplicar ítems agregados por la IA contra la BD real
    plato_map = {p.id: p for p in platos}
    for it_in in acciones.get("agregar_items") or []:
        if not isinstance(it_in, dict):
            continue
        pid = int(it_in.get("plato_id") or 0)
        pl = plato_map.get(pid)
        if not pl:
            continue
        qty = max(1, min(50, int(it_in.get("cantidad") or 1)))
        sin_list = [str(s).strip() for s in (it_in.get("sin_ingredientes") or []) if str(s).strip()]
        extras_sel: List[Dict[str, Any]] = []
        raw_ex_ids = [int(x) for x in (it_in.get("extras_ids") or []) if str(x).isdigit()]
        if raw_ex_ids:
            opts = (
                db.query(IngredienteOpcionPlato)
                .filter(
                    IngredienteOpcionPlato.id.in_(raw_ex_ids),
                    IngredienteOpcionPlato.plato_id == pl.id,
                    IngredienteOpcionPlato.disponible_global.is_(True),
                )
                .all()
            )
            for opt in opts:
                extras_sel.append({"nombre": opt.nombre, "precio": float(opt.precio_adicional or 0.0)})
        precio_ex = round(sum(e["precio"] for e in extras_sel), 2)
        state["items"].append(
            {
                "plato_id": pl.id,
                "nombre": pl.nombre,
                "precio_base": float(pl.precio_base),
                "cantidad": qty,
                "sin_ingredientes": sin_list,
                "extras": extras_sel,
                "precio_extras": precio_ex,
                "notas": str(it_in.get("notas") or "").strip(),
            }
        )

    # Modalidad de entrega validada
    t_ent = acciones.get("tipo_entrega")
    if isinstance(t_ent, str) and t_ent.strip():
        t_up = t_ent.upper().strip()
        if t_up == "DOMICILIO" and sucursal.permite_domicilio:
            state["tipo_entrega"] = "DOMICILIO"
            if not state.get("zona_envio_id"):
                state["costo_envio"] = float(sucursal.costo_envio_base or 0.0)
        elif t_up == "RECOGER_LOCAL" and sucursal.permite_recoger_local:
            state["tipo_entrega"] = "RECOGER_LOCAL"
            state["direccion_entrega"] = f"Recoger en {sucursal.nombre_sucursal}"
            state["costo_envio"] = 0.0
        elif t_up == "MESA" and copy["uses_tables"]:
            state["tipo_entrega"] = "MESA"
            state["direccion_entrega"] = f"Consumo en mesa ({sucursal.nombre_sucursal})"
            state["costo_envio"] = 0.0

    # Zona de envío validada
    z_id = acciones.get("zona_envio_id")
    if z_id is not None and str(z_id).isdigit():
        z_obj = next((z for z in zonas_act if z.id == int(z_id)), None)
        if z_obj:
            state["zona_envio_id"] = z_obj.id
            state["zona_nombre"] = z_obj.nombre_zona
            state["costo_envio"] = float(z_obj.costo_envio or 0.0)

    # Dirección
    dir_in = acciones.get("direccion_entrega")
    if isinstance(dir_in, str) and len(dir_in.strip()) >= 5:
        state["direccion_entrega"] = dir_in.strip()

    # Método de pago validado
    m_pago = acciones.get("metodo_pago")
    if isinstance(m_pago, str) and m_pago.strip():
        m_up = m_pago.upper().strip()
        if m_up == "EFECTIVO" and comercio.acepta_efectivo:
            state["metodo_pago"] = "EFECTIVO"
        elif m_up == "TRANSFERENCIA" and comercio.acepta_transferencia:
            state["metodo_pago"] = "TRANSFERENCIA"
            state["paga_con"] = None

    paga_in = acciones.get("paga_con")
    if paga_in is not None:
        try:
            p_val = float(paga_in)
            if p_val > 0:
                state["paga_con"] = round(p_val, 2)
        except Exception:
            pass

    pedido_creado = None
    reserva_creada = None
    res_in = acciones.get("reservacion")
    if isinstance(res_in, dict) and any(
        res_in.get(k) for k in ("mesa_id", "cantidad_personas", "fecha_hora", "nombre_cliente", "notas", "confirmar_reservacion")
    ):
        draft = dict(state.get("reserva_draft") or {})
        if res_in.get("cantidad_personas"):
            try:
                draft["cantidad_personas"] = max(1, min(30, int(res_in["cantidad_personas"])))
            except Exception:
                pass
        if res_in.get("mesa_id") and str(res_in["mesa_id"]).isdigit():
            m_obj = next((m for m in sucursal.mesas if m.activa and m.id == int(res_in["mesa_id"])), None)
            if m_obj:
                draft["mesa_id"] = m_obj.id
                draft["mesa_nombre"] = m_obj.nombre_mesa
                draft["anticipo"] = float(m_obj.monto_anticipo_reserva or 0.0)
        if res_in.get("fecha_hora"):
            draft["fecha_hora"] = str(res_in["fecha_hora"]).strip()
        if res_in.get("nombre_cliente"):
            draft["nombre_cliente"] = str(res_in["nombre_cliente"]).strip()[:80]
        if res_in.get("notas"):
            draft["notas"] = str(res_in["notas"]).strip()
        state["reserva_draft"] = draft

        if (
            res_in.get("confirmar_reservacion")
            and copy["uses_tables"]
            and comercio.reservaciones_habilitadas
            and draft.get("cantidad_personas")
            and draft.get("fecha_hora")
        ):
            reserva = _create_reservation_from_session(db, sesion, comercio, sucursal, draft)
            reserva_creada = {
                "id": reserva.id,
                "folio": reserva.folio_reserva,
                "estado_reserva": reserva.estado_reserva,
                "estado_pago": reserva.estado_pago,
                "monto_anticipo": reserva.monto_pago_requerido,
            }
            mesa_lbl = draft.get("mesa_nombre") or "Asignada"
            state["reserva_draft"] = None
            if float(reserva.monto_pago_requerido or 0.0) > 0 or reserva.folio_reserva not in reply_text:
                reply_text = _format_reserva_created_reply(comercio, reserva, mesa_lbl)

    if acciones.get("transferir_humano"):
        sesion.modo_atencion = "HUMANO_ACTIVO"
        sesion.resumen_ia_chat = f"Atención humana solicitada (IA): {texto[:160]}"
    elif acciones.get("confirmar_pedido"):
        # Validación dura en servidor: solo crear pedido si están TODOS los datos obligatorios
        has_items = bool(state.get("items"))
        has_entrega = state.get("tipo_entrega") in ("DOMICILIO", "RECOGER_LOCAL", "MESA")
        has_dir = (state.get("tipo_entrega") != "DOMICILIO") or bool(state.get("direccion_entrega"))
        has_pago = state.get("metodo_pago") in ("EFECTIVO", "TRANSFERENCIA")

        if has_items and has_entrega and has_dir and has_pago:
            pedido = _create_order_from_session(db, sesion, comercio, sucursal, state, "SOLO_IA")
            checkout_data = simulated_gateway.create_checkout_session(db, pedido)
            checkout_rel = checkout_data["checkout_url"]
            checkout_full = f"{base_url}{checkout_rel}"
            pedido_creado = {
                "id": pedido.id,
                "folio": pedido.folio,
                "total": pedido.total,
                "estado_pedido": pedido.estado_pedido,
                "estado_pago": pedido.estado_pago,
                "checkout_url": checkout_rel,
            }
            state.clear()
            state.update(_empty_state())
            state["pedido_activo_id"] = pedido.id
            sesion.estado_conversacion = "SIGUIENDO_PEDIDO"
            # Exigir pago previo antes de entrar al panel del comercio (listo para Pagadito API)
            from app.core.demo_seeds import get_giro_notification_copy

            gc = get_giro_notification_copy(comercio.giro)
            noun_cap = gc["item_noun"].capitalize()
            reply_text = (
                f"*{noun_cap} registrado — Pendiente de Pago* (*#{pedido.folio}*)\n"
                f"Total a pagar: *{sym}{pedido.total:.2f}*\n\n"
                f"Para confirmar tu {gc['item_noun']} y enviarlo a {copy['prep_word']}, realiza tu pago en este enlace seguro:\n"
                f"{checkout_full}\n\n"
                f"_Tu {gc['item_noun']} se confirmará automáticamente en cuanto se apruebe el pago._"
            )

    _save_session_state(sesion, state)
    if reply_text:
        db.add(
            MensajeChatWhatsApp(
                sesion_id=sesion.id,
                tenant_id=comercio.id,
                remitente="BOT",
                tipo_contenido="TEXTO",
                contenido=reply_text,
            )
        )
    db.commit()

    if pedido_creado and pedido_creado.get("estado_pedido") != "ESPERANDO_PAGO":
        publish_tenant_event(
            comercio.id,
            "NUEVO_PEDIDO",
            {"pedido_id": pedido_creado["id"], "folio": pedido_creado["folio"]},
        )
    elif reserva_creada:
        publish_tenant_event(
            comercio.id,
            "NUEVA_RESERVACION",
            {"reserva_id": reserva_creada["id"], "folio": reserva_creada["folio"]},
        )
    else:
        publish_tenant_event(
            comercio.id,
            "NUEVO_MENSAJE",
            {"sesion_id": sesion.id},
        )

    _added_items = acciones.get("agregar_items") if isinstance(acciones, dict) else None
    _first_pid = (
        _added_items[0].get("plato_id")
        if isinstance(_added_items, list) and _added_items and isinstance(_added_items[0], dict)
        else None
    )
    return {
        "reply_text": reply_text,
        "reply_image_url": _get_product_public_image_url(db, _first_pid),
        "sesion_id": sesion.id,
        "modo_atencion": sesion.modo_atencion,
        "estado_conversacion": sesion.estado_conversacion,
        "pedido_creado": pedido_creado,
        "reserva_creada": reserva_creada,
        "quick_replies": [],
        "ai_used": ai_used,
    }


def process_incoming_whatsapp_message(
    db: Session,
    telefono_raw: str,
    texto_raw: str,
    nombre_cliente: str = "Cliente WhatsApp",
    forced_sucursal_id: Optional[int] = None,
    modo_bot: str = "MENU_GUIADO",
) -> Dict[str, Any]:
    telefono = clean_whatsapp_phone(telefono_raw) or "50500000000"
    texto = (texto_raw or "").strip()
    if not texto:
        return {
            "reply_text": "¡Hola! Escribe *menú* para ver lo que tenemos disponible hoy.",
            "sesion_id": None,
            "modo_atencion": "BOT_ACTIVO",
            "estado_conversacion": "INICIO",
            "pedido_creado": None,
            "quick_replies": ["Menú"],
            "ai_used": None,
        }

    modo = (modo_bot or "MENU_GUIADO").upper().strip()
    if modo not in ("MENU_GUIADO", "HIBRIDO_IA", "SOLO_IA"):
        modo = "MENU_GUIADO"

    sesion, prompt_seleccion = _resolve_or_create_session(
        db, telefono, nombre_cliente, texto, forced_sucursal_id=forced_sucursal_id
    )
    if not sesion:
        return {
            "reply_text": prompt_seleccion,
            "sesion_id": None,
            "modo_atencion": "BOT_ACTIVO",
            "estado_conversacion": "SELECCION_COMERCIO",
            "pedido_creado": None,
            "quick_replies": ["1", "2"],
            "ai_used": None,
        }

    comercio = db.query(TenantComercio).filter(TenantComercio.id == sesion.tenant_id).first()
    sucursal = db.query(SucursalComercio).filter(SucursalComercio.id == sesion.sucursal_id).first()
    if not comercio or not sucursal:
        return {
            "reply_text": "Este comercio no está disponible en este momento.",
            "sesion_id": sesion.id,
            "modo_atencion": sesion.modo_atencion,
            "estado_conversacion": sesion.estado_conversacion,
            "pedido_creado": None,
            "quick_replies": [],
            "ai_used": None,
        }

    sesion.ultimo_mensaje_en = _utcnow()
    db.add(
        MensajeChatWhatsApp(
            sesion_id=sesion.id,
            tenant_id=comercio.id,
            remitente="CLIENTE",
            tipo_contenido="TEXTO",
            contenido=texto,
        )
    )

    if sesion.modo_atencion == "HUMANO_ACTIVO":
        db.commit()
        return {
            "reply_text": None,
            "sesion_id": sesion.id,
            "modo_atencion": "HUMANO_ACTIVO",
            "estado_conversacion": sesion.estado_conversacion,
            "pedido_creado": None,
            "quick_replies": [],
            "ai_used": None,
        }

    state = _load_session_state(sesion)
    categorias, platos = _get_available_catalog(db, comercio.id, sucursal.id)
    cats_con_items = [c for c in categorias if any(p.categoria_id == c.id for p in platos)]

    # MODO SOLO_IA: la IA maneja todo el flujo respetando restricciones y precios reales en BD
    if modo == "SOLO_IA":
        ia_turn = _handle_solo_ia_turn(
            db, sesion, comercio, sucursal, categorias, platos, state, telefono, texto
        )
        if ia_turn is not None:
            return ia_turn

    norm = _normalize_text(texto)
    upper = texto.upper().strip()
    ai_used: Optional[str] = None
    pedido_creado: Optional[Dict[str, Any]] = None
    reserva_creada: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = ["1", "2"]
    reply_text = ""

    # -----------------------------------------------------------------
    # CONCIENCIA DE PEDIDO ACTIVO (SIGUIENDO_PEDIDO)
    # Mantiene el chat consciente del pedido hasta ENTREGADO / CANCELADO
    # -----------------------------------------------------------------
    if sesion.estado_conversacion == "SIGUIENDO_PEDIDO" and state.get("pedido_activo_id"):
        ped_act = (
            db.query(Pedido)
            .filter(Pedido.id == int(state["pedido_activo_id"]))
            .first()
        )
        if not ped_act or ped_act.estado_pedido in ("ENTREGADO", "CANCELADO"):
            # El pedido ya finalizó -> reiniciar limpiamente para nuevo pedido
            state = _empty_state()
            sesion.estado_conversacion = "EXPLORANDO_MENU"
        else:
            if upper == "3" or _fuzzy_contains_any(norm, ["humano", "asesor", "persona", "agente", "ayuda"]):
                sesion.modo_atencion = "HUMANO_ACTIVO"
                reply_text = f"¡Con gusto! Te comunico con el equipo de *{comercio.nombre_comercial}* sobre tu pedido *#{ped_act.folio}*."
                quick_replies = []
            elif upper == "2" or _fuzzy_contains_any(norm, ["nuevo", "otro", "adicional", "menu", "pedir"]):
                state = _empty_state()
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif upper == "1" or _fuzzy_contains_any(norm, ["detalle", "resumen", "que pedi"]):
                reply_text = _format_active_order_detail(comercio, ped_act)
                quick_replies = ["2", "3"]
            else:
                reply_text = _format_active_order_status(comercio, sucursal, ped_act)
                quick_replies = ["1", "2", "3"]

            _save_session_state(sesion, state)
            db.add(
                MensajeChatWhatsApp(
                    sesion_id=sesion.id,
                    tenant_id=comercio.id,
                    remitente="BOT",
                    tipo_contenido="TEXTO",
                    contenido=reply_text,
                )
            )
            db.commit()
            return {
                "reply_text": reply_text,
                "sesion_id": sesion.id,
                "modo_atencion": sesion.modo_atencion,
                "estado_conversacion": sesion.estado_conversacion,
                "pedido_creado": None,
                "quick_replies": quick_replies,
                "ai_used": None,
            }

    # Detección IA de reclamos / casos especiales (antes de los comandos normales, solo si dispara pre-filtro)
    if (
        not _fuzzy_contains_any(norm, ["humano", "asesor", "agente", "persona"])
        and _looks_like_complaint_or_special_case(norm)
        and sesion.estado_conversacion != "PIDIENDO_DIRECCION"
    ):
        escalate = False
        motivo_esc = "Caso especial / reclamo detectado"
        empatica = (
            f"Lamento mucho el inconveniente. Ya pasé tu mensaje directamente al equipo de *{comercio.nombre_comercial}* "
            "para que una persona te atienda de inmediato por este chat."
        )
        if modo == "HIBRIDO_IA":
            ai_esc = detect_escalation_or_complaint_ai(texto, comercio.nombre_comercial)
            if ai_esc:
                ai_used = ai_esc.get("_provider")
                escalate = bool(ai_esc.get("requiere_humano"))
                if ai_esc.get("motivo"):
                    motivo_esc = str(ai_esc["motivo"])
                if ai_esc.get("respuesta_empatica"):
                    empatica = str(ai_esc["respuesta_empatica"])
        else:
            escalate = True

        if escalate:
            sesion.modo_atencion = "HUMANO_ACTIVO"
            sesion.resumen_ia_chat = f"Atención humana requerida ({motivo_esc}): {texto[:160]}"
            _save_session_state(sesion, state)
            db.add(
                MensajeChatWhatsApp(
                    sesion_id=sesion.id,
                    tenant_id=comercio.id,
                    remitente="BOT",
                    tipo_contenido="TEXTO",
                    contenido=empatica,
                )
            )
            db.commit()
            return {
                "reply_text": empatica,
                "sesion_id": sesion.id,
                "modo_atencion": sesion.modo_atencion,
                "estado_conversacion": sesion.estado_conversacion,
                "pedido_creado": None,
                "quick_replies": [],
                "ai_used": ai_used,
            }

    # Comandos globales naturales (con tolerancia a typos)
    if _fuzzy_contains_any(norm, ["humano", "asesor", "agente", "persona"]):
        sesion.modo_atencion = "HUMANO_ACTIVO"
        reply_text = (
            f"¡Con gusto! Te comunico ahora mismo con alguien de *{comercio.nombre_comercial}*. "
            "En un momento te responden por aquí."
        )
        quick_replies = []
    elif _fuzzy_contains_any(norm, ["vaciar", "reiniciar"]):
        state = _empty_state()
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = "Listo, vaciamos tu carrito.\n\n" + _format_welcome_categories(
            comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
        )
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
    elif (
        norm in ("menu", "inicio", "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "catalogo")
        or _extract_branch_code(texto)
    ):
        state["categoria_id"] = None
        state["pending_item"] = None
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = _format_welcome_categories(
            comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=False, telefono_cliente=telefono
        )
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
        if _giro_copy(comercio.giro)["uses_tables"] and comercio.reservaciones_habilitadas and any(m.activa for m in sucursal.mesas):
            quick_replies.append("Reservar mesa")
    elif norm in ("ver", "carrito", "mi pedido"):
        sesion.estado_conversacion = "PREGUNTANDO_MAS" if state["items"] else "EXPLORANDO_MENU"
        reply_text = _format_cart_view(comercio, state)
        quick_replies = ["1", "2", "3"] if state["items"] else ["Menú"]
    elif _fuzzy_contains_any(norm, ["reservar", "reservacion", "apartar mesa"]) and sesion.estado_conversacion not in ("RESERVA_PERSONAS", "RESERVA_MESA", "RESERVA_FECHA", "RESERVA_NOMBRE", "RESERVA_CONFIRMAR"):
        copy_g = _giro_copy(comercio.giro)
        mesas_act = [m for m in sucursal.mesas if m.activa]
        if not copy_g["uses_tables"] or not mesas_act:
            reply_text = f"*{comercio.nombre_comercial}* no cuenta con reservación de mesas en esta sucursal. Escribe *menú* para ver nuestros productos."
            quick_replies = ["Menú"]
        elif not comercio.reservaciones_habilitadas:
            reply_text = (
                f"Las reservaciones automáticas en *{comercio.nombre_comercial}* se coordinan con un asesor. "
                "Escribe *asesor* si deseas que te ayuden a apartar mesa."
            )
            quick_replies = ["Asesor", "Menú"]
        else:
            state["reserva_draft"] = {}
            sesion.estado_conversacion = "RESERVA_PERSONAS"
            reply_text = "¡Con gusto apartamos tu mesa!\n¿Para cuántas personas sería la reservación?\n_Responde *0* para cancelar._"
            quick_replies = ["2", "4", "6", "0"]
    elif _fuzzy_contains_any(norm, ["listo", "finalizar", "pagar"]) and sesion.estado_conversacion in ("EXPLORANDO_MENU", "ELIGIENDO_PRODUCTO", "PREGUNTANDO_MAS"):
        if not state["items"]:
            reply_text = "Todavía no has elegido ningún producto. Dime qué sección te gustaría ver:\n\n" + _format_welcome_categories(
                comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
            )
            quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
        else:
            sesion.estado_conversacion = "PIDIENDO_MODALIDAD"
            reply_text, quick_replies = _format_delivery_options(comercio, sucursal)
    else:
        est = sesion.estado_conversacion

        # 1. EXPLORANDO_MENU -> Elegir categoría por número o nombre aproximado (o producto directo)
        if est == "EXPLORANDO_MENU":
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None
            cat_idx = (num - 1) if (num and 1 <= num <= len(cats_con_items)) else _fuzzy_match_options(texto, [c.nombre for c in cats_con_items], cutoff=0.66)

            if cat_idx is not None and 0 <= cat_idx < len(cats_con_items):
                cat_obj = cats_con_items[cat_idx]
                state["categoria_id"] = cat_obj.id
                sesion.estado_conversacion = "ELIGIENDO_PRODUCTO"
                platos_cat = [p for p in platos if p.categoria_id == cat_obj.id]
                reply_text = _format_products_list(comercio, cat_obj, platos_cat)
                quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0"]
            else:
                # Intentar coincidencia directa con algún producto del catálogo (ej: "quiero piza peperoni")
                plato_idx = _fuzzy_match_options(texto, [p.nombre for p in platos], cutoff=0.70)
                if plato_idx is not None:
                    pl = platos[plato_idx]
                    sym = comercio.simbolo_moneda or "C$"
                    state["pending_item"] = {
                        "plato_id": pl.id,
                        "nombre": pl.nombre,
                        "precio_base": float(pl.precio_base),
                        "cantidad": 1,
                    }
                    sesion.estado_conversacion = "PIDIENDO_CANTIDAD"
                    reply_text = _format_product_selected_qty_prompt(db, comercio, pl)
                    quick_replies = ["1", "2", "3", "0"]
                elif ENABLE_AI_NATURAL_ORDER and modo == "HIBRIDO_IA":
                    catalogo_resumido = [
                        {
                            "plato_id": p.id,
                            "nombre": p.nombre,
                            "precio": p.precio_base,
                            "categoria": p.categoria.nombre if p.categoria else "",
                        }
                        for p in platos
                    ]
                    ai_res = interpret_natural_order_message(
                        texto, comercio.nombre_comercial, comercio.giro, catalogo_resumido
                    )
                    if ai_res:
                        ai_used = ai_res.get("_provider")
                        acc = str(ai_res.get("accion") or "").upper()
                        if acc == "HUMANO":
                            sesion.modo_atencion = "HUMANO_ACTIVO"
                            reply_text = f"¡Con gusto! Te paso con alguien del equipo de *{comercio.nombre_comercial}*."
                            quick_replies = []
                        elif acc == "AGREGAR" and ai_res.get("items"):
                            plato_map = {p.id: p for p in platos}
                            agregados = []
                            for item_ai in ai_res["items"]:
                                pl = plato_map.get(int(item_ai.get("plato_id") or 0))
                                if not pl:
                                    continue
                                qty = max(1, min(50, int(item_ai.get("cantidad") or 1)))
                                state["items"].append(
                                    {
                                        "plato_id": pl.id,
                                        "nombre": pl.nombre,
                                        "precio_base": float(pl.precio_base),
                                        "cantidad": qty,
                                        "sin_ingredientes": item_ai.get("sin_ingredientes") or [],
                                        "extras": [],
                                        "precio_extras": 0.0,
                                        "notas": item_ai.get("notas") or "",
                                    }
                                )
                                agregados.append(f"{qty}x {pl.nombre}")
                            if agregados:
                                sesion.estado_conversacion = "PREGUNTANDO_MAS"
                                reply_text = _format_ask_more(comercio, state, ", ".join(agregados))
                                quick_replies = ["1", "2", "3"]
                            else:
                                reply_text = _format_welcome_categories(
                                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                                )
                        elif acc == "CONSULTA" and ai_res.get("respuesta_breve"):
                            reply_text = ai_res["respuesta_breve"]
                            quick_replies = ["Menú"]
                        else:
                            reply_text = _format_welcome_categories(
                                comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                            )
                            quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
                    else:
                        reply_text = "No te entendí del todo, responde con el número o nombre de la categoría:\n\n" + _format_welcome_categories(
                            comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                        )
                        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
                else:
                    reply_text = "Responde con el número o el nombre de la categoría que deseas ver:\n\n" + _format_welcome_categories(
                        comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                    )
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]

        # 2. ELIGIENDO_PRODUCTO -> Por número, nombre aproximado o paginación (+ / -)
        elif est == "ELIGIENDO_PRODUCTO":
            cat_obj = next((c for c in cats_con_items if c.id == state.get("categoria_id")), None)
            platos_cat = [p for p in platos if p.categoria_id == state.get("categoria_id")]
            cur_page = int(state.get("productos_page") or 0)
            total_pages = max(1, (len(platos_cat) + 5) // 6)
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None

            if num == 0 or _fuzzy_contains_any(norm, ["volver", "atras", "regresar"]) or not cat_obj:
                state["categoria_id"] = None
                state["productos_page"] = 0
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif texto.strip() == "+" or _fuzzy_contains_any(norm, ["mas productos", "siguiente", "ver mas"]):
                cur_page = min(total_pages - 1, cur_page + 1)
                state["productos_page"] = cur_page
                reply_text = _format_products_list(comercio, cat_obj, platos_cat, page=cur_page)
                quick_replies = ["+", "-", "0"] if cur_page + 1 < total_pages else ["-", "0"]
            elif texto.strip() == "-" or _fuzzy_contains_any(norm, ["anterior", "pagina anterior"]):
                cur_page = max(0, cur_page - 1)
                state["productos_page"] = cur_page
                reply_text = _format_products_list(comercio, cat_obj, platos_cat, page=cur_page)
                quick_replies = ["+", "0"] if cur_page + 1 < total_pages else ["0"]
            else:
                p_idx = (num - 1) if (num and 1 <= num <= len(platos_cat)) else _fuzzy_match_options(texto, [p.nombre for p in platos_cat], cutoff=0.62)
                if p_idx is not None and 0 <= p_idx < len(platos_cat):
                    pl = platos_cat[p_idx]
                    sym = comercio.simbolo_moneda or "C$"
                    state["pending_item"] = {
                        "plato_id": pl.id,
                        "nombre": pl.nombre,
                        "precio_base": float(pl.precio_base),
                        "cantidad": 1,
                    }
                    sesion.estado_conversacion = "PIDIENDO_CANTIDAD"
                    reply_text = _format_product_selected_qty_prompt(db, comercio, pl)
                    quick_replies = ["1", "2", "3", "0"]
                else:
                    reply_text = _format_products_list(comercio, cat_obj, platos_cat, page=cur_page)
                    quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0"]

        # 3. PIDIENDO_CANTIDAD -> Acepta dígitos, palabras ("dos") o 0/cancelar para volver atrás
        elif est == "PIDIENDO_CANTIDAD":
            pending = state.get("pending_item")
            qty_num = _extract_number(texto)
            if not pending:
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
            elif texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar", "cancelo", "volver", "atras", "otro", "no quiero"]):
                state["pending_item"] = None
                cat_obj = next((c for c in cats_con_items if c.id == state.get("categoria_id")), None)
                if cat_obj:
                    platos_cat = [p for p in platos if p.categoria_id == cat_obj.id]
                    sesion.estado_conversacion = "ELIGIENDO_PRODUCTO"
                    reply_text = "Sin problema, cancelé ese producto.\n\n" + _format_products_list(
                        comercio, cat_obj, platos_cat, page=int(state.get("productos_page") or 0)
                    )
                    quick_replies = ["1", "2", "0"]
                else:
                    sesion.estado_conversacion = "EXPLORANDO_MENU"
                    reply_text = "Sin problema, cancelé ese producto.\n\n" + _format_welcome_categories(
                        comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                    )
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif qty_num and 1 <= int(qty_num) <= 50:
                pending["cantidad"] = int(qty_num)
                removibles, extras = _get_item_customization_options(db, pending["plato_id"])
                if removibles or extras:
                    state["pending_item"] = pending
                    sesion.estado_conversacion = "PERSONALIZANDO_ITEM"
                    reply_text, quick_replies = _format_customization_prompt(comercio, pending, removibles, extras)
                else:
                    state["items"].append(
                        {
                            "plato_id": pending["plato_id"],
                            "nombre": pending["nombre"],
                            "precio_base": pending["precio_base"],
                            "cantidad": pending["cantidad"],
                            "sin_ingredientes": [],
                            "extras": [],
                            "precio_extras": 0.0,
                            "notas": "",
                        }
                    )
                    state["pending_item"] = None
                    sesion.estado_conversacion = "PREGUNTANDO_MAS"
                    reply_text = _format_ask_more(comercio, state, f"{pending['cantidad']}x {pending['nombre']}")
                    quick_replies = ["1", "2", "3"]
            else:
                reply_text = "Indícame cuántas unidades deseas (ej: *1*, *2* o *dos*), o responde *0* para cancelar y elegir otro producto."
                quick_replies = ["1", "2", "3", "0"]

        # 4. PERSONALIZANDO_ITEM -> Tolerante a "no quiero pepinillas", "quitale sebolla", extras o 0 para cancelar
        elif est == "PERSONALIZANDO_ITEM":
            pending = state.get("pending_item")
            if not pending:
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
            elif texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar", "cancelo", "volver", "atras"]):
                state["pending_item"] = None
                cat_obj = next((c for c in cats_con_items if c.id == state.get("categoria_id")), None)
                if cat_obj:
                    platos_cat = [p for p in platos if p.categoria_id == cat_obj.id]
                    sesion.estado_conversacion = "ELIGIENDO_PRODUCTO"
                    reply_text = "Producto cancelado.\n\n" + _format_products_list(
                        comercio, cat_obj, platos_cat, page=int(state.get("productos_page") or 0)
                    )
                    quick_replies = ["1", "2", "0"]
                else:
                    sesion.estado_conversacion = "EXPLORANDO_MENU"
                    reply_text = "Producto cancelado.\n\n" + _format_welcome_categories(
                        comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                    )
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            else:
                removibles, extras = _get_item_customization_options(db, pending["plato_id"])
                extras_sel: List[Dict[str, Any]] = []
                sin_sel: List[str] = []
                understood = False

                if upper == "1" or _fuzzy_contains_any(norm, ["tal cual", "normal", "asi", "perfecto", "nada", "ninguno"]):
                    understood = True
                elif texto.strip().isdigit() and 2 <= int(texto.strip()) <= len(extras[:4]) + 1:
                    ex = extras[int(texto.strip()) - 2]
                    extras_sel.append({"nombre": ex.nombre, "precio": float(ex.precio_adicional)})
                    understood = True
                else:
                    # 1) Probar exclusiones naturales con fuzzy ("no quiero pepinillas", "quitale sebolla", "sin tomte")
                    parsed_sin = _parse_natural_exclusions(texto, removibles)
                    if parsed_sin:
                        sin_sel = parsed_sin
                        understood = True
                    # 2) Probar extra mencionado por nombre aproximado ("extra kezo", "con tocino")
                    if extras:
                        ex_idx = _fuzzy_match_options(texto, [e.nombre for e in extras], cutoff=0.66)
                        if ex_idx is not None and not _fuzzy_contains_any(norm, ["sin", "no quiero", "quitale"]):
                            ex = extras[ex_idx]
                            extras_sel.append({"nombre": ex.nombre, "precio": float(ex.precio_adicional)})
                            understood = True

                if not understood:
                    reply_text, quick_replies = _format_customization_prompt(comercio, pending, removibles, extras)
                    _save_session_state(sesion, state)
                    db.add(MensajeChatWhatsApp(sesion_id=sesion.id, tenant_id=comercio.id, remitente="BOT", tipo_contenido="TEXTO", contenido=reply_text))
                    db.commit()
                    return {
                        "reply_text": reply_text,
                        "sesion_id": sesion.id,
                        "modo_atencion": sesion.modo_atencion,
                        "estado_conversacion": sesion.estado_conversacion,
                        "pedido_creado": None,
                        "quick_replies": quick_replies,
                        "ai_used": None,
                    }

                precio_extras = round(sum(e["precio"] for e in extras_sel), 2)
                state["items"].append(
                    {
                        "plato_id": pending["plato_id"],
                        "nombre": pending["nombre"],
                        "precio_base": pending["precio_base"],
                        "cantidad": pending["cantidad"],
                        "sin_ingredientes": sin_sel,
                        "extras": extras_sel,
                        "precio_extras": precio_extras,
                        "notas": "",
                    }
                )
                det_parts = []
                if sin_sel:
                    det_parts.append(f"sin {', '.join(sin_sel)}")
                if extras_sel:
                    det_parts.append(f"+{extras_sel[0]['nombre']}")
                det_txt = f" ({'; '.join(det_parts)})" if det_parts else ""
                label = f"{pending['cantidad']}x {pending['nombre']}{det_txt}"
                state["pending_item"] = None
                sesion.estado_conversacion = "PREGUNTANDO_MAS"
                reply_text = _format_ask_more(comercio, state, label)
                quick_replies = ["1", "2", "3"]

        # 5. PREGUNTANDO_MAS
        elif est == "PREGUNTANDO_MAS":
            if upper == "1" or _fuzzy_contains_any(norm, ["si", "mas", "agregar", "otro", "menu"]):
                state["categoria_id"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif upper == "2" or _fuzzy_contains_any(norm, ["no", "continuar", "pedir", "finalizar", "eso es todo", "listo"]):
                sesion.estado_conversacion = "PIDIENDO_MODALIDAD"
                reply_text, quick_replies = _format_delivery_options(comercio, sucursal)
            elif upper == "3" or _fuzzy_contains_any(norm, ["ver", "carrito"]):
                reply_text = _format_cart_view(comercio, state)
                quick_replies = ["1", "2", "3"]
            else:
                reply_text = "Responde con el número:\n*1.* Agregar otro producto\n*2.* Continuar con mi pedido\n*3.* Ver mi carrito"
                quick_replies = ["1", "2", "3"]

        # 6. PIDIENDO_MODALIDAD
        elif est == "PIDIENDO_MODALIDAD":
            if (upper == "1" or _fuzzy_contains_any(norm, ["domicilio", "envio", "delivery", "casa", "llevar a"])) and sucursal.permite_domicilio:
                state["tipo_entrega"] = "DOMICILIO"
                zonas = [z for z in sucursal.zonas_envio if z.activa]
                if zonas:
                    sesion.estado_conversacion = "PIDIENDO_ZONA"
                    reply_text, quick_replies = _format_zones_options(comercio, sucursal, zonas)
                else:
                    state["zona_envio_id"] = None
                    state["zona_nombre"] = None
                    state["costo_envio"] = float(sucursal.costo_envio_base or 0.0)
                    sesion.estado_conversacion = "PIDIENDO_DIRECCION"
                    cli_prev = (
                        db.query(ClienteFrecuente)
                        .filter(ClienteFrecuente.tenant_id == comercio.id, ClienteFrecuente.telefono_whatsapp == telefono)
                        .first()
                    )
                    if cli_prev and cli_prev.ultima_direccion:
                        reply_text = f"¿Te lo enviamos a tu última dirección?\n_{cli_prev.ultima_direccion}_\n\n*1.* Sí, a esa misma\nO escribe tu nueva dirección."
                        quick_replies = ["1"]
                    else:
                        reply_text = "¿Cuál es tu dirección de entrega? (Indica calle, número o referencia)."
                        quick_replies = []
            elif (upper == "2" or _fuzzy_contains_any(norm, ["recoger", "local", "mostrador", "retirar", "paso"])) and sucursal.permite_recoger_local:
                state["tipo_entrega"] = "RECOGER_LOCAL"
                state["direccion_entrega"] = f"Recoger en {sucursal.nombre_sucursal}"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text, quick_replies = _format_payment_options(comercio)
            elif (upper == "3" or _fuzzy_contains_any(norm, ["mesa", "comer aqui", "comer ahi"])) and _giro_copy(comercio.giro)["uses_tables"]:
                state["tipo_entrega"] = "MESA"
                state["direccion_entrega"] = f"Consumo en mesa ({sucursal.nombre_sucursal})"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text, quick_replies = _format_payment_options(comercio)
            else:
                reply_text, quick_replies = _format_delivery_options(comercio, sucursal)

        # 7. PIDIENDO_ZONA -> Por número o nombre aproximado de zona
        elif est == "PIDIENDO_ZONA":
            zonas = [z for z in sucursal.zonas_envio if z.activa]
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None
            z_idx = (num - 1) if (num and 1 <= num <= len(zonas)) else _fuzzy_match_options(texto, [z.nombre_zona for z in zonas], cutoff=0.64)

            matched_zone_step = False
            if z_idx is not None and 0 <= z_idx < len(zonas):
                z = zonas[z_idx]
                state["zona_envio_id"] = z.id
                state["zona_nombre"] = z.nombre_zona
                state["costo_envio"] = float(z.costo_envio)
                matched_zone_step = True
            elif (num and num == len(zonas) + 1) or _fuzzy_contains_any(norm, ["otra", "ninguna"]):
                state["zona_envio_id"] = None
                state["zona_nombre"] = None
                state["costo_envio"] = float(sucursal.costo_envio_base or 0.0)
                matched_zone_step = True
            else:
                reply_text, quick_replies = _format_zones_options(comercio, sucursal, zonas)

            if matched_zone_step:
                sesion.estado_conversacion = "PIDIENDO_DIRECCION"
                cli_prev = (
                    db.query(ClienteFrecuente)
                    .filter(ClienteFrecuente.tenant_id == comercio.id, ClienteFrecuente.telefono_whatsapp == telefono)
                    .first()
                )
                if cli_prev and cli_prev.ultima_direccion:
                    reply_text = f"¿Te lo enviamos a tu última dirección?\n_{cli_prev.ultima_direccion}_\n\n*1.* Sí, a esa misma\nO escribe tu nueva dirección."
                    quick_replies = ["1"]
                else:
                    reply_text = "Ahora escríbeme tu *dirección exacta* (casa, calle o referencia)."
                    quick_replies = []

        # 8. PIDIENDO_DIRECCION (con validación inteligente de Gemini para evitar direcciones vagas)
        elif est == "PIDIENDO_DIRECCION":
            reused_prev = False
            direccion = texto
            if upper == "1" or norm in ("si", "misma", "la misma", "usar", "esa misma"):
                cli_prev = (
                    db.query(ClienteFrecuente)
                    .filter(ClienteFrecuente.tenant_id == comercio.id, ClienteFrecuente.telefono_whatsapp == telefono)
                    .first()
                )
                if cli_prev and cli_prev.ultima_direccion:
                    direccion = cli_prev.ultima_direccion
                    reused_prev = True

            dir_valida = True
            pregunta_dir = ""
            if not reused_prev:
                # Filtro rápido local (0 tokens) para respuestas obviamente demasiado vagas
                palabras_vagas = {"casa", "mi casa", "aqui", "aca", "centro", "parque", "donde siempre", "no se"}
                if len(norm) < 6 or norm in palabras_vagas:
                    dir_valida = False
                    pregunta_dir = (
                        "Para que el repartidor llegue sin problema, ¿me compartes un poco más de detalle? "
                        "(Por ejemplo: barrio o colonia, número de casa y alguna referencia cercana)."
                    )
                elif modo == "HIBRIDO_IA":
                    zonas_act = [z for z in sucursal.zonas_envio if z.activa]
                    zonas_payload = [
                        {"zona_id": z.id, "nombre": z.nombre_zona, "costo": float(z.costo_envio or 0.0)}
                        for z in zonas_act
                    ]
                    ai_dir = validate_and_normalize_address_ai(texto, zonas_payload, sucursal.nombre_sucursal)
                    if ai_dir:
                        ai_used = ai_dir.get("_provider")
                        dir_valida = bool(ai_dir.get("valida", True))
                        if dir_valida:
                            if ai_dir.get("direccion_limpia"):
                                direccion = str(ai_dir["direccion_limpia"]).strip()
                            # Si el cliente aún no tenía zona asignada y Gemini detectó una zona válida, aplicarla
                            if not state.get("zona_envio_id") and ai_dir.get("zona_id"):
                                z_match = next((z for z in zonas_act if z.id == int(ai_dir["zona_id"])), None)
                                if z_match:
                                    state["zona_envio_id"] = z_match.id
                                    state["zona_nombre"] = z_match.nombre_zona
                                    state["costo_envio"] = float(z_match.costo_envio)
                        else:
                            pregunta_dir = (
                                str(ai_dir.get("pregunta_aclaracion") or "").strip()
                                or "¿Me podrías dar una referencia más exacta (barrio, calle o color de casa) para el repartidor?"
                            )

            if not dir_valida:
                reply_text = pregunta_dir
                quick_replies = []
            else:
                state["direccion_entrega"] = direccion
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text, quick_replies = _format_payment_options(comercio)

        # 9. PIDIENDO_PAGO -> Tolerante a "efektivo", "tranferencia"
        elif est == "PIDIENDO_PAGO":
            if (upper == "1" or _fuzzy_contains_any(norm, ["efectivo", "cash", "billete"])) and comercio.acepta_efectivo:
                state["metodo_pago"] = "EFECTIVO"
                sesion.estado_conversacion = "PIDIENDO_CAMBIO"
                sym = comercio.simbolo_moneda or "C$"
                sub = _calc_subtotal(state["items"])
                env = float(state.get("costo_envio") or 0.0) if state.get("tipo_entrega") == "DOMICILIO" else 0.0
                reply_text = f"Tu total es *{sym}{sub + env:.2f}*.\n¿Con cuánto vas a pagar para llevarte vuelto?\n\nEscribe el monto (ej: *500*) o responde *0* si pagas exacto."
                quick_replies = ["0", "500", "1000"]
            elif (upper == "2" or _fuzzy_contains_any(norm, ["transferencia", "banco", "deposito"])) and comercio.acepta_transferencia:
                state["metodo_pago"] = "TRANSFERENCIA"
                state["paga_con"] = None
                sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2", "3"]
            else:
                reply_text, quick_replies = _format_payment_options(comercio)

        # 10. PIDIENDO_CAMBIO
        elif est == "PIDIENDO_CAMBIO":
            if upper == "0" or _fuzzy_contains_any(norm, ["exacto", "cabal", "justo"]):
                state["paga_con"] = None
                sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2", "3"]
            else:
                monto = _extract_number(texto)
                if monto is not None and monto > 0:
                    state["paga_con"] = round(monto, 2)
                    sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                    reply_text = _format_final_confirmation(comercio, state)
                    quick_replies = ["1", "2", "3"]
                else:
                    reply_text = "Por favor escribe el billete con el que pagas (ej: *500*) o *0* si pagas exacto."
                    quick_replies = ["0", "500"]

        # 11. CONFIRMANDO_CARRITO -> Pasa a SIGUIENDO_PEDIDO hasta que se entregue o cancele
        elif est == "CONFIRMANDO_CARRITO":
            if upper == "1" or _fuzzy_contains_any(norm, ["si", "confirmar", "confirmo", "dale", "ok", "correcto"]):
                pedido = _create_order_from_session(db, sesion, comercio, sucursal, state, modo)
                checkout_data = simulated_gateway.create_checkout_session(db, pedido)
                checkout_rel = checkout_data["checkout_url"]
                base_url_cfg = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
                checkout_full = f"{base_url_cfg}{checkout_rel}"
                sym = comercio.simbolo_moneda or "C$"
                copy = _giro_copy(comercio.giro)
                pedido_creado = {
                    "id": pedido.id,
                    "folio": pedido.folio,
                    "total": pedido.total,
                    "estado_pedido": pedido.estado_pedido,
                    "estado_pago": pedido.estado_pago,
                    "checkout_url": checkout_rel,
                }
                state = _empty_state()
                state["pedido_activo_id"] = pedido.id
                sesion.estado_conversacion = "SIGUIENDO_PEDIDO"
                from app.core.demo_seeds import get_giro_notification_copy

                gc = get_giro_notification_copy(comercio.giro)
                noun_cap = gc["item_noun"].capitalize()
                reply_text = (
                    f"*{noun_cap} registrado — Pendiente de Pago* (*#{pedido.folio}*)\n"
                    f"Total a pagar: *{sym}{pedido.total:.2f}*\n\n"
                    f"Para confirmar tu {gc['item_noun']} y enviarlo a {copy['prep_word']}, completa tu pago aquí:\n"
                    f"{checkout_full}\n\n"
                    f"_Tu {gc['item_noun']} se confirmará automáticamente en cuanto se apruebe el pago._"
                )
                quick_replies = ["1", "2", "3"]
            elif upper == "2" or _fuzzy_contains_any(norm, ["cambiar", "editar", "agregar"]):
                sesion.estado_conversacion = "PREGUNTANDO_MAS"
                reply_text = _format_cart_view(comercio, state)
                quick_replies = ["1", "2", "3"]
            elif upper == "3" or _fuzzy_contains_any(norm, ["no", "cancelar", "cancelo"]):
                state = _empty_state()
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Pedido cancelado sin problema. Cuando gustes ordenar de nuevo, solo escribe *menú*."
                quick_replies = ["Menú"]
            else:
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2", "3"]

        # -----------------------------------------------------------------
        # FLUJO GUIADO DE RESERVACIÓN DE MESAS
        # -----------------------------------------------------------------
        elif est == "RESERVA_PERSONAS":
            if texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar", "volver"]):
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Reservación cancelada. Escribe *menú* cuando gustes ordenar."
                quick_replies = ["Menú"]
            else:
                p_num = _extract_number(texto)
                if p_num and 1 <= int(p_num) <= 30:
                    personas = int(p_num)
                    mesas_act = [m for m in sucursal.mesas if m.activa]
                    aptas = [m for m in mesas_act if m.capacidad_personas >= personas] or mesas_act
                    draft = dict(state.get("reserva_draft") or {})
                    draft["cantidad_personas"] = personas
                    state["reserva_draft"] = draft
                    sesion.estado_conversacion = "RESERVA_MESA"
                    sym = comercio.simbolo_moneda or "C$"
                    lines = [f"Perfecto, para *{personas} personas*. Elige la mesa que prefieras:\n"]
                    for idx, m in enumerate(aptas[:6], start=1):
                        ant = f" · Anticipo {sym}{m.monto_anticipo_reserva:.2f}" if m.monto_anticipo_reserva > 0 else ""
                        lines.append(f"*{idx}.* {m.nombre_mesa} ({m.capacidad_personas} pers.{ant})")
                    lines.append("\n*0.* Cancelar reservación")
                    reply_text = "\n".join(lines)
                    quick_replies = [str(i) for i in range(1, min(len(aptas), 4) + 1)] + ["0"]
                else:
                    reply_text = "Indícame el número de personas (ej: *2*, *4* o *6*) o *0* para cancelar."
                    quick_replies = ["2", "4", "6", "0"]

        elif est == "RESERVA_MESA":
            if texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar", "volver"]):
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Reservación cancelada. Escribe *menú* cuando gustes."
                quick_replies = ["Menú"]
            else:
                draft = dict(state.get("reserva_draft") or {})
                personas = int(draft.get("cantidad_personas") or 2)
                mesas_act = [m for m in sucursal.mesas if m.activa]
                aptas = [m for m in mesas_act if m.capacidad_personas >= personas] or mesas_act
                num = int(_extract_number(texto)) if texto.strip().isdigit() else None
                m_idx = (num - 1) if (num and 1 <= num <= len(aptas)) else _fuzzy_match_options(texto, [m.nombre_mesa for m in aptas], cutoff=0.62)
                if m_idx is not None and 0 <= m_idx < len(aptas):
                    m = aptas[m_idx]
                    draft["mesa_id"] = m.id
                    draft["mesa_nombre"] = m.nombre_mesa
                    draft["anticipo"] = float(
                        m.monto_anticipo_reserva
                        if float(m.monto_anticipo_reserva or 0.0) > 0
                        else (comercio.monto_anticipo_reserva_default or 0.0)
                    )
                    state["reserva_draft"] = draft
                    sesion.estado_conversacion = "RESERVA_FECHA"
                    horario = f"{sucursal.horario_apertura} a {sucursal.horario_cierre}"
                    reply_text = (
                        f"Excelente, *{m.nombre_mesa}*.\n"
                        f"¿Para qué fecha y hora deseas reservar? Incluye *AM o PM* (Ej: *Hoy 7:30 PM* o *30 de noviembre 7:00 PM*).\n"
                        f"_Horario de atención: {horario}._"
                    )
                    quick_replies = ["Hoy 7:00 PM", "Mañana 1:00 PM", "0"]
                else:
                    reply_text = "Por favor responde con el número de la mesa que prefieras o *0* para cancelar."
                    quick_replies = ["1", "2", "0"]

        elif est == "RESERVA_FECHA":
            if texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar"]):
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Reservación cancelada. Escribe *menú* cuando gustes."
                quick_replies = ["Menú"]
            else:
                ok_dt, msg_or_dt = _validate_reserva_datetime(texto, sucursal)
                if not ok_dt:
                    reply_text = msg_or_dt
                    quick_replies = ["Hoy 7:00 PM", "Mañana 2:00 PM", "0"]
                else:
                    draft = dict(state.get("reserva_draft") or {})
                    draft["fecha_hora"] = msg_or_dt
                    state["reserva_draft"] = draft
                    sesion.estado_conversacion = "RESERVA_NOMBRE"
                    reply_text = "¿A nombre de quién registramos la reservación?\n_Escribe tu nombre completo o responde *0* para cancelar._"
                    quick_replies = (
                        [sesion.nombre_cliente, "0"]
                        if (sesion.nombre_cliente and not sesion.nombre_cliente.lower().startswith("cliente"))
                        else ["0"]
                    )

        elif est == "RESERVA_NOMBRE":
            if texto.strip() == "0" or _fuzzy_contains_any(norm, ["cancelar"]):
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Reservación cancelada. Escribe *menú* cuando gustes."
                quick_replies = ["Menú"]
            elif len(texto.strip()) < 2:
                reply_text = "Por favor escríbeme el nombre a quien quedará la reservación (o *0* para cancelar)."
                quick_replies = ["0"]
            else:
                draft = dict(state.get("reserva_draft") or {})
                draft["nombre_cliente"] = texto.strip()[:80]
                state["reserva_draft"] = draft
                sesion.estado_conversacion = "RESERVA_CONFIRMAR"
                reply_text = _format_reserva_confirmation_summary(comercio, draft)
                quick_replies = ["1", "2"]

        elif est == "RESERVA_CONFIRMAR":
            if upper == "1" or _fuzzy_contains_any(norm, ["si", "confirmar", "confirmo", "registrar", "dale", "ok"]):
                draft = dict(state.get("reserva_draft") or {})
                reserva = _create_reservation_from_session(db, sesion, comercio, sucursal, draft)
                mesa_lbl = draft.get("mesa_nombre") or "Asignada"
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reserva_creada = {
                    "id": reserva.id,
                    "folio": reserva.folio_reserva,
                    "estado_reserva": reserva.estado_reserva,
                    "estado_pago": reserva.estado_pago,
                    "monto_anticipo": reserva.monto_pago_requerido,
                }
                reply_text = _format_reserva_created_reply(comercio, reserva, mesa_lbl)
                publish_tenant_event(
                    comercio.id,
                    "NUEVA_RESERVACION",
                    {"reserva_id": reserva.id, "folio": reserva.folio_reserva},
                )
                quick_replies = ["Menú"]
            else:
                state["reserva_draft"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Reservación cancelada sin problema. Escribe *menú* cuando gustes."
                quick_replies = ["Menú"]
        else:
            sesion.estado_conversacion = "EXPLORANDO_MENU"
            state["categoria_id"] = None
            reply_text = _format_welcome_categories(
                comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=False
            )
            quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]

    _save_session_state(sesion, state)

    if reply_text:
        db.add(
            MensajeChatWhatsApp(
                sesion_id=sesion.id,
                tenant_id=comercio.id,
                remitente="BOT",
                tipo_contenido="TEXTO",
                contenido=reply_text,
            )
        )

    db.commit()

    if pedido_creado and pedido_creado.get("estado_pedido") != "ESPERANDO_PAGO":
        publish_tenant_event(
            comercio.id,
            "NUEVO_PEDIDO",
            {"pedido_id": pedido_creado["id"], "folio": pedido_creado["folio"]},
        )
    else:
        publish_tenant_event(
            comercio.id,
            "NUEVO_MENSAJE",
            {"sesion_id": sesion.id},
        )

    reply_image_url: Optional[str] = None
    if sesion.estado_conversacion == "PIDIENDO_CANTIDAD" and isinstance(state.get("pending_item"), dict):
        reply_image_url = _get_product_public_image_url(db, state["pending_item"].get("plato_id"))

    return {
        "reply_text": reply_text,
        "reply_image_url": reply_image_url,
        "sesion_id": sesion.id,
        "modo_atencion": sesion.modo_atencion,
        "estado_conversacion": sesion.estado_conversacion,
        "pedido_creado": pedido_creado,
        "reserva_creada": reserva_creada,
        "quick_replies": quick_replies,
        "ai_used": ai_used,
    }
