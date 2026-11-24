"""
Servicio de restricciones y clasificación por producto/servicio (Capa 2).
Usa la tabla dedicada `restricciones_producto` sin alterar `productos_platos`.
"""
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.saas_models import RestriccionProducto


def get_restricciones_map(db: Session, tenant_id: int) -> Dict[int, Dict[str, Any]]:
    rows = (
        db.query(RestriccionProducto)
        .filter(RestriccionProducto.tenant_id == int(tenant_id))
        .all()
    )
    return {
        int(r.plato_id): {
            "requiere_validacion": bool(r.requiere_validacion),
            "solo_retiro_local": bool(r.solo_retiro_local),
            "nota_restriccion": (r.nota_restriccion or "").strip(),
        }
        for r in rows
    }


def save_restriccion_producto(
    db: Session,
    tenant_id: int,
    plato_id: int,
    requiere_validacion: bool = False,
    solo_retiro_local: bool = False,
    nota_restriccion: Optional[str] = None,
) -> Dict[str, Any]:
    req_val = bool(requiere_validacion)
    solo_ret = bool(solo_retiro_local)
    nota = (nota_restriccion or "").strip()[:240]

    row = (
        db.query(RestriccionProducto)
        .filter(RestriccionProducto.plato_id == int(plato_id))
        .first()
    )
    if not req_val and not solo_ret and not nota:
        if row:
            db.delete(row)
            db.commit()
        return {
            "requiere_validacion": False,
            "solo_retiro_local": False,
            "nota_restriccion": "",
        }

    if not row:
        row = RestriccionProducto(
            tenant_id=int(tenant_id),
            plato_id=int(plato_id),
        )
        db.add(row)
    row.requiere_validacion = req_val
    row.solo_retiro_local = solo_ret
    row.nota_restriccion = nota or None
    db.commit()
    return {
        "requiere_validacion": bool(row.requiere_validacion),
        "solo_retiro_local": bool(row.solo_retiro_local),
        "nota_restriccion": (row.nota_restriccion or "").strip(),
    }
