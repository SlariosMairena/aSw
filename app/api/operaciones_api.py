"""
Rutas operativas avanzadas del Panel Web (A.1, A.4, B.1, B.4 y Exportación):
- Gestión completa de Sucursales, Zonas de Envío, Mesas y Reservaciones
- Kardex de Movimientos de Inventario + Agotado por Sucursal (StockSucursal)
- Vinculación Producto <-> Inventario (Recetas para descuento automático)
- Toma de Pedidos en Local / Mostrador (POS rápido)
- Directorio de Clientes Frecuentes
- Exportación CSV (compatible Excel) de Estadísticas de Ventas
"""
import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import record_audit_log
from app.core.database import get_db
from app.core.deps import (
    get_current_user,
    require_comercio_admin,
    verify_tenant_isolation,
)
from app.core.inventory_sync import deduct_inventory_for_delivered_order
from app.core.realtime import publish_tenant_event
from app.models.saas_models import (
    BeneficioCliente,
    ClienteFrecuente,
    ConfigFidelidadComercio,
    DetallePedido,
    FidelidadCliente,
    IngredienteOpcionPlato,
    ItemInventario,
    MesaZona,
    MovimientoInventario,
    PagoTransaccion,
    Pedido,
    ProductoPlato,
    RecetaProductoInventario,
    ReservacionMesa,
    StockSucursal,
    SucursalComercio,
    TenantComercio,
    Usuario,
    ZonaEnvioSucursal,
)

router = APIRouter(prefix="/api/v1/panel", tags=["Operaciones Avanzadas del Comercio"])


def _verify_staff_branch_scope(
    current_user: Usuario, target_sucursal_id: Optional[int]
) -> None:
    if (
        current_user.rol == "SUCURSAL_STAFF"
        and current_user.sucursal_id
        and target_sucursal_id is not None
        and target_sucursal_id != current_user.sucursal_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Aislamiento por sucursal: Tu usuario solo tiene acceso a su sucursal asignada.",
        )


# =====================================================================
# ESQUEMAS PYDANTIC
# =====================================================================
class EditarSucursalRequest(BaseModel):
    nombre_sucursal: str
    direccion_fisica: Optional[str] = None
    telefono_sucursal: Optional[str] = None
    horario_apertura: str = "08:00"
    horario_cierre: str = "22:00"
    tiempo_estimado_entrega_min: int = 35
    costo_envio_base: float = 35.0
    pedido_minimo_domicilio: float = 0.0
    permite_domicilio: bool = True
    permite_recoger_local: bool = True
    ancho_ticket_termico: str = "80mm"
    abierto_ahora: bool = True


class ZonaEnvioInput(BaseModel):
    nombre_zona: str
    costo_envio: float = 35.0
    tiempo_extra_min: int = 0


class MesaInput(BaseModel):
    nombre_mesa: str
    capacidad_personas: int = 4
    monto_anticipo_reserva: float = 150.0


class CambiarEstadoReservaRequest(BaseModel):
    estado_reserva: str  # CONFIRMADA | COMPLETADA | CANCELADA


class ToggleAgotadoSucursalRequest(BaseModel):
    sucursal_id: int


class RecetaItemInput(BaseModel):
    item_inventario_id: int
    cantidad_por_unidad: float = 1.0


class GuardarRecetaRequest(BaseModel):
    items: List[RecetaItemInput] = []


class PedidoManualItemInput(BaseModel):
    plato_id: int
    cantidad: int = 1
    extras_ids: List[int] = []
    notas_plato: Optional[str] = None


class CrearPedidoManualRequest(BaseModel):
    sucursal_id: int
    tipo_entrega: str = "MESA"  # MESA | RECOGER_LOCAL | DOMICILIO
    nombre_cliente: str = "Cliente en Local"
    telefono_cliente: str = "00000000"
    direccion_entrega: Optional[str] = None
    zona_envio_id: Optional[int] = None
    mesa_nombre: Optional[str] = None
    metodo_pago: str = "EFECTIVO"  # EFECTIVO | TRANSFERENCIA | TARJETA_LOCAL
    marcar_pagado: bool = True
    paga_con_monto: Optional[float] = None
    estado_inicial: str = "RECIBIDO"  # RECIBIDO | EN_PROCESO | LISTO | ENTREGADO
    notas_especiales: Optional[str] = None
    items: List[PedidoManualItemInput]


class ClienteInput(BaseModel):
    nombre_cliente: str
    telefono_whatsapp: str
    ultima_direccion: Optional[str] = None
    ultima_zona_envio: Optional[str] = None


