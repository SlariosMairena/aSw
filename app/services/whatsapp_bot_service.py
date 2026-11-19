"""
Motor Conversacional del Bot de WhatsApp Multi-Tenant y Multi-Giro.
Soporta dos modos seleccionables:
- MENU_GUIADO (Opción 3): 100% guiado por opciones numéricas, costo cero, respuesta instantánea.
- HIBRIDO_IA (Opción 1): Combina menú guiado rápido + interpretación de lenguaje natural
  con Multi-IA (Gemini/Groq/OpenRouter/OpenAI) si hay API Key disponible.

Características:
- Detecta sucursal por código QR / Opción B ([CODIGO]) en el primer mensaje.
- Respeta modo HUMANO_ACTIVO (si el empleado tomó control en el panel, el bot no interrumpe).
- Filtra productos agotados globalmente o agotados solo en la sucursal (StockSucursal).
- Calcula todos los precios, tarifas de zona de envío y vueltos exclusivamente desde la BD.
- Crea el Pedido + DetallePedido + PagoTransaccion + ClienteFrecuente directo en el Kanban.
"""
import json
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.core.demo_seeds import normalize_giro_code
from app.models.saas_models import (
    CategoriaMenu,
    ClienteFrecuente,
    DetallePedido,
    MensajeChatWhatsApp,
    PagoTransaccion,
    Pedido,
    ProductoPlato,
    SesionChatWhatsApp,
    StockSucursal,
    SucursalComercio,
    TenantComercio,
    ZonaEnvioSucursal,
)
from app.services.ai_router import interpret_natural_order_message
from app.services.whatsapp_provider import clean_whatsapp_phone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _extract_branch_code(text: str) -> Optional[str]:
    """Extrae el código de sucursal de mensajes como 'Hola! Quiero pedir en Napoli [Napoli-Centro]'."""
    if not text:
        return None
    m = re.search(r"\[([^\]]{2,50})\]", text)
    if m:
        return m.group(1).strip()
    return None


def _load_session_state(sesion: SesionChatWhatsApp) -> Dict[str, Any]:
    """
    Lee el carrito y metadatos de conversación almacenados en sesion.carrito_json.
    Soporta retrocompatibilidad si carrito_json era una lista simple '[]'.
    """
    default_state: Dict[str, Any] = {
        "items": [],  # [{plato_id, nombre, precio_base, cantidad, sin_ingredientes, notas}]
        "categoria_id": None,
        "tipo_entrega": None,  # DOMICILIO | RECOGER_LOCAL | MESA
        "direccion_entrega": None,
        "zona_envio_id": None,
        "zona_nombre": None,
        "costo_envio": 0.0,
        "metodo_pago": None,  # EFECTIVO | TRANSFERENCIA
        "paga_con": None,
    }
    try:
        raw = json.loads(sesion.carrito_json or "[]")
        if isinstance(raw, list):
            default_state["items"] = raw
            return default_state
        if isinstance(raw, dict):
            merged = dict(default_state)
            merged.update(raw)
            if not isinstance(merged.get("items"), list):
                merged["items"] = []
            return merged
    except Exception:
        pass
    return default_state


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
    platos_visibles = [p for p in platos if p.id not in agotados_ids]
    return categorias, platos_visibles


def _giro_copy(giro_raw: Optional[str]) -> Dict[str, Any]:
    g = normalize_giro_code(giro_raw)
    if g == "FARMACIA":
        return {"catalog": "catalogo de medicamentos", "uses_tables": False, "item": "producto"}
    if g == "TIENDA":
        return {"catalog": "catalogo de productos", "uses_tables": False, "item": "articulo"}
    if g == "SUPERMERCADO":
        return {"catalog": "pasillos y productos", "uses_tables": False, "item": "producto"}
    if g == "CAFETERIA":
        return {"catalog": "menu de cafeteria", "uses_tables": True, "item": "producto"}
    return {"catalog": "menu", "uses_tables": True, "item": "plato"}


def _format_categories_menu(
    comercio: TenantComercio,
    sucursal: SucursalComercio,
    categorias: List[CategoriaMenu],
    platos: List[ProductoPlato],
    state: Dict[str, Any],
) -> str:
    copy = _giro_copy(comercio.giro)
    sym = comercio.simbolo_moneda or "C$"
    cats_con_items = [c for c in categorias if any(p.categoria_id == c.id for p in platos)]

    lines = [
        f"Hola! Bienvenido a *{comercio.nombre_comercial}* ({sucursal.nombre_sucursal}).",
    ]
    if not sucursal.abierto_ahora:
        lines.append(
            f"En este momento esta sucursal esta cerrada (Horario: {sucursal.horario_apertura} - {sucursal.horario_cierre})."
        )
        lines.append("Puedes explorar nuestro catalogo o escribir *HUMANO* para dejar un mensaje.")

    lines.append(f"\nElige una categoria de nuestro {copy['catalog']} respondiendo con el numero:")
    for idx, cat in enumerate(cats_con_items, start=1):
        cnt = sum(1 for p in platos if p.categoria_id == cat.id)
        lines.append(f"*{idx}.* {cat.nombre} ({cnt})")

    if state["items"]:
        sub = sum(float(i["precio_base"]) * int(i["cantidad"]) for i in state["items"])
        qty_tot = sum(int(i["cantidad"]) for i in state["items"])
        lines.append(f"\nTu carrito actual: *{qty_tot} art.* ({sym}{sub:.2f})")
        lines.append("Escribe *VER* para ver tu carrito o *CONFIRMAR* para finalizar tu pedido.")

    lines.append("\nComandos rapidos: *MENU* | *VER* | *CONFIRMAR* | *HUMANO*")
    return "\n".join(lines)


