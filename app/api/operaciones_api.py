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
from app.models.saas_models import (
    ClienteFrecuente,
    DetallePedido,
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

    costo_envio = 0.0
    if tipo_ent == "DOMICILIO":
        if payload.zona_envio_id:
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
    total = round(subtotal + costo_envio, 2)

    tel_clean = "".join(ch for ch in (payload.telefono_cliente or "00000000") if ch.isdigit()) or "00000000"
    nom_cli = payload.nombre_cliente.strip() or "Cliente en Local"

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
# B.4 — DIRECTORIO DE CLIENTES FRECUENTES
# =====================================================================
@router.get("/comercio/{tenant_id}/clientes")
def listar_clientes_frecuentes(
    tenant_id: int,
    q: Optional[str] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_tenant_isolation(current_user, tenant_id)
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

    return {
        "clientes": [
            {
                "id": c.id,
                "nombre_cliente": c.nombre_cliente or "Sin nombre",
                "telefono_whatsapp": c.telefono_whatsapp,
                "ultima_direccion": c.ultima_direccion or "",
                "ultima_zona_envio": c.ultima_zona_envio or "",
                "total_pedidos": max(c.total_pedidos_realizados, conteo_por_tel.get(c.telefono_whatsapp, 0)),
                "total_gastado": gasto_por_tel.get(c.telefono_whatsapp, 0.0),
                "actualizado_en": c.actualizado_en.strftime("%d/%m/%Y") if c.actualizado_en else "",
            }
            for c in clientes
        ]
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
