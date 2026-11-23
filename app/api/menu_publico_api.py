"""
Mini-Menú Web Interactivo Público por Sucursal (/m/{codigo_whatsapp}):
- Permite que el cliente seleccione visualmente categorías, productos, cantidades,
  exclusiones de ingredientes, extras, modalidad, zona de envío y método de pago sin escribir.
- Calcula todos los precios exclusivamente desde la base de datos.
- Crea el Pedido real en el Kanban, vincula la SesionChatWhatsApp en SIGUIENDO_PEDIDO
  y envía confirmación inmediata al WhatsApp real del cliente.
"""
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.demo_seeds import normalize_giro_code
from app.core.realtime import publish_tenant_event
from app.models.saas_models import (
    CategoriaMenu,
    ClienteFrecuente,
    ComercioLogo,
    DetallePedido,
    IngredienteOpcionPlato,
    MensajeChatWhatsApp,
    PagoTransaccion,
    Pedido,
    ProductoImagen,
    ProductoPlato,
    SesionChatWhatsApp,
    StockSucursal,
    SucursalComercio,
    TenantComercio,
    ZonaEnvioSucursal,
)
from app.services.fidelidad_service import (
    calcular_descuento_cliente,
    consumir_premio_sellos,
    get_cliente_beneficios,
)
from app.services.payment_service import simulated_gateway
from app.services.whatsapp_provider import clean_whatsapp_phone, send_whatsapp_text

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["Mini-Menú Web Interactivo"])


class PublicOrderItemInput(BaseModel):
    plato_id: int
    cantidad: int = 1
    sin_ingredientes: List[str] = []
    extras_ids: List[int] = []
    notas_plato: Optional[str] = None


class PublicOrderCreateRequest(BaseModel):
    nombre_cliente: str
    telefono_cliente: str
    tipo_entrega: str = "DOMICILIO"  # DOMICILIO | RECOGER_LOCAL | MESA
    zona_envio_id: Optional[int] = None
    direccion_entrega: Optional[str] = None
    mesa_nombre: Optional[str] = None
    metodo_pago: str = "EFECTIVO"  # EFECTIVO | TRANSFERENCIA
    paga_con_monto: Optional[float] = None
    notas_especiales: Optional[str] = None
    items: List[PublicOrderItemInput]


def _find_branch_by_code(db: Session, codigo_whatsapp: str) -> Optional[SucursalComercio]:
    code_clean = (codigo_whatsapp or "").strip().lower()
    if not code_clean:
        return None
    for s in db.query(SucursalComercio).all():
        if (s.codigo_whatsapp or "").strip().lower() == code_clean:
            return s
    return None


@router.get("/m/{codigo_whatsapp}", response_class=HTMLResponse)
def ver_menu_publico_html(
    codigo_whatsapp: str,
    request: Request,
    tel: Optional[str] = None,
    nombre: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Renderiza el Mini-Menú Web Interactivo mobile-first de la sucursal."""
    suc = _find_branch_by_code(db, codigo_whatsapp)
    if not suc or not suc.comercio or not suc.comercio.activo:
        raise HTTPException(status_code=404, detail="Menú o sucursal no disponible.")

    return templates.TemplateResponse(
        request,
        "menu_publico.html",
        {
            "codigo_whatsapp": suc.codigo_whatsapp,
            "comercio_nombre": suc.comercio.nombre_comercial,
            "sucursal_nombre": suc.nombre_sucursal,
            "prefill_tel": clean_whatsapp_phone(tel or ""),
            "prefill_nombre": (nombre or "").strip(),
        },
    )


@router.get("/api/v1/public/logo/{tenant_id}")
def obtener_logo_comercio_publico(tenant_id: int, db: Session = Depends(get_db)):
    """Sirve el logo WebP comprimido del comercio con caché agresiva de 7 días."""
    logo = (
        db.query(ComercioLogo)
        .filter(ComercioLogo.tenant_id == tenant_id)
        .first()
    )
    if not logo or not logo.imagen_bytes:
        raise HTTPException(status_code=404, detail="Logo no encontrado.")
    return Response(
        content=logo.imagen_bytes,
        media_type=logo.mime_type or "image/webp",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/api/v1/public/img/{plato_id}")
def obtener_imagen_producto_publica(plato_id: int, db: Session = Depends(get_db)):
    """Sirve la miniatura WebP comprimida del producto con caché agresiva de 7 días."""
    img = (
        db.query(ProductoImagen)
        .filter(ProductoImagen.plato_id == plato_id)
        .first()
    )
    if not img or not img.imagen_bytes:
        raise HTTPException(status_code=404, detail="Imagen no encontrada.")
    return Response(
        content=img.imagen_bytes,
        media_type=img.mime_type or "image/webp",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/api/v1/public/menu/{codigo_whatsapp}")
def obtener_menu_publico_json(
    codigo_whatsapp: str,
    tel: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Devuelve catálogo, ingredientes, extras y zonas de envío de la sucursal."""
    suc = _find_branch_by_code(db, codigo_whatsapp)
    if not suc or not suc.comercio or not suc.comercio.activo:
        raise HTTPException(status_code=404, detail="Menú o sucursal no disponible.")

    comercio = suc.comercio
    giro_code = normalize_giro_code(comercio.giro)

    agotados_ids = {
        r.plato_id
        for r in db.query(StockSucursal)
        .filter(
            StockSucursal.tenant_id == comercio.id,
            StockSucursal.sucursal_id == suc.id,
            StockSucursal.agotado_hoy.is_(True),
        )
        .all()
    }

    categorias = (
        db.query(CategoriaMenu)
        .filter(CategoriaMenu.tenant_id == comercio.id, CategoriaMenu.activa.is_(True))
        .order_by(CategoriaMenu.orden.asc(), CategoriaMenu.id.asc())
        .all()
    )
    platos = (
        db.query(ProductoPlato)
        .filter(
            ProductoPlato.tenant_id == comercio.id,
            ProductoPlato.disponible_global.is_(True),
        )
        .order_by(ProductoPlato.categoria_id.asc(), ProductoPlato.id.asc())
        .all()
    )

    img_rows = (
        db.query(ProductoImagen.plato_id, ProductoImagen.actualizado_en)
        .filter(ProductoImagen.tenant_id == comercio.id)
        .all()
    )
    img_map = {
        pid: f"/api/v1/public/img/{pid}?v={int(ts.timestamp()) if ts else 1}"
        for pid, ts in img_rows
    }

    platos_data = []
    for p in platos:
        if p.id in agotados_ids:
            continue
        removibles = [
            {"id": ing.id, "nombre": ing.nombre}
            for ing in p.ingredientes_opciones
            if ing.tipo == "INGREDIENTE_BASE" and ing.disponible_global
        ]
        extras = [
            {"id": ing.id, "nombre": ing.nombre, "precio_adicional": float(ing.precio_adicional or 0.0)}
            for ing in p.ingredientes_opciones
            if ing.tipo in ("EXTRA", "VARIANTE") and ing.disponible_global
        ]
        platos_data.append(
            {
                "id": p.id,
                "categoria_id": p.categoria_id,
                "categoria": p.categoria.nombre if p.categoria else "General",
                "nombre": p.nombre,
                "descripcion": p.descripcion or "",
                "precio_base": float(p.precio_base),
                "imagen_url": img_map.get(p.id),
                "ingredientes_removibles": removibles,
                "extras": extras,
            }
        )

    cats_data = [
        {"id": c.id, "nombre": c.nombre}
        for c in categorias
        if any(pl["categoria_id"] == c.id for pl in platos_data)
    ]

    ultima_direccion = ""
    tel_clean = clean_whatsapp_phone(tel or "")
    beneficios_cli = get_cliente_beneficios(db, comercio.id, tel_clean)
    cliente_envio_gratis = bool(beneficios_cli.get("envio_gratis"))
    if tel_clean:
        cli = (
            db.query(ClienteFrecuente)
            .filter(
                ClienteFrecuente.tenant_id == comercio.id,
                ClienteFrecuente.telefono_whatsapp == tel_clean,
            )
            .first()
        )
        if cli and cli.ultima_direccion:
            ultima_direccion = cli.ultima_direccion

    logo_row = (
        db.query(ComercioLogo.actualizado_en)
        .filter(ComercioLogo.tenant_id == comercio.id)
        .first()
    )
    logo_url = (
        f"/api/v1/public/logo/{comercio.id}?v={int(logo_row[0].timestamp()) if logo_row[0] else 1}"
        if logo_row
        else None
    )

    return {
        "comercio": {
            "nombre_comercial": comercio.nombre_comercial,
            "giro": giro_code,
            "simbolo_moneda": comercio.simbolo_moneda or "C$",
            "logo_url": logo_url,
            "moneda": comercio.moneda or "NIO",
            "acepta_efectivo": bool(comercio.acepta_efectivo),
            "acepta_transferencia": bool(comercio.acepta_transferencia),
            "datos_bancarios_texto": comercio.datos_bancarios_texto or "",
            "usa_mesas": giro_code in ("RESTAURANTE", "CAFETERIA"),
        },
        "sucursal": {
            "id": suc.id,
            "nombre_sucursal": suc.nombre_sucursal,
            "codigo_whatsapp": suc.codigo_whatsapp,
            "direccion_fisica": suc.direccion_fisica or "",
            "horario_apertura": suc.horario_apertura,
            "horario_cierre": suc.horario_cierre,
            "abierto_ahora": bool(suc.abierto_ahora),
            "tiempo_estimado_entrega_min": suc.tiempo_estimado_entrega_min or 30,
            "costo_envio_base": float(suc.costo_envio_base or 0.0),
            "permite_domicilio": bool(suc.permite_domicilio),
            "permite_recoger_local": bool(suc.permite_recoger_local),
            "zonas_envio": [
                {
                    "id": z.id,
                    "nombre_zona": z.nombre_zona,
                    "costo_envio": float(z.costo_envio or 0.0),
                    "tiempo_extra_min": z.tiempo_extra_min or 0,
                }
                for z in suc.zonas_envio
                if z.activa
            ],
        },
        "categorias": cats_data,
        "platos": platos_data,
        "ultima_direccion": ultima_direccion,
        "cliente_envio_gratis": cliente_envio_gratis,
        "cliente_beneficios": beneficios_cli,
    }


@router.post("/api/v1/public/menu/{codigo_whatsapp}/pedido")
def crear_pedido_desde_menu_publico(
    codigo_whatsapp: str,
    payload: PublicOrderCreateRequest,
    db: Session = Depends(get_db),
):
    """Recibe el pedido interactivo del cliente, calcula precios en BD, lo manda al Kanban y avisa por WhatsApp."""
    suc = _find_branch_by_code(db, codigo_whatsapp)
    if not suc or not suc.comercio or not suc.comercio.activo:
        raise HTTPException(status_code=404, detail="Comercio o sucursal no disponible.")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Agrega al menos un producto a tu pedido.")

    comercio = suc.comercio
    tel_clean = clean_whatsapp_phone(payload.telefono_cliente)
    if len(tel_clean) < 7:
        raise HTTPException(status_code=400, detail="Ingresa un número de WhatsApp válido.")
    nom_cli = (payload.nombre_cliente or "").strip() or "Cliente WhatsApp"

    tipo_ent = (payload.tipo_entrega or "DOMICILIO").upper().strip()
    if tipo_ent not in ("DOMICILIO", "RECOGER_LOCAL", "MESA"):
        tipo_ent = "RECOGER_LOCAL"

    if tipo_ent == "DOMICILIO" and not (payload.direccion_entrega or "").strip():
        raise HTTPException(status_code=400, detail="Indica tu dirección de entrega para envío a domicilio.")

    subtotal = 0.0
    detalles: List[DetallePedido] = []
    resumen_lineas: List[str] = []

    for it in payload.items:
        plato = (
            db.query(ProductoPlato)
            .filter(
                ProductoPlato.id == it.plato_id,
                ProductoPlato.tenant_id == comercio.id,
                ProductoPlato.disponible_global.is_(True),
            )
            .first()
        )
        if not plato:
            raise HTTPException(status_code=404, detail=f"Producto ID {it.plato_id} no disponible.")
        qty = max(1, min(50, int(it.cantidad)))

        extras_list: List[Dict[str, Any]] = []
        extras_unit = 0.0
        if it.extras_ids:
            opts = (
                db.query(IngredienteOpcionPlato)
                .filter(
                    IngredienteOpcionPlato.id.in_(it.extras_ids),
                    IngredienteOpcionPlato.plato_id == plato.id,
                    IngredienteOpcionPlato.disponible_global.is_(True),
                )
                .all()
            )
            for opt in opts:
                extras_list.append({"nombre": opt.nombre, "precio": float(opt.precio_adicional or 0.0)})
                extras_unit += float(opt.precio_adicional or 0.0)

        line_sub = round((float(plato.precio_base) + extras_unit) * qty, 2)
        subtotal += line_sub

        sin_clean = [s.strip() for s in (it.sin_ingredientes or []) if s and s.strip()]
        det_parts = []
        if sin_clean:
            det_parts.append(f"sin {', '.join(sin_clean)}")
        if extras_list:
            det_parts.append("+" + ", ".join(e["nombre"] for e in extras_list))
        det_txt = f" ({'; '.join(det_parts)})" if det_parts else ""
        resumen_lineas.append(f"{qty}x {plato.nombre}{det_txt}")

        detalles.append(
            DetallePedido(
                plato_id=plato.id,
                nombre_plato=plato.nombre,
                cantidad=qty,
                precio_unitario_base=plato.precio_base,
                sin_ingredientes_json=json.dumps(sin_clean, ensure_ascii=False),
                extras_json=json.dumps(extras_list, ensure_ascii=False),
                precio_extras_unitario=round(extras_unit, 2),
                subtotal_linea=line_sub,
                notas_plato=(it.notas_plato or "").strip() or None,
            )
        )

    costo_envio = 0.0
    zona_nombre = None
    beneficios_cli = get_cliente_beneficios(db, comercio.id, tel_clean)
    es_vip_envio_gratis = bool(beneficios_cli.get("envio_gratis"))
    desc_monto, desc_label, usa_premio_sello = calcular_descuento_cliente(subtotal, beneficios_cli)
    if tipo_ent == "DOMICILIO":
        if payload.zona_envio_id:
            z = (
                db.query(ZonaEnvioSucursal)
                .filter(
                    ZonaEnvioSucursal.id == payload.zona_envio_id,
                    ZonaEnvioSucursal.sucursal_id == suc.id,
                    ZonaEnvioSucursal.activa.is_(True),
                )
                .first()
            )
            if z:
                costo_envio = 0.0 if es_vip_envio_gratis else float(z.costo_envio or 0.0)
                zona_nombre = z.nombre_zona
            else:
                costo_envio = 0.0 if es_vip_envio_gratis else float(suc.costo_envio_base or 0.0)
        else:
            costo_envio = 0.0 if es_vip_envio_gratis else float(suc.costo_envio_base or 0.0)

    total = round(max(0.0, subtotal - desc_monto) + costo_envio, 2)
    if usa_premio_sello and desc_monto > 0:
        consumir_premio_sellos(db, comercio.id, tel_clean)
    metodo_pago = (payload.metodo_pago or "EFECTIVO").upper().strip()
    if metodo_pago not in ("EFECTIVO", "TRANSFERENCIA"):
        metodo_pago = "EFECTIVO"

    paga_con = payload.paga_con_monto
    cambio = None
    if metodo_pago == "EFECTIVO" and paga_con is not None and float(paga_con) >= total:
        cambio = round(float(paga_con) - total, 2)

    now = datetime.now(timezone.utc)
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
            ultima_direccion=(payload.direccion_entrega or "").strip() or None,
            ultima_zona_envio=zona_nombre,
            total_pedidos_realizados=1,
        )
        db.add(cli)
        db.flush()
    else:
        cli.nombre_cliente = nom_cli
        cli.total_pedidos_realizados = (cli.total_pedidos_realizados or 0) + 1
        if payload.direccion_entrega:
            cli.ultima_direccion = payload.direccion_entrega.strip()
        if zona_nombre:
            cli.ultima_zona_envio = zona_nombre
        cli.actualizado_en = now

    # Vincular o crear sesión de WhatsApp en SIGUIENDO_PEDIDO para seguimiento automático
    sesion = (
        db.query(SesionChatWhatsApp)
        .filter(
            SesionChatWhatsApp.telefono_cliente == tel_clean,
            SesionChatWhatsApp.sucursal_id == suc.id,
            SesionChatWhatsApp.activa.is_(True),
        )
        .order_by(SesionChatWhatsApp.id.desc())
        .first()
    )
    if not sesion:
        sesion = SesionChatWhatsApp(
            tenant_id=comercio.id,
            sucursal_id=suc.id,
            telefono_cliente=tel_clean,
            nombre_cliente=nom_cli,
            modo_atencion="BOT_ACTIVO",
            estado_conversacion="SIGUIENDO_PEDIDO",
            carrito_json="{}",
            como_se_realizo_resumen=f"Menú Web Interactivo ({suc.nombre_sucursal})",
            activa=True,
        )
        db.add(sesion)
        db.flush()

    folio = f"WEB-{now.strftime('%H%M')}-{random.randint(100, 999)}"
    dir_final = (
        (payload.mesa_nombre or "Consumo en mesa / local").strip()
        if tipo_ent == "MESA"
        else (
            (payload.direccion_entrega or "").strip()
            if tipo_ent == "DOMICILIO"
            else f"Recoger en {suc.nombre_sucursal}"
        )
    )
    resumen_texto = f"Pedido Menú Web: {', '.join(resumen_lineas)}. Entrega: {tipo_ent}. Pago: {metodo_pago}."
    sesion.resumen_ia_chat = resumen_texto
    sesion.nombre_cliente = nom_cli
    sesion.ultimo_mensaje_en = now

    pedido = Pedido(
        folio=folio,
        tenant_id=comercio.id,
        sucursal_id=suc.id,
        sesion_chat_id=sesion.id,
        cliente_frecuente_id=cli.id,
        telefono_cliente=tel_clean,
        nombre_cliente=nom_cli,
        tipo_entrega=tipo_ent,
        direccion_entrega=dir_final,
        estado_pedido="ESPERANDO_PAGO",
        subtotal=round(subtotal, 2),
        costo_envio=round(costo_envio, 2),
        total=total,
        resumen_chat_ia=resumen_texto,
        como_se_realizo_pedido=f"Menú Web Interactivo ({suc.nombre_sucursal})",
        notas_especiales=(payload.notas_especiales or "").strip() or None,
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
    sesion.estado_conversacion = "SIGUIENDO_PEDIDO"
    sesion.carrito_json = json.dumps({"items": [], "pedido_activo_id": pedido.id}, ensure_ascii=False)

    # Generar enlace de pago obligatorio (hoy Simulador Sandbox, listo para Pagadito API)
    checkout_data = simulated_gateway.create_checkout_session(db, pedido)
    checkout_rel = checkout_data["checkout_url"]
    base_url = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
    checkout_full = f"{base_url}{checkout_rel}"

    sym = comercio.simbolo_moneda or "C$"
    msg_wa = (
        f"¡Recibimos tu solicitud *#{folio}* desde el menú web de *{comercio.nombre_comercial}*!\n\n"
        + "\n".join(f"- {l}" for l in resumen_lineas)
        + f"\n\nTotal a pagar: *{sym}{total:.2f}*\n"
        f"Estado: *PENDIENTE DE PAGO* _(aún no entra a cocina)_\n\n"
        f"Completa tu pago aquí para confirmar tu pedido al instante:\n{checkout_full}"
    )
    db.add(
        MensajeChatWhatsApp(
            sesion_id=sesion.id,
            tenant_id=comercio.id,
            remitente="BOT",
            tipo_contenido="TEXTO",
            contenido=msg_wa,
        )
    )
    db.commit()

    # Enviar enlace de pago por WhatsApp (el pedido entrará al Kanban vía SSE cuando el pago sea aprobado)
    send_whatsapp_text(tel_clean, msg_wa)

    return {
        "status": "ok",
        "pedido_id": pedido.id,
        "folio": folio,
        "total": total,
        "simbolo_moneda": sym,
        "estado_pedido": pedido.estado_pedido,
        "estado_pago": pedido.estado_pago,
        "checkout_url": checkout_rel,
        "tiempo_estimado_min": suc.tiempo_estimado_entrega_min or 30,
    }

