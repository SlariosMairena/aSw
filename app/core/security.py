"""
Motor de Seguridad Empresarial (ISO/IEC 27001, ISO 27701 y OWASP ASVS / Top 10).
Incluye:
- Hashing de contraseñas con Argon2id (OWASP Top Recommendation)
- Tokens JWT firmados (Access + Refresh) con aislamiento de Tenant y Sucursal
- Cifrado simétrico AES-256 (Fernet + PBKDF2-HMAC-SHA256) para secretos en reposo
- Validación criptográfica HMAC-SHA256 en tiempo constante para Webhooks
- Autenticación de Doble Factor (2FA TOTP) y Generador de Códigos QR para Comercios/Sucursales
"""
import base64
import hashlib
import hmac
import io
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings

# =====================================================================
# 1. HASHING DE CONTRASEÑAS CON ARGON2ID (OWASP ASVS v4.0.3 - 2.4.1)
# =====================================================================
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain_password: str) -> str:
    """Genera un hash Argon2id seguro para almacenar en la base de datos."""
    return _ph.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verifica una contraseña contra su hash Argon2id en tiempo constante."""
    try:
        return _ph.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# =====================================================================
# 2. CIFRADO EN REPOSO AES-256 / FERNET (ISO/IEC 27001 A.10.1)
# =====================================================================
def _get_fernet_cipher() -> Fernet:
    """Deriva una llave criptográfica de 32 bytes desde ENCRYPTION_MASTER_KEY."""
    salt = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()[:16]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(
        kdf.derive(settings.ENCRYPTION_MASTER_KEY.encode("utf-8"))
    )
    return Fernet(key)


def encrypt_sensitive_data(plain_text: str) -> str:
    """Cifra tokens, API keys o secretos TOTP antes de guardarlos en la BD."""
    if not plain_text:
        return ""
    cipher = _get_fernet_cipher()
    return cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_sensitive_data(encrypted_text: str) -> str:
    """Descifra datos sensibles almacenados en la BD."""
    if not encrypted_text:
        return ""
    cipher = _get_fernet_cipher()
    try:
        return cipher.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


# =====================================================================
# 3. TOKENS JWT CON AISLAMIENTO MULTI-TENANT Y MULTI-SUCURSAL
# =====================================================================
def create_access_token(
    user_id: int,
    role: str,
    tenant_id: Optional[int] = None,
    sucursal_id: Optional[int] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Crea un JWT firmado que incluye el contexto de aislamiento Multi-Tenant."""
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "tenant_id": tenant_id,
        "sucursal_id": sucursal_id,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    user_id: int,
    role: str,
    tenant_id: Optional[int] = None,
    sucursal_id: Optional[int] = None,
) -> str:
    """Crea un Refresh Token de mayor duración."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "tenant_id": tenant_id,
        "sucursal_id": sucursal_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodifica y valida un token JWT. Retorna None si expiró o fue alterado."""
    try:
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None


# =====================================================================
# 4. FIRMAS CRIPTOGRÁFICAS HMAC-SHA256 PARA WEBHOOKS (WhatsApp / Pagos)
# =====================================================================
def verify_webhook_hmac_signature(
    payload_bytes: bytes, signature_header: str, secret: Optional[str] = None
) -> bool:
    """
    Verifica en tiempo constante que un webhook provenga legítimamente de Meta o Pasarela.
    Soporta formato 'sha256=<hex>' o '<hex>'.
    """
    if not signature_header:
        return False
    signing_secret = (secret or settings.WEBHOOK_HMAC_SECRET).encode("utf-8")
    expected_mac = hmac.new(
        signing_secret, msg=payload_bytes, digestmod=hashlib.sha256
    ).hexdigest()
    clean_sig = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected_mac, clean_sig)


# =====================================================================
# 5. DOBLE FACTOR (2FA TOTP) Y GENERADOR DE QR PARA COMERCIOS (OPCIÓN B)
# =====================================================================
def generate_totp_secret() -> str:
    """Genera una clave secreta Base32 para Google Authenticator / Authy."""
    return pyotp.random_base32()


def verify_totp_code(secret: str, code: str) -> bool:
    """Valida un código de 6 dígitos de 2FA con ventana de tolerancia de 30s."""
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code.strip(), valid_window=1)


def generate_qr_base64_data_uri(data_url: str) -> str:
    """
    Genera una imagen QR en formato Data URI (PNG Base64) lista para mostrar o descargar
    en el Panel Web (tanto para el Link Opción B de cada Restaurante/Sucursal como para 2FA).
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def build_whatsapp_branch_deep_link(
    bot_phone_number: str, branch_code: str, comercio_nombre: str
) -> str:
    """
    Construye el Deep Link de la Opción B para un comercio/sucursal hacia tu único WhatsApp.
    Ejemplo: https://wa.me/5215500000000?text=Hola%20quiero%20pedir%20en%20%5BNapoli-Centro%5D
    """
    from urllib.parse import quote

    clean_phone = "".join(ch for ch in bot_phone_number if ch.isdigit())
    welcome_text = f"Hola! Quiero hacer un pedido en {comercio_nombre} [{branch_code}]"
    return f"https://wa.me/{clean_phone}?text={quote(welcome_text)}"


def generate_csrf_token() -> str:
    """Genera un token aleatorio seguro contra ataques CSRF."""
    return secrets.token_urlsafe(32)

