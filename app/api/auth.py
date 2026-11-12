"""
Endpoints de Autenticación y Seguridad (ISO/IEC 27001 & OWASP ASVS):
- Login con Argon2id + emisión de JWT en Cookie HttpOnly y JSON
- Verificación opcional de Doble Factor (2FA TOTP)
- Configuración de 2FA con código QR
- Cierre de sesión seguro y Registro de Auditoría (AuditLog)
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.audit import record_audit_log
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decrypt_sensitive_data,
    encrypt_sensitive_data,
    generate_qr_base64_data_uri,
    generate_totp_secret,
    verify_password,
    verify_totp_code,
)
from app.models.saas_models import Usuario

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación y Seguridad ISO/OWASP"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: Optional[str] = None


class Enable2FARequest(BaseModel):
    totp_code: str


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Autentica a SUPER_ADMIN, COMERCIO_ADMIN o SUCURSAL_STAFF y registra auditoría."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    user = (
        db.query(Usuario)
        .filter(Usuario.email == payload.email.lower().strip())
        .first()
    )

    if not user or not verify_password(payload.password, user.password_hash):
        record_audit_log(
            db=db,
            accion="LOGIN_FALLIDO",
            recurso_tipo="USUARIO",
            actor_email=payload.email,
            ip_origen=client_ip,
            detalles={"motivo": "Credenciales inválidas"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos.",
        )

    if not user.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta se encuentra suspendida.",
        )

    # Si tiene 2FA activado, exigir y validar el código TOTP
    if user.is_2fa_enabled and user.totp_secret_encrypted:
        if not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere código de doble factor (2FA).",
            )
        secret = decrypt_sensitive_data(user.totp_secret_encrypted)
        if not verify_totp_code(secret, payload.totp_code):
            record_audit_log(
                db=db,
                accion="LOGIN_2FA_FALLIDO",
                recurso_tipo="USUARIO",
                recurso_id=str(user.id),
                usuario_id=user.id,
                tenant_id=user.tenant_id,
                actor_email=user.email,
                ip_origen=client_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Código 2FA inválido.",
            )

    access_token = create_access_token(
        user_id=user.id,
        role=user.rol,
        tenant_id=user.tenant_id,
        sucursal_id=user.sucursal_id,
    )
    refresh_token = create_refresh_token(
        user_id=user.id,
        role=user.rol,
        tenant_id=user.tenant_id,
        sucursal_id=user.sucursal_id,
    )

    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    # Guardar cookie HttpOnly segura (OWASP ASVS Session Management)
    response.set_cookie(
        key="saas_access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    record_audit_log(
        db=db,
        accion="LOGIN_EXITOSO",
        recurso_tipo="USUARIO",
        recurso_id=str(user.id),
        usuario_id=user.id,
        tenant_id=user.tenant_id,
        actor_email=user.email,
        ip_origen=client_ip,
        detalles={"rol": user.rol, "sucursal_id": user.sucursal_id},
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "nombre_completo": user.nombre_completo,
            "email": user.email,
            "rol": user.rol,
            "tenant_id": user.tenant_id,
            "sucursal_id": user.sucursal_id,
            "is_2fa_enabled": user.is_2fa_enabled,
        },
    }


@router.get("/me")
def get_me(current_user: Usuario = Depends(get_current_user)):
    """Devuelve el perfil y contexto Multi-Tenant del usuario actual."""
    return {
        "id": current_user.id,
        "nombre_completo": current_user.nombre_completo,
        "email": current_user.email,
        "rol": current_user.rol,
        "tenant_id": current_user.tenant_id,
        "sucursal_id": current_user.sucursal_id,
        "is_2fa_enabled": current_user.is_2fa_enabled,
    }


@router.post("/2fa/setup")
def setup_2fa(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genera un secreto TOTP cifrado con AES-256 y devuelve su QR para Google Authenticator."""
    secret = generate_totp_secret()
    current_user.totp_secret_encrypted = encrypt_sensitive_data(secret)
    db.commit()

    import pyotp

    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.email, issuer_name=settings.APP_NAME
    )
    qr_data_uri = generate_qr_base64_data_uri(uri)
    return {
        "secret_manual": secret,
        "qr_data_uri": qr_data_uri,
        "mensaje": "Escanea el QR con Google Authenticator y confirma con /api/v1/auth/2fa/enable",
    }


@router.post("/2fa/enable")
def enable_2fa(
    payload: Enable2FARequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Activa formalmente el 2FA tras verificar el primer código de 6 dígitos."""
    if not current_user.totp_secret_encrypted:
        raise HTTPException(
            status_code=400, detail="Primero solicita /api/v1/auth/2fa/setup"
        )
    secret = decrypt_sensitive_data(current_user.totp_secret_encrypted)
    if not verify_totp_code(secret, payload.totp_code):
        raise HTTPException(status_code=400, detail="Código TOTP incorrecto.")

    current_user.is_2fa_enabled = True
    db.commit()

    record_audit_log(
        db=db,
        accion="2FA_ACTIVADO",
        recurso_tipo="USUARIO",
        recurso_id=str(current_user.id),
        usuario_id=current_user.id,
        tenant_id=current_user.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
    )
    return {"status": "ok", "is_2fa_enabled": True}


@router.post("/logout")
def logout(response: Response):
    """Elimina la cookie HttpOnly de sesión."""
    response.delete_cookie(key="saas_access_token", path="/")
    return {"status": "logged_out"}

