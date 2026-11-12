"""
Servicio de Auditoría Inmutable (ISO/IEC 27001 A.12.4).
Registra cada evento crítico de seguridad y operación en la tabla 'audit_logs'.
"""
import json
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.saas_models import AuditLog


def record_audit_log(
    db: Session,
    accion: str,
    recurso_tipo: str,
    recurso_id: Optional[str] = None,
    usuario_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    actor_email: Optional[str] = None,
    ip_origen: Optional[str] = None,
    detalles: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """Guarda un evento de auditoría de seguridad en la base de datos."""
    log_entry = AuditLog(
        tenant_id=tenant_id,
        usuario_id=usuario_id,
        actor_email=actor_email,
        accion=accion,
        recurso_tipo=recurso_tipo,
        recurso_id=str(recurso_id) if recurso_id is not None else None,
        ip_origen=ip_origen or "127.0.0.1",
        detalles_json=json.dumps(detalles or {}, ensure_ascii=False),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry

