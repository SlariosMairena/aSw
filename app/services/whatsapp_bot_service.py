"""
Motor Conversacional Natural y Paso a Paso del Bot de WhatsApp.
- Mensajes cortos, cálidos y fáciles de leer en celular (una sola pregunta por mensaje).
- Flujo guiado con confirmación en cada etapa clave:
  Categoría -> Producto -> Cantidad -> (Personalización si aplica) -> "¿Agregar algo más?"
  -> Modalidad -> (Zona y Dirección si es Domicilio) -> Pago -> (Vuelto si es Efectivo)
  -> Confirmación Final -> Pedido real en el Kanban.
- Soporta Modo Menú Guiado (costo cero) y Modo Híbrido con IA (si hay API Key configurada).
- Respeta productos agotados globalmente o por sucursal (StockSucursal) y modo HUMANO_ACTIVO.
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
    IngredienteOpcionPlato,
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
    if not text:
        return None
    m = re.search(r"\[([^\]]{2,50})\]", text)
    return m.group(1).strip() if m else None


def _empty_state() -> Dict[str, Any]:
    return {
        "items": [],  # [{plato_id, nombre, precio_base, cantidad, sin_ingredientes, extras, precio_extras, notas}]
        "categoria_id": None,
        "pending_item": None,  # {plato_id, nombre, precio_base, cantidad}
        "tipo_entrega": None,  # DOMICILIO | RECOGER_LOCAL | MESA
        "direccion_entrega": None,
        "zona_envio_id": None,
        "zona_nombre": None,
        "costo_envio": 0.0,
        "metodo_pago": None,  # EFECTIVO | TRANSFERENCIA
        "paga_con": None,
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
    primer = nombre.strip().split()[0]
    return f"¡Hola {primer}!"


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
        lines.append("\n¿Qué te gustaría pedir hoy? Responde con el número:")

    for idx, cat in enumerate(cats, start=1):
        lines.append(f"*{idx}.* {cat.nombre}")

    if state["items"]:
        sym = comercio.simbolo_moneda or "C$"
        qty = sum(int(i["cantidad"]) for i in state["items"])
        lines.append(f"\nLlevas *{qty} art.* ({sym}{_calc_subtotal(state['items']):.2f}). Escribe *Listo* cuando quieras finalizar.")
    elif not is_returning:
        lines.append("\n_Si prefieres hablar con una persona, escribe *asesor*._")

    return "\n".join(lines)


def _format_products_list(
    comercio: TenantComercio,
    categoria: CategoriaMenu,
    platos_cat: List[ProductoPlato],
) -> str:
    sym = comercio.simbolo_moneda or "C$"
    lines = [f"*{categoria.nombre}*", "¿Cuál te gustaría? Responde con el número:\n"]
    for idx, pl in enumerate(platos_cat, start=1):
        lines.append(f"*{idx}.* {pl.nombre} — *{sym}{pl.precio_base:.2f}*")
    lines.append("\n*0.* Volver atrás")
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
    lines = ["¿En qué zona te encuentras? Responde con el número:\n"]
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
        lines.append(f"\n_Si deseas quitar algo ({nombres}), escribe por ejemplo: *sin {removibles[0].nombre.lower()}*._")
    return "\n".join(lines), qr


def _resolve_or_create_session(
    db: Session,
    telefono: str,
    nombre_cliente: str,
    texto: str,
    forced_sucursal_id: Optional[int] = None,
) -> Tuple[Optional[SesionChatWhatsApp], Optional[str]]:
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
        estado_pedido="RECIBIDO",
        subtotal=round(subtotal, 2),
        costo_envio=round(costo_envio, 2),
        total=total,
        resumen_chat_ia=resumen_texto,
        como_se_realizo_pedido=f"Bot WhatsApp ({sucursal.nombre_sucursal}) • {modo_bot}",
        metodo_pago=metodo_pago,
        estado_pago="PENDIENTE",
        paga_con_monto=paga_con,
        cambio_a_entregar=cambio,
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
            paga_con=paga_con,
            cambio=cambio,
        )
    )

    comercio.pedidos_mes_actual = (comercio.pedidos_mes_actual or 0) + 1
    return pedido


def _extract_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


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

    upper = texto.upper().strip()
    ai_used: Optional[str] = None
    pedido_creado: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = ["1", "2"]
    reply_text = ""

    # Comandos globales naturales
    if upper in ("HUMANO", "ASESOR", "AYUDA", "AGENTE", "HABLAR CON ALGUIEN"):
        sesion.modo_atencion = "HUMANO_ACTIVO"
        reply_text = (
            f"¡Con gusto! Te comunico ahora mismo con alguien de *{comercio.nombre_comercial}*. "
            "En un momento te responden por aquí."
        )
        quick_replies = []
    elif upper in ("VACIAR", "REINICIAR"):
        state = _empty_state()
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = "Listo, vaciamos tu carrito.\n\n" + _format_welcome_categories(
            comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
        )
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
    elif (
        upper in ("MENU", "MENÚ", "INICIO", "HOLA", "BUENAS", "BUENOS DIAS", "BUENAS TARDES", "BUENAS NOCHES", "CATALOGO", "CATÁLOGO")
        or _extract_branch_code(texto)
    ):
        state["categoria_id"] = None
        state["pending_item"] = None
        sesion.estado_conversacion = "EXPLORANDO_MENU"
        reply_text = _format_welcome_categories(
            comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=False
        )
        quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
    elif upper in ("VER", "CARRITO", "MI PEDIDO"):
        sesion.estado_conversacion = "PREGUNTANDO_MAS" if state["items"] else "EXPLORANDO_MENU"
        reply_text = _format_cart_view(comercio, state)
        quick_replies = ["1", "2", "3"] if state["items"] else ["Menú"]
    elif upper in ("LISTO", "CONFIRMAR", "FINALIZAR", "PAGAR") and sesion.estado_conversacion in ("EXPLORANDO_MENU", "ELIGIENDO_PRODUCTO", "PREGUNTANDO_MAS"):
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

        # 1. EXPLORANDO_MENU -> Elegir categoría (o lenguaje natural con IA si está activo)
        if est == "EXPLORANDO_MENU":
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None
            if num and 1 <= num <= len(cats_con_items):
                cat_obj = cats_con_items[num - 1]
                state["categoria_id"] = cat_obj.id
                sesion.estado_conversacion = "ELIGIENDO_PRODUCTO"
                platos_cat = [p for p in platos if p.categoria_id == cat_obj.id]
                reply_text = _format_products_list(comercio, cat_obj, platos_cat)
                quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0"]
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
                            reply_text = "No encontré ese producto disponible hoy.\n\n" + _format_welcome_categories(
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
                    reply_text = "Dime el número de la categoría que te gustaría ver:\n\n" + _format_welcome_categories(
                        comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                    )
                    quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            else:
                reply_text = "Responde con el número de la categoría que deseas ver:\n\n" + _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]

        # 2. ELIGIENDO_PRODUCTO -> Seleccionar producto de la categoría
        elif est == "ELIGIENDO_PRODUCTO":
            cat_obj = next((c for c in cats_con_items if c.id == state.get("categoria_id")), None)
            platos_cat = [p for p in platos if p.categoria_id == state.get("categoria_id")]
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None
            if num == 0 or not cat_obj:
                state["categoria_id"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif num and 1 <= num <= len(platos_cat):
                pl = platos_cat[num - 1]
                sym = comercio.simbolo_moneda or "C$"
                state["pending_item"] = {
                    "plato_id": pl.id,
                    "nombre": pl.nombre,
                    "precio_base": float(pl.precio_base),
                    "cantidad": 1,
                }
                sesion.estado_conversacion = "PIDIENDO_CANTIDAD"
                reply_text = f"¡Buena elección! *{pl.nombre}* ({sym}{pl.precio_base:.2f}).\n\n¿Cuántas unidades te gustaría llevar?"
                quick_replies = ["1", "2", "3"]
            else:
                reply_text = _format_products_list(comercio, cat_obj, platos_cat)
                quick_replies = [str(i) for i in range(1, min(len(platos_cat), 4) + 1)] + ["0"]

        # 3. PIDIENDO_CANTIDAD -> Cantidad y verificar si ofrece personalización
        elif est == "PIDIENDO_CANTIDAD":
            pending = state.get("pending_item")
            qty_num = _extract_number(texto)
            if not pending:
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
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
                reply_text = "Por favor indícame solo el número de unidades (por ejemplo: *1*, *2* o *3*)."
                quick_replies = ["1", "2", "3"]

        # 4. PERSONALIZANDO_ITEM -> Tal cual / Extra / Sin ingrediente
        elif est == "PERSONALIZANDO_ITEM":
            pending = state.get("pending_item")
            if not pending:
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
            else:
                removibles, extras = _get_item_customization_options(db, pending["plato_id"])
                extras_sel: List[Dict[str, Any]] = []
                sin_sel: List[str] = []
                if upper in ("1", "TAL CUAL", "NORMAL", "ASI", "ASÍ", "NO"):
                    pass
                elif texto.strip().isdigit() and 2 <= int(texto.strip()) <= len(extras[:4]) + 1:
                    ex = extras[int(texto.strip()) - 2]
                    extras_sel.append({"nombre": ex.nombre, "precio": float(ex.precio_adicional)})
                elif "SIN " in upper:
                    raw_sin = re.split(r"sin\s+", texto, maxsplit=1, flags=re.IGNORECASE)[1]
                    sin_sel = [s.strip() for s in re.split(r"[,y]+", raw_sin) if s.strip()]
                else:
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
                extra_txt = f" (+{extras_sel[0]['nombre']})" if extras_sel else (f" (sin {', '.join(sin_sel)})" if sin_sel else "")
                label = f"{pending['cantidad']}x {pending['nombre']}{extra_txt}"
                state["pending_item"] = None
                sesion.estado_conversacion = "PREGUNTANDO_MAS"
                reply_text = _format_ask_more(comercio, state, label)
                quick_replies = ["1", "2", "3"]

        # 5. PREGUNTANDO_MAS -> ¿Agregar otro producto o continuar al checkout?
        elif est == "PREGUNTANDO_MAS":
            if upper in ("1", "SI", "SÍ", "MAS", "MÁS", "AGREGAR"):
                state["categoria_id"] = None
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = _format_welcome_categories(
                    comercio, sucursal, categorias, platos, state, sesion.nombre_cliente, is_returning=True
                )
                quick_replies = [str(i) for i in range(1, min(len(cats_con_items), 4) + 1)]
            elif upper in ("2", "NO", "CONTINUAR", "PEDIR", "FINALIZAR"):
                sesion.estado_conversacion = "PIDIENDO_MODALIDAD"
                reply_text, quick_replies = _format_delivery_options(comercio, sucursal)
            elif upper in ("3", "VER", "CARRITO"):
                reply_text = _format_cart_view(comercio, state)
                quick_replies = ["1", "2", "3"]
            else:
                reply_text = "Responde con el número:\n*1.* Agregar otro producto\n*2.* Continuar con mi pedido\n*3.* Ver mi carrito"
                quick_replies = ["1", "2", "3"]

        # 6. PIDIENDO_MODALIDAD
        elif est == "PIDIENDO_MODALIDAD":
            if upper in ("1", "DOMICILIO", "ENVIO", "ENVÍO") and sucursal.permite_domicilio:
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
            elif upper in ("2", "RECOGER", "LOCAL", "MOSTRADOR") and sucursal.permite_recoger_local:
                state["tipo_entrega"] = "RECOGER_LOCAL"
                state["direccion_entrega"] = f"Recoger en {sucursal.nombre_sucursal}"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text, quick_replies = _format_payment_options(comercio)
            elif upper in ("3", "MESA") and _giro_copy(comercio.giro)["uses_tables"]:
                state["tipo_entrega"] = "MESA"
                state["direccion_entrega"] = f"Consumo en mesa ({sucursal.nombre_sucursal})"
                state["costo_envio"] = 0.0
                sesion.estado_conversacion = "PIDIENDO_PAGO"
                reply_text, quick_replies = _format_payment_options(comercio)
            else:
                reply_text, quick_replies = _format_delivery_options(comercio, sucursal)

        # 7. PIDIENDO_ZONA
        elif est == "PIDIENDO_ZONA":
            zonas = [z for z in sucursal.zonas_envio if z.activa]
            num = int(_extract_number(texto)) if texto.strip().isdigit() else None
            if num and 1 <= num <= len(zonas):
                z = zonas[num - 1]
                state["zona_envio_id"] = z.id
                state["zona_nombre"] = z.nombre_zona
                state["costo_envio"] = float(z.costo_envio)
            elif num and num == len(zonas) + 1:
                state["zona_envio_id"] = None
                state["zona_nombre"] = None
                state["costo_envio"] = float(sucursal.costo_envio_base or 0.0)
            else:
                reply_text, quick_replies = _format_zones_options(comercio, sucursal, zonas)
                num = None

            if num is not None:
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

        # 8. PIDIENDO_DIRECCION
        elif est == "PIDIENDO_DIRECCION":
            direccion = texto
            if upper in ("1", "SI", "SÍ", "USAR", "MISMA"):
                cli_prev = (
                    db.query(ClienteFrecuente)
                    .filter(ClienteFrecuente.tenant_id == comercio.id, ClienteFrecuente.telefono_whatsapp == telefono)
                    .first()
                )
                if cli_prev and cli_prev.ultima_direccion:
                    direccion = cli_prev.ultima_direccion
            state["direccion_entrega"] = direccion
            sesion.estado_conversacion = "PIDIENDO_PAGO"
            reply_text, quick_replies = _format_payment_options(comercio)

        # 9. PIDIENDO_PAGO
        elif est == "PIDIENDO_PAGO":
            if upper in ("1", "EFECTIVO", "CASH") and comercio.acepta_efectivo:
                state["metodo_pago"] = "EFECTIVO"
                sesion.estado_conversacion = "PIDIENDO_CAMBIO"
                sym = comercio.simbolo_moneda or "C$"
                sub = _calc_subtotal(state["items"])
                env = float(state.get("costo_envio") or 0.0) if state.get("tipo_entrega") == "DOMICILIO" else 0.0
                reply_text = f"Tu total es *{sym}{sub + env:.2f}*.\n¿Con cuánto vas a pagar para llevarte vuelto?\n\nEscribe el monto (ej: *500*) o responde *0* si pagas exacto."
                quick_replies = ["0", "500", "1000"]
            elif upper in ("2", "TRANSFERENCIA", "BANCO") and comercio.acepta_transferencia:
                state["metodo_pago"] = "TRANSFERENCIA"
                state["paga_con"] = None
                sesion.estado_conversacion = "CONFIRMANDO_CARRITO"
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2", "3"]
            else:
                reply_text, quick_replies = _format_payment_options(comercio)

        # 10. PIDIENDO_CAMBIO
        elif est == "PIDIENDO_CAMBIO":
            if upper in ("0", "EXACTO", "PAGO EXACTO"):
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

        # 11. CONFIRMANDO_CARRITO -> Confirmación final obligatoria
        elif est == "CONFIRMANDO_CARRITO":
            if upper in ("1", "SI", "SÍ", "CONFIRMAR", "OK"):
                pedido = _create_order_from_session(db, sesion, comercio, sucursal, state, modo)
                sym = comercio.simbolo_moneda or "C$"
                copy = _giro_copy(comercio.giro)
                pedido_creado = {
                    "id": pedido.id,
                    "folio": pedido.folio,
                    "total": pedido.total,
                    "estado_pedido": pedido.estado_pedido,
                }
                state = _empty_state()
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = (
                    f"¡Listo! Tu pedido *#{pedido.folio}* ya fue enviado a {copy['prep_word']}.\n"
                    f"Total: *{sym}{pedido.total:.2f}* · Tiempo estimado: *{sucursal.tiempo_estimado_entrega_min} min*.\n\n"
                    "Te avisaremos por aquí en cuanto vaya avanzando. ¡Gracias por tu preferencia!"
                )
                quick_replies = ["Menú"]
            elif upper in ("2", "CAMBIAR", "EDITAR", "AGREGAR"):
                sesion.estado_conversacion = "PREGUNTANDO_MAS"
                reply_text = _format_cart_view(comercio, state)
                quick_replies = ["1", "2", "3"]
            elif upper in ("3", "NO", "CANCELAR"):
                state = _empty_state()
                sesion.estado_conversacion = "EXPLORANDO_MENU"
                reply_text = "Pedido cancelado sin problema. Cuando gustes ordenar de nuevo, solo escribe *menú*."
                quick_replies = ["Menú"]
            else:
                reply_text = _format_final_confirmation(comercio, state)
                quick_replies = ["1", "2", "3"]
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

    return {
        "reply_text": reply_text,
        "sesion_id": sesion.id,
        "modo_atencion": sesion.modo_atencion,
        "estado_conversacion": sesion.estado_conversacion,
        "pedido_creado": pedido_creado,
        "quick_replies": quick_replies,
        "ai_used": ai_used,
    }