def _format_category_products(
    comercio: TenantComercio,
    categoria: CategoriaMenu,
    platos_cat: List[ProductoPlato],
    state: Dict[str, Any],
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    lines = [f"*{categoria.nombre}* — Responde con el numero del producto (ej: *1* o *1 x2*):"]
    for idx, pl in enumerate(platos_cat, start=1):
        desc = f" — {pl.descripcion[:55]}" if pl.descripcion else ""
        lines.append(f"*{idx}.* {pl.nombre} — *{sym}{pl.precio_base:.2f}*{desc}")

    lines.append("\n*0.* Volver a categorias")
    if state["items"]:
        lines.append("Escribe *VER* para revisar tu carrito o *CONFIRMAR* para pedir.")
    return "\n".join(lines)


def _format_cart_summary(comercio: TenantComercio, state: Dict[str, Any]) -> str:
    sym = comercio.simbolo_moneda or "C$"
    items = state.get("items") or []
    if not items:
        return "Tu carrito esta vacio. Escribe *MENU* para ver las categorias disponibles."
    lines = ["*Resumen de tu carrito:*"]
    subtotal = 0.0
    for idx, it in enumerate(items, start=1):
        sub = float(it["precio_base"]) * int(it["cantidad"])
        subtotal += sub
        sin_txt = f" (sin {', '.join(it['sin_ingredientes'])})" if it.get("sin_ingredientes") else ""
        lines.append(f"{idx}. {it['cantidad']}x {it['nombre']}{sin_txt} — {sym}{sub:.2f}")
    lines.append(f"\nSubtotal: *{sym}{subtotal:.2f}*")
    lines.append("Escribe *CONFIRMAR* para continuar, *MENU* para agregar mas o *VACIAR* para reiniciar.")
    return "\n".join(lines)


def _format_delivery_options(comercio: TenantComercio, sucursal: SucursalComercio) -> str:
    copy = _giro_copy(comercio.giro)
    lines = ["Como deseas recibir tu pedido? Responde con el numero:"]
    if sucursal.permite_domicilio:
        lines.append("*1.* Envio a Domicilio")
    if sucursal.permite_recoger_local:
        lines.append("*2.* Recoger en Local / Mostrador")
    if copy["uses_tables"]:
        lines.append("*3.* Consumo en Mesa / Local")
    lines.append("\nEscribe *MENU* si deseas agregar algo mas.")
    return "\n".join(lines)


def _format_payment_options(comercio: TenantComercio) -> str:
    lines = ["Como deseas pagar tu pedido? Responde con el numero:"]
    if comercio.acepta_efectivo:
        lines.append("*1.* Efectivo (contra entrega / en caja)")
    if comercio.acepta_transferencia:
        lines.append("*2.* Transferencia Bancaria")
    return "\n".join(lines)


def _format_final_confirmation(comercio: TenantComercio, state: Dict[str, Any]) -> str:
    sym = comercio.simbolo_moneda or "C$"
    items = state.get("items") or []
    subtotal = sum(float(i["precio_base"]) * int(i["cantidad"]) for i in items)
    envio = float(state.get("costo_envio") or 0.0) if state.get("tipo_entrega") == "DOMICILIO" else 0.0
    total = round(subtotal + envio, 2)

    lines = ["*Confirma los datos de tu pedido:*"]
    for it in items:
        sub = float(it["precio_base"]) * int(it["cantidad"])
        sin_txt = f" (sin {', '.join(it['sin_ingredientes'])})" if it.get("sin_ingredientes") else ""
        lines.append(f"- {it['cantidad']}x {it['nombre']}{sin_txt}: {sym}{sub:.2f}")

    lines.append(f"\nModalidad: *{state.get('tipo_entrega')}*")
    if state.get("direccion_entrega"):
        lines.append(f"Direccion: {state['direccion_entrega']}")
    lines.append(f"Metodo de pago: *{state.get('metodo_pago')}*")
    if state.get("metodo_pago") == "TRANSFERENCIA" and comercio.datos_bancarios_texto:
        lines.append(f"Datos bancarios: {comercio.datos_bancarios_texto}")
    lines.append(f"Subtotal: {sym}{subtotal:.2f}")
    if envio > 0:
        lines.append(f"Envio: {sym}{envio:.2f}")
    lines.append(f"*TOTAL A PAGAR: {sym}{total:.2f}*")
    lines.append("\nResponde *1* (o *SI*) para confirmar tu pedido, o *2* (o *NO*) para cancelar.")
    return "\n".join(lines)


def _resolve_or_create_session(
    db: Session,
    telefono: str,
    nombre_cliente: str,
    texto: str,
    forced_sucursal_id: Optional[int] = None,
) -> Tuple[Optional[SesionChatWhatsApp], Optional[str]]:
    """Encuentra la sesión activa del cliente o crea una nueva según código [Opción B] o sucursal forzada."""
    branch_code = _extract_branch_code(texto)
    target_suc: Optional[SucursalComercio] = None

    if forced_sucursal_id:
        target_suc = (
            db.query(SucursalComercio)
            .filter(SucursalComercio.id == forced_sucursal_id)
            .first()
        )
    elif branch_code:
        # Match case-insensitive con codigo_whatsapp
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
        if not sesion:
            sesion = SesionChatWhatsApp(
                tenant_id=target_suc.tenant_id,
                sucursal_id=target_suc.id,
                telefono_cliente=telefono,
                nombre_cliente=nombre_cliente or "Cliente WhatsApp",
                modo_atencion="BOT_ACTIVO",
                estado_conversacion="EXPLORANDO_MENU",
                carrito_json=json.dumps({"items": [], "categoria_id": None}),
                como_se_realizo_resumen=f"Entrada por WhatsApp Opción B [{target_suc.codigo_whatsapp}]",
                activa=True,
            )
            db.add(sesion)
            db.flush()
        else:
            if nombre_cliente and (not sesion.nombre_cliente or sesion.nombre_cliente == "Cliente WhatsApp"):
                sesion.nombre_cliente = nombre_cliente
        return sesion, None

    # Si no vino código, buscar la última sesión activa de este teléfono
    sesion = (
        db.query(SesionChatWhatsApp)
        .filter(
            SesionChatWhatsApp.telefono_cliente == telefono,
            SesionChatWhatsApp.activa.is_(True),
        )
        .order_by(SesionChatWhatsApp.ultimo_mensaje_en.desc())
        .first()
    )
    if sesion:
        return sesion, None

    # Si es un número nuevo sin código, ofrecer las sucursales activas (máx 10)
    sucs = (
        db.query(SucursalComercio)
        .join(TenantComercio, TenantComercio.id == SucursalComercio.tenant_id)
        .filter(TenantComercio.activo.is_(True))
        .order_by(SucursalComercio.id.asc())
        .limit(10)
        .all()
    )
    if not sucs:
        return None, "En este momento no hay comercios activos disponibles."

    # Si el usuario respondió con un número de sucursal de la lista inicial
    if texto.strip().isdigit():
        idx = int(texto.strip()) - 1
        if 0 <= idx < len(sucs):
            chosen = sucs[idx]
            sesion = SesionChatWhatsApp(
                tenant_id=chosen.tenant_id,
                sucursal_id=chosen.id,
                telefono_cliente=telefono,
                nombre_cliente=nombre_cliente or "Cliente WhatsApp",
                modo_atencion="BOT_ACTIVO",
                estado_conversacion="EXPLORANDO_MENU",
                carrito_json=json.dumps({"items": [], "categoria_id": None}),
                como_se_realizo_resumen="Selección directa de comercio en WhatsApp",
                activa=True,
            )
            db.add(sesion)
            db.flush()
            return sesion, None

    lines = ["Hola! Selecciona el comercio/sucursal donde deseas ordenar respondiendo con el numero:"]
    for i, s in enumerate(sucs, start=1):
        lines.append(f"*{i}.* {s.comercio.nombre_comercial} - {s.nombre_sucursal} ([{s.codigo_whatsapp}])")
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
        line_sub = round(float(plato.precio_base) * qty, 2)
        subtotal += line_sub
        sin_txt = f" (sin {', '.join(sin_list)})" if sin_list else ""
        resumen_items.append(f"{qty}x {plato.nombre}{sin_txt}")
        detalles.append(
            DetallePedido(
                plato_id=plato.id,
                nombre_plato=plato.nombre,
                cantidad=qty,
                precio_unitario_base=plato.precio_base,
                sin_ingredientes_json=json.dumps(sin_list, ensure_ascii=False),
                extras_json="[]",
                precio_extras_unitario=0.0,
                subtotal_linea=line_sub,
                notas_plato=it.get("notas") or None,
            )
        )

    tipo_entrega = state.get("tipo_entrega") or "RECOGER_LOCAL"
    costo_envio = 0.0
    if tipo_entrega == "DOMICILIO":
        if state.get("zona_envio_id"):
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

    total = round(subtotal + costo_envio, 2)
    tel_clean = clean_whatsapp_phone(sesion.telefono_cliente) or "00000000"
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
    resumen_texto = (
        f"Pedido via WhatsApp ({'Menu Guiado' if modo_bot == 'MENU_GUIADO' else 'Hibrido IA'}): "
        f"{', '.join(resumen_items)}. Entrega: {tipo_entrega}. Pago: {metodo_pago}."
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
        estado_pedido="RECIBIDO",
        subtotal=round(subtotal, 2),
        costo_envio=round(costo_envio, 2),
        total=total,
        resumen_chat_ia=resumen_texto,
        como_se_realizo_pedido=f"Bot WhatsApp ({sucursal.nombre_sucursal}) • {modo_bot}",
        metodo_pago=metodo_pago,
        estado_pago="PENDIENTE",
        paga_con_monto=state.get("paga_con"),
    )
    db.add(pedido)
    db.flush()

    for det in detalles:
        det.pedido_id = pedido.id
        db.add(det)

    db.add(
        PagoTransaccion(
            tenant_id=comercio.id,
            pedido_id=pedido.id,
            modo_operacion="MODO_1_DIRECTO",
            metodo=metodo_pago,
            monto=total,
            moneda=comercio.moneda or "NIO",
            estado="PENDIENTE",
        )
    )

    comercio.pedidos_mes_actual = (comercio.pedidos_mes_actual or 0) + 1
    return pedido


def _parse_product_selection(text: str) -> Optional[Tuple[int, int, List[str]]]:
    """
    Parsea entradas como:
    - '1' -> (1, 1, [])
    - '2 x3' o '2x3' -> (2, 3, [])
    - '1 sin cebolla, tomate' -> (1, 1, ['cebolla', 'tomate'])
    """
    m = re.match(r"^\s*(\d+)(?:\s*[xX*]\s*(\d+))?(?:\s+sin\s+(.+))?\s*$", text, flags=re.IGNORECASE)
    if not m:
        return None
    idx = int(m.group(1))
    qty = max(1, min(50, int(m.group(2) or 1)))
    sin_raw = (m.group(3) or "").strip()
    sin_list = [s.strip() for s in re.split(r"[,y]+", sin_raw) if s.strip()] if sin_raw else []
    return idx, qty, sin_list


def process_incoming_whatsapp_message(
    db: Session,
    telefono_raw: str,
    texto_raw: str,
    nombre_cliente: str = "Cliente WhatsApp",
    forced_sucursal_id: Optional[int] = None,
    modo_bot: str = "MENU_GUIADO",  # MENU_GUIADO | HIBRIDO_IA
) -> Dict[str, Any]:
    """
    Procesa un mensaje entrante de WhatsApp (desde Evolution API, Meta Cloud API o Simulador Web).
    Retorna:
      {
        "reply_text": Optional[str],  # None si la sesión está en HUMANO_ACTIVO
        "sesion_id": Optional[int],
        "modo_atencion": "BOT_ACTIVO" | "HUMANO_ACTIVO",
        "estado_conversacion": str,
        "pedido_creado": Optional[dict],
        "quick_replies": List[str],
        "ai_used": Optional[str],
      }
    """
    telefono = clean_whatsapp_phone(telefono_raw) or "50500000000"
    texto = (texto_raw or "").strip()
    if not texto:
        return {
            "reply_text": "Escribe *MENU* para ver los productos disponibles.",
            "sesion_id": None,
            "modo_atencion": "BOT_ACTIVO",
            "estado_conversacion": "INICIO",
            "pedido_creado": None,
            "quick_replies": ["MENU"],
            "ai_used": None,
        }

    modo = (modo_bot or "MENU_GUIADO").upper().strip()
    if modo not in ("MENU_GUIADO", "HIBRIDO_IA"):
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
            "reply_text": "Comercio o sucursal no disponible en este momento.",
            "sesion_id": sesion.id,
            "modo_atencion": sesion.modo_atencion,
            "estado_conversacion": sesion.estado_conversacion,
            "pedido_creado": None,
            "quick_replies": [],
            "ai_used": None,
        }

    # Guardar mensaje entrante del cliente
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

    # Si la conversación está en modo HUMANO_ACTIVO, no responder automáticamente
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

    upper = texto.upper().strip()
    ai_used: Optional[str] = None
    pedido_creado: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = ["MENU", "VER", "CONFIRMAR"]
    reply_text = ""

    # Comandos globales disponibles en cualquier estado
    if upper in ("HUMANO", "ASESOR", "AYUDA HUMANA", "AGENTE"):
        sesion.modo_atencion = "HUMANO_ACTIVO"
        reply_text = (
            f"Listo! He transferido tu chat a un asesor de *{comercio.nombre_comercial}*. "
            "El bot quedara pausado mientras te atienden personalmente."
        )
        quick_replies = []
    elif upper in ("VACIAR", "CANCELAR", "REINICIAR"):
        state = {
            "items": [],
            "categoria_id": None,
            "tipo_entrega": None,
            "direccion_entrega": None,
            "zona_envio_id": None,
            "zona_nombre": None,
            "costo_envio": 0.0,
            "metodo_pago": None,
            "paga_con": None,
        }
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = "Carrito reiniciado.\n\n" + _format_categories_menu(
            comercio, sucursal, categorias, platos, state
        )
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
    elif upper in ("MENU", "INICIO", "HOLA", "CATALOGO") or _extract_branch_code(texto):
        state["categoria_id"] = None
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = _format_categories_menu(comercio, sucursal, categorias, platos, state)
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
        if state["items"]:
            quick_replies.extend(["VER", "CONFIRMAR"])
    elif upper in ("VER", "CARRITO", "RESUMEN"):
        reply_text = _format_cart_summary(comercio, state)
        quick_replies = ["CONFIRMAR", "MENU", "VACIAR"]
    elif upper in ("CONFIRMAR", "LISTO", "PAGAR", "FINALIZAR") and sesion.estado_conversacion == "EXPLORANDO_MENU":
        if not state["items"]:
            reply_text = "Aun no tienes productos en tu carrito. Elige una categoria primero:\n\n" + _format_categories_menu(
                comercio, sucursal, categorias, platos, state
            )
            quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
        else:
            sesion.estado_conversacion = "PIDIENDO_MODALIDAD"
            reply_text = _format_delivery_options(comercio, sucursal)
            quick_replies = ["1", "2"] + (["3"] if _giro_copy(comercio.giro)["uses_tables"] else [])
    else:
        est = sesion.estado_conversacion

        # -----------------------------------------------------------------
        # ESTADO 1: EXPLORANDO_MENU (Categorías / Productos / IA opcional)
        # -----------------------------------------------------------------
        if est == "EXPLORANDO_MENU":
            sel = _parse_product_selection(texto)
            if state.get("categoria_id") is None:
                # Esperando número de categoría
                if sel and 1 <= sel[0] <= len(cats_con_items):
                    cat_obj = cats_con_items[sel[0] - 1]
                    state["categoria_id"] = cat_obj.id
                    platos_cat = [p for p in platos if p.categoria_id == cat_obj.id]
                    reply_text = _format_category_products(comercio, cat_obj, platos_cat, state)
                    quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0", "CONFIRMAR"]
                elif modo == "HIBRIDO_IA":
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
                            reply_text = f"Transfiriendo tu consulta a un asesor de *{comercio.nombre_comercial}*."
                            quick_replies = []
                        elif acc == "CONFIRMAR" and state["items"]:
                            sesion.estado_conversacion = "PIDIENDO_MODALIDAD"
                            reply_text = _format_delivery_options(comercio, sucursal)
                            quick_replies = ["1", "2"]
                        elif acc == "AGREGAR" and ai_res.get("items"):
                            agregados_txt = []
                            plato_map = {p.id: p for p in platos}
                            for item_ai in ai_res["items"]:
                                pid = int(item_ai.get("plato_id") or 0)
                                pl = plato_map.get(pid)
                                if not pl:
                                    continue
                                qty = max(1, min(50, int(item_ai.get("cantidad") or 1)))
                                sin_ing = item_ai.get("sin_ingredientes") or []
                                state["items"].append(
                                    {
                                        "plato_id": pl.id,
                                        "nombre": pl.nombre,
                                        "precio_base": float(pl.precio_base),
                                        "cantidad": qty,
                                        "sin_ingredientes": sin_ing,
                                        "notas": item_ai.get("notas") or "",
                                    }
                                )
                                agregados_txt.append(f"{qty}x {pl.nombre}")
                            if agregados_txt:
                                reply_text = (
                                    f"Agregado al carrito: *{', '.join(agregados_txt)}*.\n\n"
                                    + _format_cart_summary(comercio, state)
                                )
                                quick_replies = ["CONFIRMAR", "MENU", "VER"]
                            else:
                                reply_text = "No encontre ese producto disponible.\n\n" + _format_categories_menu(
                                    comercio, sucursal, categorias, platos, state
                                )
                        elif acc == "CONSULTA" and ai_res.get("respuesta_breve"):
                            reply_text = ai_res["respuesta_breve"] + "\n\nEscribe *MENU* para ver las opciones."
                            quick_replies = ["MENU", "VER", "CONFIRMAR"]
                        else:
                            reply_text = "No entendi tu seleccion. Elige un numero de categoria:\n\n" + _format_categories_menu(
                                comercio, sucursal, categorias, platos, state
                            )
                    else:
                        reply_text = "Por favor responde con el numero de una categoria:\n\n" + _format_categories_menu(
                            comercio, sucursal, categorias, platos, state
                        )
                        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
                else:
                    reply_text = "Por favor responde con el numero de una categoria valida:\n\n" + _format_categories_menu(
                        comercio, sucursal, categorias, platos, state
                    )
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            else:
                # Dentro de una categoría específica
                cat_obj = next((c for c in cats_con_items if c.id == state["categoria_id"]), None)
                platos_cat = [p for p in platos if p.categoria_id == state["categoria_id"]]
                if sel and sel[0] == 0:
                    state["categoria_id"] = None
                    reply_text = _format_categories_menu(comercio, sucursal, categorias, platos, state)
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)] + ["CONFIRMAR"]
                elif sel and 1 <= sel[0] <= len(platos_cat):
                    pl = platos_cat[sel[0] - 1]
                    qty = sel[1]
                    sin_list = sel[2]
                    state["items"].append(
                        {
                            "plato_id": pl.id,
                            "nombre": pl.nombre,
                            "precio_base": float(pl.precio_base),
                            "cantidad": qty,
                            "sin_ingredientes": sin_list,
                            "notas": "",
                        }
                    )
                    sym = comercio.simbolo_moneda or "C$"
                    sin_txt = f" (sin {', '.join(sin_list)})" if sin_list else ""
                    reply_text = (
                        f"Agregado: *{qty}x {pl.nombre}{sin_txt}* ({sym}{pl.precio_base * qty:.2f}).\n"
                        "Puedes elegir otro numero de esta categoria, *0* para volver a categorias, *VER* o *CONFIRMAR*."
                    )
                    quick_replies = ["CONFIRMAR", "VER", "0"]
                else:
                    if cat_obj:
                        reply_text = "Elige el numero del producto (ej: *1* o *1 x2*) o *0* para volver:\n\n" + _format_category_products(
                            comercio, cat_obj, platos_cat, state
                        )
                        quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0", "CONFIRMAR"]
                    else:
                        state["categoria_id"] = None
                        reply_text = _format_categories_menu(comercio, sucursal, categorias, platos, state)

        # -----------------------------------------------------------------
        # ESTADO 2: PIDIENDO_MODALIDAD (Domicilio / Recoger / Mesa)
        # -----------------------------------------------------------------
        elif est == "PIDIENDO_MODALIDAD":
            if upper in ("1", "DOMICILIO", "ENVIO") and sucursal.permite_domicilio:
                state["tipo_entrega"] = "DOMICILIO"
                sesion.estado_conversacion = "PIDIENDO_DIRECCION"
                zonas = [z for z in sucursal.zonas_envio if z.activa]
                sym = comercio.simbolo_moneda or "C$"
                cli_prev = (
                    db.query(ClienteFrecuente)
                    .filter(
                        ClienteFrecuente.tenant_id == comercio.id,
                        ClienteFrecuente.telefono_whatsapp == telefono,
                    )
                    .first()
                )
                lines = ["Escribe tu *direccion de entrega* completa (calle, numero y referencia)."]
                if cli_prev and cli_prev.ultima_direccion:
                    lines.append(f"O responde *USAR* para enviar a tu ultima direccion: _{cli_prev.ultima_direccion}_")
                if zonas:
                    lines.append("\nZonas de envio disponibles:")
                    for idx, z in enumerate(zonas, start=1):
                        lines.append(f"- {z.nombre_zona} (+{sym}{z.costo_envio:.2f})")
                else:
                    lines.append(f"Tarifa base de envio: {sym}{sucursal.costo_envio_base:.2f}")
                reply_text = "\n".join(lines)
                quick_replies = ["USAR"] if (cli_prev and cli_prev.ultima_direccion) else []
            elif upper in ("2", "RECOGER", "LOCAL", "MOSTRADOR") and sucursal.permite_recoger_local:
                state["tipo_entrega"] = "RECOGER_LOCAL"
                state["direccion_entrega"] = f"Recoger en {sucursal.nombre_sucursal}"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text = _format_payment_options(comercio)
                quick_replies = ["1", "2"]
            elif upper in ("3", "MESA") and _giro_copy(comercio.giro)["uses_tables"]:
                state["tipo_entrega"] = "MESA"
                state["direccion_entrega"] = f"Consumo en mesa ({sucursal.nombre_sucursal})"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text = _format_payment_options(comercio)
                quick_replies = ["1", "2"]
            else:
                reply_text = "Opcion no valida.\n\n" + _format_delivery_options(comercio, sucursal)
                quick_replies = ["1", "2"]

        # -----------------------------------------------------------------
        # ESTADO 3: PIDIENDO_DIRECCION (solo si eligió Domicilio)
        # -----------------------------------------------------------------
        elif est == "PIDIENDO_DIRECCION":
            direccion = texto
            if upper == "USAR":
                cli_prev = (
                    db.query(ClienteFrecuente)
                    .filter(
                        ClienteFrecuente.tenant_id == comercio.id,
                        ClienteFrecuente.telefono_whatsapp == telefono,
                    )
                    .first()
                )
                if cli_prev and cli_prev.ultima_direccion:
                    direccion = cli_prev.ultima_direccion
            state["direccion_entrega"] = direccion
            # Detectar si la dirección menciona una zona configurada
            zonas = [z for z in sucursal.zonas_envio if z.activa]
            matched_z = next(
                (z for z in zonas if z.nombre_zona.lower() in direccion.lower()), None
            )
            if matched_z:
                state["zona_envio_id"] = matched_z.id
                state["zona_nombre"] = matched_z.nombre_zona
                state["costo_envio"] = float(matched_z.costo_envio)
            else:
                state["zona_envio_id"] = None
                state["zona_nombre"] = None
                state["costo_envio"] = float(sucursal.costo_envio_base or 0.0)

            sesion.estado_conversacion = "PIDIENDO_PAGO"
            reply_text = _format_payment_options(comercio)
            quick_replies = ["1", "2"]

        # -----------------------------------------------------------------
        # ESTADO 4: PIDIENDO_PAGO
        # -----------------------------------------------------------------
        elif est == "PIDIENDO_PAGO":
            if upper in ("1", "EFECTIVO", "CASH") and comercio.acepta_efectivo:
                state["metodo_pago"] = "EFECTIVO"
                sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2"]
            elif upper in ("2", "TRANSFERENCIA", "BANCO") and comercio.acepta_transferencia:
                state["metodo_pago"] = "TRANSFERENCIA"
                sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2"]
            else:
                reply_text = "Por favor elige un metodo de pago valido:\n\n" + _format_payment_options(comercio)
                quick_replies = ["1", "2"]

        # -----------------------------------------------------------------
        # ESTADO 5: CONFIRMANDO_CARRITO -> Crear Pedido Real en Kanban
        # -----------------------------------------------------------------
        elif est == "CONFIRMANDO_CARRITO":
            if upper in ("1", "SI", "SÍ", "CONFIRMAR", "OK"):
                pedido = _create_order_from_session(db, sesion, comercio, sucursal, state, modo)
                sym = comercio.simbolo_moneda or "C$"
                pedido_creado = {
                    "id": pedido.id,
                    "folio": pedido.folio,
                    "total": pedido.total,
                    "estado_pedido": pedido.estado_pedido,
                }
                # Limpiar carrito y volver a EXPLORANDO_MENU para futuras compras
                state = {
                    "items": [],
                    "categoria_id": None,
                    "tipo_entrega": None,
                    "direccion_entrega": None,
                    "zona_envio_id": None,
                    "zona_nombre": None,
                    "costo_envio": 0.0,
                    "metodo_pago": None,
                    "paga_con": None,
                }
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = (
                    f"Pedido confirmado! Folio: *#{pedido.folio}*\n"
                    f"Total: *{sym}{pedido.total:.2f}* ({pedido.metodo_pago})\n"
                    f"Tiempo estimado: *{sucursal.tiempo_estimado_entrega_min} min*.\n"
                    "Te avisaremos por este chat en cuanto avance tu pedido."
                )
                quick_replies = ["MENU"]
            elif upper in ("2", "NO", "CANCELAR"):
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                state["categoria_id"] = None
                reply_text = "Pedido pausado. Puedes editar tu carrito, escribir *VACIAR* o *MENU*."
                quick_replies = ["MENU", "VER", "VACIAR"]
            else:
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2"]
        else:
            sesion.estado_conversacion = "EXPLORANDO_MENU"
            state["categoria_id"] = None
            reply_text = _format_categories_menu(comercio, sucursal, categorias, platos, state)
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

    return {
        "reply_text": reply_text,
        "sesion_id": sesion.id,
        "modo_atencion": sesion.modo_atencion,
        "estado_conversacion": sesion.estado_conversacion,
        "pedido_creado": pedido_creado,
        "quick_replies": quick_replies,
        "ai_used": ai_used,
    }

