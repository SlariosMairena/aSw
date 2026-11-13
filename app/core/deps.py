"""
Dependencias de Autenticación y Aislamiento Multi-Tenant / Multi-Sucursal (OWASP A01).
"""
from typing import Optional
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_jwt_token
from app.models.saas_models import TenantComercio, Usuario


def _extract_token(
    authorization: Optional[str] = Header(default=None),
    saas_access_token: Optional[str] = Cookie(default=None),
) -> Optional[str]:
    """Extrae el JWT desde la cookie HttpOnly o desde el header Authorization Bearer."""
    if saas_access_token:
        return saas_access_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(_extract_token),
) -> Usuario:
    """Valida el JWT y devuelve el usuario autenticado activo."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado. Inicia sesión para continuar.",
        )
    payload = decode_jwt_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión expirada o token inválido.",
        )

    user_id = payload.get("sub")
    user = (
        db.query(Usuario)
        .filter(Usuario.id == int(user_id), Usuario.activo.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inexistente o desactivado.",
        )

    if user.rol != "SUPER_ADMIN" and user.tenant_id:
        comercio = (
            db.query(TenantComercio)
            .filter(TenantComercio.id == user.tenant_id)
            .first()
        )
        if not comercio or not comercio.activo or comercio.estado_suscripcion == "SUSPENDIDO":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu comercio se encuentra suspendido o inactivo. Contacta al administrador del SaaS.",
            )
    return user


def require_super_admin(current_user: Usuario = Depends(get_current_user)) -> Usuario:
    """Garantiza que solo tú (SUPER_ADMIN, dueño del SaaS) puedas ejecutar esta acción."""
    if current_user.rol != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Se requiere rol SUPER_ADMIN.",
        )
    return current_user


def require_comercio_admin(current_user: Usuario = Depends(get_current_user)) -> Usuario:
    """Garantiza que solo el Dueño/Admin del comercio (COMERCIO_ADMIN) o SUPER_ADMIN ejecute acciones administrativas."""
    if current_user.rol not in ("COMERCIO_ADMIN", "SUPER_ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Acción exclusiva para el Administrador/Dueño del comercio.",
        )
    return current_user


def verify_tenant_isolation(current_user: Usuario, target_tenant_id: int) -> None:
    """
    Control estricto OWASP A01 / ISO 27001:
    Impide que el Restaurante A consulte o modifique pedidos, chats o menú del Restaurante B.
    """
    if current_user.rol == "SUPER_ADMIN":
        return
    if current_user.tenant_id != target_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Aislamiento Multi-Tenant: No tienes permiso para acceder a otro comercio.",
        )