# =====================================================================
# A.1 — EDICIÓN DE SUCURSALES, ZONAS DE ENVÍO, MESAS Y RESERVACIONES
# =====================================================================
@router.put("/comercio/sucursales/{sucursal_id}")
def editar_sucursal_comercio(
    sucursal_id: int,
    payload: EditarSucursalRequest,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    suc = db.query(SucursalComercio).filter(SucursalComercio.id == sucursal_id).first()
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    verify_tenant_isolation(current_user, suc.tenant_id)

    suc.nombre_sucursal = payload.nombre_sucursal.strip()
    suc.direccion_fisica = (payload.direccion_fisica or "").strip()
    suc.telefono_sucursal = (payload.telefono_sucursal or "").strip() or None
    suc.horario_apertura = payload.horario_apertura.strip() or "08:00"
    suc.horario_cierre = payload.horario_cierre.strip() or "22:00"
    suc.tiempo_estimado_entrega_min = max(5, int(payload.tiempo_estimado_entrega_min))
    suc.costo_envio_base = max(0.0, float(payload.costo_envio_base))
    suc.pedido_minimo_domicilio = max(0.0, float(payload.pedido_minimo_domicilio))
    suc.permite_domicilio = payload.permite_domicilio
    suc.permite_recoger_local = payload.permite_recoger_local
    suc.ancho_ticket_termico = (
        payload.ancho_ticket_termico if payload.ancho_ticket_termico in ("58mm", "80mm") else "80mm"
    )
    suc.abierto_ahora = payload.abierto_ahora
    db.commit()
    return {"status": "ok", "sucursal_id": suc.id}


@router.post("/comercio/sucursales/{sucursal_id}/zonas")
def agregar_zona_envio_sucursal(
    sucursal_id: int,
    payload: ZonaEnvioInput,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    suc = db.query(SucursalComercio).filter(SucursalComercio.id == sucursal_id).first()
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    verify_tenant_isolation(current_user, suc.tenant_id)

    zona = ZonaEnvioSucursal(
        sucursal_id=suc.id,
        nombre_zona=payload.nombre_zona.strip(),
        costo_envio=max(0.0, float(payload.costo_envio)),
        tiempo_extra_min=max(0, int(payload.tiempo_extra_min)),
        activa=True,
    )
    db.add(zona)
    db.commit()
    db.refresh(zona)
    return {"status": "ok", "zona_id": zona.id}


@router.delete("/comercio/zonas/{zona_id}")
def eliminar_zona_envio_sucursal(
    zona_id: int,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    zona = db.query(ZonaEnvioSucursal).filter(ZonaEnvioSucursal.id == zona_id).first()
    if not zona or not zona.sucursal:
        raise HTTPException(status_code=404, detail="Zona de envío no encontrada.")
    verify_tenant_isolation(current_user, zona.sucursal.tenant_id)
    db.delete(zona)
    db.commit()
    return {"status": "ok"}


@router.post("/comercio/sucursales/{sucursal_id}/mesas")
def agregar_mesa_sucursal(
    sucursal_id: int,
    payload: MesaInput,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    suc = db.query(SucursalComercio).filter(SucursalComercio.id == sucursal_id).first()
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    verify_tenant_isolation(current_user, suc.tenant_id)

    mesa = MesaZona(
        tenant_id=suc.tenant_id,
        sucursal_id=suc.id,
        nombre_mesa=payload.nombre_mesa.strip(),
        capacidad_personas=max(1, int(payload.capacidad_personas)),
        monto_anticipo_reserva=max(0.0, float(payload.monto_anticipo_reserva)),
        activa=True,
    )
    db.add(mesa)
    db.commit()
    db.refresh(mesa)
    return {"status": "ok", "mesa_id": mesa.id}


@router.delete("/comercio/mesas/{mesa_id}")
def eliminar_mesa_sucursal(
    mesa_id: int,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    mesa = db.query(MesaZona).filter(MesaZona.id == mesa_id).first()
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa no encontrada.")
    verify_tenant_isolation(current_user, mesa.tenant_id)
    db.delete(mesa)
    db.commit()
    return {"status": "ok"}


@router.patch("/comercio/reservaciones/{reserva_id}/estado")
def actualizar_estado_reserva(
    reserva_id: int,
    payload: CambiarEstadoReservaRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    res = db.query(ReservacionMesa).filter(ReservacionMesa.id == reserva_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Reservación no encontrada.")
    verify_tenant_isolation(current_user, res.tenant_id)
    _verify_staff_branch_scope(current_user, res.sucursal_id)

    est = payload.estado_reserva.upper().strip()
    if est not in ("CONFIRMADA", "COMPLETADA", "CANCELADA", "ESPERANDO_PAGO"):
        raise HTTPException(status_code=400, detail="Estado de reservación inválido.")
    res.estado_reserva = est
    db.commit()
    return {"status": "ok", "estado_reserva": res.estado_reserva}


# =====================================================================
# A.4 & B.4 — KARDEX DE MOVIMIENTOS, STOCK POR SUCURSAL Y RECETAS
# =====================================================================
@router.get("/comercio/{tenant_id}/inventario/movimientos")
def listar_movimientos_inventario(
    tenant_id: int,
    item_id: Optional[int] = None,
    sucursal_id: Optional[int] = None,
    limit: int = 100,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol == "SUCURSAL_STAFF" and current_user.sucursal_id:
        sucursal_id = current_user.sucursal_id

    q = (
        db.query(MovimientoInventario)
        .join(ItemInventario, MovimientoInventario.item_id == ItemInventario.id)
        .filter(MovimientoInventario.tenant_id == tenant_id)
    )
    if item_id:
        q = q.filter(MovimientoInventario.item_id == item_id)
    if sucursal_id:
        q = q.filter(ItemInventario.sucursal_id == sucursal_id)

    movs = q.order_by(MovimientoInventario.id.desc()).limit(min(limit, 200)).all()
    return {
        "movimientos": [
            {
                "id": m.id,
                "item_id": m.item_id,
                "item_nombre": m.item.nombre_insumo if m.item else f"Ítem #{m.item_id}",
                "unidad_medida": m.item.unidad_medida if m.item else "",
                "tipo_movimiento": m.tipo_movimiento,
                "cantidad": m.cantidad,
                "stock_anterior": m.stock_anterior,
                "stock_nuevo": m.stock_nuevo,
                "motivo": m.motivo,
                "actor_email": m.actor_email,
                "fecha": m.creado_en.strftime("%d/%m/%Y %H:%M") if m.creado_en else "",
            }
            for m in movs
        ]
    }


@router.post("/comercio/platos/{plato_id}/toggle-agotado-sucursal")
def toggle_agotado_sucursal_plato(
    plato_id: int,
    payload: ToggleAgotadoSucursalRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marca o desmarca 'Agotado hoy' para un producto únicamente en la sucursal indicada."""
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    verify_tenant_isolation(current_user, plato.tenant_id)
    _verify_staff_branch_scope(current_user, payload.sucursal_id)

    suc = (
        db.query(SucursalComercio)
        .filter(
            SucursalComercio.id == payload.sucursal_id,
            SucursalComercio.tenant_id == plato.tenant_id,
        )
        .first()
    )
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal inválida para este comercio.")

    reg = (
        db.query(StockSucursal)
        .filter(
            StockSucursal.tenant_id == plato.tenant_id,
            StockSucursal.sucursal_id == suc.id,
            StockSucursal.plato_id == plato.id,
        )
        .first()
    )
    if not reg:
        reg = StockSucursal(
            tenant_id=plato.tenant_id,
            sucursal_id=suc.id,
            plato_id=plato.id,
            agotado_hoy=True,
        )
        db.add(reg)
    else:
        reg.agotado_hoy = not reg.agotado_hoy
        reg.actualizado_en = datetime.now(timezone.utc)
    db.commit()
    return {
        "status": "ok",
        "plato_id": plato.id,
        "sucursal_id": suc.id,
        "agotado_hoy": reg.agotado_hoy,
    }


@router.get("/comercio/{tenant_id}/recetas")
def listar_recetas_inventario(
    tenant_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    rows = (
        db.query(RecetaProductoInventario)
        .filter(RecetaProductoInventario.tenant_id == tenant_id)
        .all()
    )
    por_plato: Dict[int, List[Dict[str, object]]] = {}
    for r in rows:
        por_plato.setdefault(r.plato_id, []).append(
            {
                "id": r.id,
                "item_inventario_id": r.item_inventario_id,
                "item_nombre": r.item_inventario.nombre_insumo if r.item_inventario else "",
                "unidad_medida": r.item_inventario.unidad_medida if r.item_inventario else "",
                "cantidad_por_unidad": r.cantidad_por_unidad,
            }
        )
    return {"recetas_por_plato": por_plato}


@router.put("/comercio/platos/{plato_id}/receta")
def guardar_receta_producto(
    plato_id: int,
    payload: GuardarRecetaRequest,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    verify_tenant_isolation(current_user, plato.tenant_id)

    db.query(RecetaProductoInventario).filter(
        RecetaProductoInventario.plato_id == plato.id,
        RecetaProductoInventario.tenant_id == plato.tenant_id,
    ).delete()

    valid_ids = {
        i.id
        for i in db.query(ItemInventario)
        .filter(ItemInventario.tenant_id == plato.tenant_id, ItemInventario.activo.is_(True))
        .all()
    }
    count = 0
    for it in payload.items:
        if it.item_inventario_id in valid_ids and it.cantidad_por_unidad > 0:
            db.add(
                RecetaProductoInventario(
                    tenant_id=plato.tenant_id,
                    plato_id=plato.id,
                    item_inventario_id=it.item_inventario_id,
                    cantidad_por_unidad=round(float(it.cantidad_por_unidad), 3),
                )
            )
            count += 1
    db.commit()
    return {"status": "ok", "plato_id": plato.id, "vinculados": count}


# =====================================================================
# B.1 — PEDIDO MANUAL EN LOCAL / MOSTRADOR (POS RÁPIDO)
# =====================================================================
@router.post("/comercio/{tenant_id}/pedidos/manual")
def crear_pedido_manual_pos(
    tenant_id: int,
    payload: CrearPedidoManualRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    _verify_staff_branch_scope(current_user, payload.sucursal_id)

    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    suc = (
        db.query(SucursalComercio)
        .filter(
            SucursalComercio.id == payload.sucursal_id,
            SucursalComercio.tenant_id == tenant_id,
        )
        .first()
    )
    if not comercio or not suc:
        raise HTTPException(status_code=404, detail="Comercio o sucursal no encontrada.")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Agrega al menos un producto al pedido.")

    tipo_ent = payload.tipo_entrega.upper().strip()
    if tipo_ent not in ("MESA", "RECOGER_LOCAL", "DOMICILIO"):
        tipo_ent = "RECOGER_LOCAL"

    estado_ini = payload.estado_inicial.upper().strip()
    if estado_ini not in ("RECIBIDO", "EN_PROCESO", "LISTO", "ENTREGADO"):
        estado_ini = "RECIBIDO"

    # Calcular precios de forma blindada en servidor
    subtotal = 0.0
    detalles_a_guardar = []
    resumen_lineas = []
    for item_in in payload.items:
        plato = (
            db.query(ProductoPlato)
            .filter(
                ProductoPlato.id == item_in.plato_id,
                ProductoPlato.tenant_id == tenant_id,
            )
            .first()
        )
        if not plato:
            raise HTTPException(
                status_code=404, detail=f"Producto ID {item_in.plato_id} no encontrado."
            )
        qty = max(1, int(item_in.cantidad))
        extras_list = []
        extras_unit = 0.0
        if item_in.extras_ids:
            opts = (
                db.query(IngredienteOpcionPlato)
                .filter(
                    IngredienteOpcionPlato.id.in_(item_in.extras_ids),
                    IngredienteOpcionPlato.plato_id == plato.id,
                )
                .all()
            )
            for opt in opts:
                extras_list.append({"nombre": opt.nombre, "precio": opt.precio_adicional})
                extras_unit += float(opt.precio_adicional or 0.0)

        unit_total = float(plato.precio_base) + extras_unit
        line_sub = round(unit_total * qty, 2)
        subtotal += line_sub
        resumen_lineas.append(f"{qty}x {plato.nombre}")
        detalles_a_guardar.append(
            DetallePedido(
                plato_id=plato.id,
                nombre_plato=plato.nombre,
                cantidad=qty,
                precio_unitario_base=plato.precio_base,
                sin_ingredientes_json="[]",
                extras_json=json.dumps(extras_list, ensure_ascii=False),
                precio_extras_unitario=round(extras_unit, 2),
                subtotal_linea=line_sub,
                notas_plato=(item_in.notas_plato or "").strip() or None,
            )
        )

    tel_clean = "".join(ch for ch in (payload.telefono_cliente or "00000000") if ch.isdigit()) or "00000000"
    nom_cli = payload.nombre_cliente.strip() or "Cliente en Local"

    from app.services.fidelidad_service import (
        calcular_descuento_cliente,
        consumir_premio_sellos,
        get_cliente_beneficios,
    )

    beneficios_cli = get_cliente_beneficios(db, tenant_id, tel_clean)
    desc_monto, desc_label, usa_premio_sello = calcular_descuento_cliente(subtotal, beneficios_cli)

    costo_envio = 0.0
    if tipo_ent == "DOMICILIO":
        if beneficios_cli.get("envio_gratis"):
            costo_envio = 0.0
        elif payload.zona_envio_id:
            zona_obj = (
                db.query(ZonaEnvioSucursal)
                .filter(
                    ZonaEnvioSucursal.id == payload.zona_envio_id,
                    ZonaEnvioSucursal.sucursal_id == suc.id,
                    ZonaEnvioSucursal.activa == True,
                )
                .first()
            )
            costo_envio = float(zona_obj.costo_envio) if zona_obj else float(suc.costo_envio_base or 0.0)
        else:
            costo_envio = float(suc.costo_envio_base or 0.0)
    total = round(max(0.0, subtotal - desc_monto) + costo_envio, 2)
    if usa_premio_sello and desc_monto > 0:
        consumir_premio_sellos(db, tenant_id, tel_clean)

    cli_obj = None
    if tel_clean != "00000000":
        cli_obj = (
            db.query(ClienteFrecuente)
            .filter(
                ClienteFrecuente.tenant_id == tenant_id,
                ClienteFrecuente.telefono_whatsapp == tel_clean,
            )
            .first()
        )
        if not cli_obj:
            cli_obj = ClienteFrecuente(
                tenant_id=tenant_id,
                telefono_whatsapp=tel_clean,
                nombre_cliente=nom_cli,
                ultima_direccion=(payload.direccion_entrega or "").strip() or None,
                total_pedidos_realizados=1,
            )
            db.add(cli_obj)
            db.flush()
        else:
            cli_obj.nombre_cliente = nom_cli
            cli_obj.total_pedidos_realizados += 1
            if payload.direccion_entrega:
                cli_obj.ultima_direccion = payload.direccion_entrega.strip()
            cli_obj.actualizado_en = datetime.now(timezone.utc)

    folio = f"LOC-{datetime.now(timezone.utc).strftime('%H%M')}-{random.randint(100, 999)}"
    paga_con = payload.paga_con_monto
    cambio = None
    if payload.metodo_pago == "EFECTIVO" and paga_con is not None and paga_con >= total:
        cambio = round(paga_con - total, 2)

    dir_final = (
        payload.mesa_nombre.strip()
        if tipo_ent == "MESA" and payload.mesa_nombre
        else (
            payload.direccion_entrega.strip()
            if tipo_ent == "DOMICILIO" and payload.direccion_entrega
            else ("Consumo en local / Mesa" if tipo_ent == "MESA" else "Retiro en mostrador")
        )
    )

    estado_pago = "PAGADO" if payload.marcar_pagado else "PENDIENTE"
    pedido = Pedido(
        folio=folio,
        tenant_id=tenant_id,
        sucursal_id=suc.id,
        cliente_frecuente_id=cli_obj.id if cli_obj else None,
        telefono_cliente=tel_clean,
        nombre_cliente=nom_cli,
        tipo_entrega=tipo_ent,
        direccion_entrega=dir_final,
        estado_pedido=estado_ini,
        subtotal=round(subtotal, 2),
        costo_envio=round(costo_envio, 2),
        total=total,
        resumen_chat_ia=f"Pedido registrado en mostrador/local por {current_user.nombre_completo}: {', '.join(resumen_lineas)}.",
        como_se_realizo_pedido=f"Registro directo en Panel POS ({suc.nombre_sucursal}) • {tipo_ent}",
        notas_especiales=(payload.notas_especiales or "").strip() or None,
        metodo_pago=payload.metodo_pago.upper().strip(),
        estado_pago=estado_pago,
        paga_con_monto=paga_con,
        cambio_a_entregar=cambio,
    )
    db.add(pedido)
    db.flush()

    for det in detalles_a_guardar:
        det.pedido_id = pedido.id
        db.add(det)

    db.add(
        PagoTransaccion(
            tenant_id=tenant_id,
            pedido_id=pedido.id,
            modo_operacion="MODO_1_DIRECTO",
            metodo=pedido.metodo_pago,
            monto=total,
            moneda=comercio.moneda or "NIO",
            estado="APROBADO" if estado_pago == "PAGADO" else "PENDIENTE",
            paga_con=paga_con,
            cambio=cambio,
            validado_por_usuario_id=current_user.id if estado_pago == "PAGADO" else None,
        )
    )

    comercio.pedidos_mes_actual = (comercio.pedidos_mes_actual or 0) + 1
    db.flush()

    stock_descontado = []
    if estado_ini == "ENTREGADO":
        stock_descontado = deduct_inventory_for_delivered_order(
            db, pedido, current_user.email
        )

    db.commit()
    publish_tenant_event(
        tenant_id,
        "NUEVO_PEDIDO",
        {"pedido_id": pedido.id, "folio": pedido.folio},
    )

    record_audit_log(
        db=db,
        accion="CREAR_PEDIDO_LOCAL_POS",
        recurso_tipo="PEDIDO",
        recurso_id=pedido.folio,
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"total": total, "tipo_entrega": tipo_ent, "estado_inicial": estado_ini},
    )
    return {
        "status": "ok",
        "pedido_id": pedido.id,
        "folio": pedido.folio,
        "total": total,
        "stock_descontado": stock_descontado,
    }


# =====================================================================
# B.4 — DIRECTORIO DE CLIENTES FRECUENTES Y BENEFICIO ENVÍO GRATIS VIP
# =====================================================================
def cliente_tiene_envio_gratis(db: Session, tenant_id: int, telefono_limpio: str) -> bool:
    """Devuelve True si el dueño autorizó envío gratis VIP para este cliente."""
    tel = "".join(ch for ch in (telefono_limpio or "") if ch.isdigit())
    if not tel or tel == "00000000":
        return False
    row = (
        db.query(BeneficioCliente.envio_gratis)
        .join(ClienteFrecuente, BeneficioCliente.cliente_id == ClienteFrecuente.id)
        .filter(
            ClienteFrecuente.tenant_id == tenant_id,
            ClienteFrecuente.telefono_whatsapp == tel,
            BeneficioCliente.envio_gratis.is_(True),
        )
        .first()
    )
    return bool(row and row[0])


class ClienteEnvioGratisPayload(BaseModel):
    envio_gratis: bool


class ClienteVipPayload(BaseModel):
    envio_gratis: Optional[bool] = None
    descuento_pct: Optional[float] = None
    es_vip: Optional[bool] = None
    notificar_whatsapp: bool = True


class ConfigFidelidadPayload(BaseModel):
    activo: bool = False
    sellos_meta: int = 5
    monto_minimo_sello: float = 150.0
    premio_descuento_pct: float = 15.0


@router.put("/comercio/{tenant_id}/fidelidad/config")
def guardar_config_fidelidad(
    tenant_id: int,
    payload: ConfigFidelidadPayload,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    from app.services.fidelidad_service import get_or_create_config_fidelidad

    cfg = get_or_create_config_fidelidad(db, tenant_id)
    cfg.activo = bool(payload.activo)
    cfg.sellos_meta = max(2, min(30, int(payload.sellos_meta or 5)))
    cfg.monto_minimo_sello = max(0.0, float(payload.monto_minimo_sello or 0.0))
    cfg.premio_descuento_pct = max(1.0, min(100.0, float(payload.premio_descuento_pct or 15.0)))
    db.commit()
    return {
        "status": "ok",
        "fidelidad_config": {
            "activo": bool(cfg.activo),
            "sellos_meta": int(cfg.sellos_meta),
            "monto_minimo_sello": float(cfg.monto_minimo_sello),
            "premio_descuento_pct": float(cfg.premio_descuento_pct),
        },
    }


@router.get("/comercio/{tenant_id}/clientes")
def listar_clientes_frecuentes(
    tenant_id: int,
    q: Optional[str] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    from app.services.fidelidad_service import get_or_create_config_fidelidad

    cfg_fid = get_or_create_config_fidelidad(db, tenant_id)
    query = db.query(ClienteFrecuente).filter(ClienteFrecuente.tenant_id == tenant_id)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            (ClienteFrecuente.nombre_cliente.ilike(term))
            | (ClienteFrecuente.telefono_whatsapp.ilike(term))
        )
    clientes = query.order_by(ClienteFrecuente.total_pedidos_realizados.desc(), ClienteFrecuente.id.desc()).all()

    pedidos = (
        db.query(Pedido)
        .filter(Pedido.tenant_id == tenant_id, Pedido.estado_pedido != "CANCELADO")
        .all()
    )
    gasto_por_tel: Dict[str, float] = {}
    conteo_por_tel: Dict[str, int] = {}
    for p in pedidos:
        tel = p.telefono_cliente or ""
        gasto_por_tel[tel] = round(gasto_por_tel.get(tel, 0.0) + float(p.total or 0.0), 2)
        conteo_por_tel[tel] = conteo_por_tel.get(tel, 0) + 1

    beneficios_rows = (
        db.query(BeneficioCliente.cliente_id, BeneficioCliente.envio_gratis)
        .filter(BeneficioCliente.tenant_id == tenant_id)
        .all()
    )
    envio_gratis_map = {cid: bool(eg) for cid, eg in beneficios_rows}

    fid_rows = (
        db.query(FidelidadCliente)
        .filter(FidelidadCliente.tenant_id == tenant_id)
        .all()
    )
    fid_map = {f.cliente_id: f for f in fid_rows}

    return {
        "fidelidad_config": {
            "activo": bool(cfg_fid.activo),
            "sellos_meta": int(cfg_fid.sellos_meta or 5),
            "monto_minimo_sello": float(cfg_fid.monto_minimo_sello or 150.0),
            "premio_descuento_pct": float(cfg_fid.premio_descuento_pct or 15.0),
        },
        "clientes": [
            {
                "id": c.id,
                "nombre_cliente": c.nombre_cliente or "Sin nombre",
                "telefono_whatsapp": c.telefono_whatsapp,
                "ultima_direccion": c.ultima_direccion or "",
                "ultima_zona_envio": c.ultima_zona_envio or "",
                "total_pedidos": max(c.total_pedidos_realizados, conteo_por_tel.get(c.telefono_whatsapp, 0)),
                "total_pedidos_realizados": max(c.total_pedidos_realizados, conteo_por_tel.get(c.telefono_whatsapp, 0)),
                "total_gastado": gasto_por_tel.get(c.telefono_whatsapp, 0.0),
                "envio_gratis": envio_gratis_map.get(c.id, False),
                "descuento_pct": float(fid_map[c.id].descuento_pct or 0.0) if c.id in fid_map else 0.0,
                "es_vip": bool(
                    (c.id in fid_map and (fid_map[c.id].es_vip or float(fid_map[c.id].descuento_pct or 0.0) > 0))
                    or envio_gratis_map.get(c.id, False)
                ),
                "sellos_actuales": int(fid_map[c.id].sellos_actuales or 0) if c.id in fid_map else 0,
                "premios_disponibles": int(fid_map[c.id].premios_disponibles or 0) if c.id in fid_map else 0,
                "actualizado_en": c.actualizado_en.strftime("%d/%m/%Y") if c.actualizado_en else "",
            }
            for c in clientes
        ],
    }


@router.patch("/comercio/{tenant_id}/clientes/{cliente_id}/vip")
def actualizar_vip_cliente(
    tenant_id: int,
    cliente_id: int,
    payload: ClienteVipPayload,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Actualiza Envío Gratis, Descuento VIP (%) y etiqueta VIP del cliente, y le avisa por WhatsApp."""
    verify_tenant_isolation(current_user, tenant_id)
    from app.services.fidelidad_service import (
        get_or_create_fidelidad_cliente,
        notificar_beneficio_vip_whatsapp,
    )

    cli = (
        db.query(ClienteFrecuente)
        .filter(ClienteFrecuente.id == cliente_id, ClienteFrecuente.tenant_id == tenant_id)
        .first()
    )
    if not cli:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    ben = db.query(BeneficioCliente).filter(BeneficioCliente.cliente_id == cli.id).first()
    if not ben:
        ben = BeneficioCliente(tenant_id=tenant_id, cliente_id=cli.id, envio_gratis=False)
        db.add(ben)
    fid = get_or_create_fidelidad_cliente(db, tenant_id, cli.id)

    prev_eg = bool(ben.envio_gratis)
    prev_desc = float(fid.descuento_pct or 0.0)
    prev_vip = bool(fid.es_vip)

    if payload.envio_gratis is not None:
        ben.envio_gratis = bool(payload.envio_gratis)
    if payload.descuento_pct is not None:
        fid.descuento_pct = max(0.0, min(50.0, float(payload.descuento_pct)))
    if payload.es_vip is not None:
        fid.es_vip = bool(payload.es_vip)
    elif ben.envio_gratis or fid.descuento_pct > 0:
        fid.es_vip = True

    db.commit()

    activado = (
        (ben.envio_gratis and not prev_eg)
        or (fid.descuento_pct > prev_desc and fid.descuento_pct > 0)
        or (fid.es_vip and not prev_vip)
    )
    wa_enviado = False
    if payload.notificar_whatsapp and activado:
        wa_enviado = notificar_beneficio_vip_whatsapp(
            db, tenant_id, cli, bool(ben.envio_gratis), float(fid.descuento_pct), bool(fid.es_vip)
        )

    return {
        "status": "ok",
        "cliente_id": cli.id,
        "envio_gratis": bool(ben.envio_gratis),
        "descuento_pct": float(fid.descuento_pct),
        "es_vip": bool(fid.es_vip or ben.envio_gratis or fid.descuento_pct > 0),
        "wa_notificado": wa_enviado,
    }


@router.patch("/comercio/{tenant_id}/clientes/{cliente_id}/envio-gratis")
def actualizar_envio_gratis_cliente(
    tenant_id: int,
    cliente_id: int,
    payload: ClienteEnvioGratisPayload,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite al dueño otorgar o revocar el beneficio de Envío Gratis VIP a un cliente."""
    verify_tenant_isolation(current_user, tenant_id)
    cli = (
        db.query(ClienteFrecuente)
        .filter(ClienteFrecuente.id == cliente_id, ClienteFrecuente.tenant_id == tenant_id)
        .first()
    )
    if not cli:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    ben = (
        db.query(BeneficioCliente)
        .filter(BeneficioCliente.cliente_id == cli.id)
        .first()
    )
    if not ben:
        ben = BeneficioCliente(
            tenant_id=tenant_id,
            cliente_id=cli.id,
            envio_gratis=bool(payload.envio_gratis),
        )
        db.add(ben)
    else:
        ben.envio_gratis = bool(payload.envio_gratis)
    db.commit()
    return {
        "status": "ok",
        "cliente_id": cli.id,
        "envio_gratis": bool(ben.envio_gratis),
    }


@router.post("/comercio/{tenant_id}/clientes")
def crear_cliente_frecuente(
    tenant_id: int,
    payload: ClienteInput,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    tel = "".join(ch for ch in payload.telefono_whatsapp if ch.isdigit())
    if not tel:
        raise HTTPException(status_code=400, detail="Teléfono inválido.")
    existente = (
        db.query(ClienteFrecuente)
        .filter(ClienteFrecuente.tenant_id == tenant_id, ClienteFrecuente.telefono_whatsapp == tel)
        .first()
    )
    if existente:
        existente.nombre_cliente = payload.nombre_cliente.strip()
        existente.ultima_direccion = (payload.ultima_direccion or "").strip() or existente.ultima_direccion
        existente.ultima_zona_envio = (payload.ultima_zona_envio or "").strip() or existente.ultima_zona_envio
        existente.actualizado_en = datetime.now(timezone.utc)
        db.commit()
        return {"status": "ok", "cliente_id": existente.id, "updated": True}

    nuevo = ClienteFrecuente(
        tenant_id=tenant_id,
        telefono_whatsapp=tel,
        nombre_cliente=payload.nombre_cliente.strip(),
        ultima_direccion=(payload.ultima_direccion or "").strip() or None,
        ultima_zona_envio=(payload.ultima_zona_envio or "").strip() or None,
        total_pedidos_realizados=0,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return {"status": "ok", "cliente_id": nuevo.id, "updated": False}


# =====================================================================
# EXPORTACIÓN CSV (EXCEL) DE ESTADÍSTICAS DEL COMERCIO
# =====================================================================
@router.get("/comercio/{tenant_id}/estadisticas/exportar.csv")
def exportar_estadisticas_csv(
    tenant_id: int,
    periodo: str = "30d",
    sucursal_id: Optional[int] = None,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    ahora = datetime.now(timezone.utc)
    per = (periodo or "30d").lower().strip()
    desde_dt = None
    if per == "hoy":
        desde_dt = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    elif per == "7d":
        desde_dt = ahora - timedelta(days=7)
    elif per == "mes":
        desde_dt = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif per == "30d":
        desde_dt = ahora - timedelta(days=30)

    q = db.query(Pedido).filter(Pedido.tenant_id == tenant_id)
    if sucursal_id:
        q = q.filter(Pedido.sucursal_id == sucursal_id)
    if desde_dt is not None:
        q = q.filter(Pedido.creado_en >= desde_dt)
    pedidos = q.order_by(Pedido.creado_en.desc()).all()

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM UTF-8 para abrir tildes correctamente en Excel
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Folio",
            "Fecha",
            "Sucursal",
            "Cliente",
            "Teléfono",
            "Tipo Entrega",
            "Estado Pedido",
            "Método Pago",
            "Estado Pago",
            "Productos",
            f"Subtotal ({comercio.simbolo_moneda})",
            f"Envío ({comercio.simbolo_moneda})",
            f"Total ({comercio.simbolo_moneda})",
        ]
    )
    for p in pedidos:
        prods_txt = "; ".join(f"{d.cantidad}x {d.nombre_plato}" for d in p.detalles)
        writer.writerow(
            [
                p.folio,
                p.creado_en.strftime("%Y-%m-%d %H:%M") if p.creado_en else "",
                p.sucursal.nombre_sucursal if p.sucursal else "",
                p.nombre_cliente,
                p.telefono_cliente,
                p.tipo_entrega,
                p.estado_pedido,
                p.metodo_pago,
                p.estado_pago,
                prods_txt,
                f"{float(p.subtotal or 0):.2f}",
                f"{float(p.costo_envio or 0):.2f}",
                f"{float(p.total or 0):.2f}",
            ]
        )

    filename = f"estadisticas_{comercio.slug}_{per}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
