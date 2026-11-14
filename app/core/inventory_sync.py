"""
Helper de sincronización automática de inventario al entregar pedidos.
Idempotente: verifica por folio en MovimientoInventario para evitar descuentos dobles.
"""
from datetime import datetime, timezone
from typing import Dict, List
from sqlalchemy.orm import Session

from app.models.saas_models import (
    ItemInventario,
    MovimientoInventario,
    Pedido,
    RecetaProductoInventario,
)


def deduct_inventory_for_delivered_order(
    db: Session,
    pedido: Pedido,
    actor_email: str,
) -> List[Dict[str, object]]:
    """
    Descuenta del inventario los insumos vinculados por receta (o por coincidencia exacta
    de nombre si el comercio no ha configurado receta) cuando el pedido alcanza ENTREGADO.
    """
    motivo_tag = f"Venta #{pedido.folio}"
    ya_descontado = (
        db.query(MovimientoInventario)
        .filter(
            MovimientoInventario.tenant_id == pedido.tenant_id,
            MovimientoInventario.motivo == motivo_tag,
        )
        .first()
    )
    if ya_descontado:
        return []

    descontados: List[Dict[str, object]] = []
    recetas_all = (
        db.query(RecetaProductoInventario)
        .filter(RecetaProductoInventario.tenant_id == pedido.tenant_id)
        .all()
    )
    recetas_by_plato: Dict[int, List[RecetaProductoInventario]] = {}
    for rec in recetas_all:
        recetas_by_plato.setdefault(rec.plato_id, []).append(rec)

    items_activos = (
        db.query(ItemInventario)
        .filter(
            ItemInventario.tenant_id == pedido.tenant_id,
            ItemInventario.activo.is_(True),
        )
        .all()
    )

    def _apply_deduction(item: ItemInventario, qty: float) -> None:
        stock_ant = round(float(item.stock_actual or 0.0), 2)
        stock_nvo = round(max(0.0, stock_ant - qty), 2)
        item.stock_actual = stock_nvo
        item.actualizado_en = datetime.now(timezone.utc)
        db.add(
            MovimientoInventario(
                tenant_id=pedido.tenant_id,
                item_id=item.id,
                tipo_movimiento="SALIDA",
                cantidad=qty,
                stock_anterior=stock_ant,
                stock_nuevo=stock_nvo,
                motivo=motivo_tag,
                actor_email=actor_email,
            )
        )
        descontados.append(
            {"item": item.nombre_insumo, "cantidad": qty, "stock_nuevo": stock_nvo}
        )

    for det in pedido.detalles:
        qty_pedida = float(det.cantidad or 1)
        recs = recetas_by_plato.get(det.plato_id or -1, [])

        if recs:
            for rec in recs:
                item = next((i for i in items_activos if i.id == rec.item_inventario_id), None)
                if not item:
                    continue
                descuento = round(qty_pedida * float(rec.cantidad_por_unidad or 1.0), 3)
                if descuento > 0:
                    _apply_deduction(item, descuento)
        else:
            nom_norm = (det.nombre_plato or "").strip().lower()
            candidato = next(
                (
                    i
                    for i in items_activos
                    if i.nombre_insumo.strip().lower() == nom_norm
                    and i.sucursal_id == pedido.sucursal_id
                ),
                None,
            ) or next(
                (i for i in items_activos if i.nombre_insumo.strip().lower() == nom_norm),
                None,
            )
            if candidato and qty_pedida > 0:
                _apply_deduction(candidato, qty_pedida)

    return descontados
