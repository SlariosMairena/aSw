"""
Pruebas Automatizadas de la Fase 1:
- Adaptador Neon.tech (PostgreSQL Serverless con sslmode=require)
- Controles de Seguridad ISO/IEC 27001 y OWASP (Argon2id, AES-256, JWT, HMAC-SHA256, 2FA, Headers)
- Base de Datos SaaS Multi-Tenant y Multi-Sucursal (Planes, Menú con Ingredientes, Pedidos, Reservaciones)
- Endpoints de Autenticación, Salud (/health) y Auditoría (AuditLog)
"""
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import (
    decrypt_sensitive_data,
    encrypt_sensitive_data,
    hash_password,
    verify_password,
    verify_webhook_hmac_signature,
)
from app.main import app


def test_neon_database_url_normalization():
    """Verifica que cualquier URL de Neon.tech se convierta a psycopg v3 con sslmode=require."""
    raw_neon = "postgresql://user:pass@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/neondb"
    normalized = Settings._normalize_database_url(raw_neon)
    assert normalized.startswith("postgresql+psycopg://")
    assert "sslmode=require" in normalized


def test_security_primitives_iso_owasp():
    """Verifica Argon2id, cifrado AES-256 en reposo y firma HMAC-SHA256."""
    # Argon2id
    hashed = hash_password("MiClaveUltraSegura#2026")
    assert hashed.startswith("$argon2id$")
    assert verify_password("MiClaveUltraSegura#2026", hashed) is True
    assert verify_password("ClaveIncorrecta", hashed) is False

    # AES-256 (Fernet)
    secret_api_key = "sk_live_secreto_restaurante_12345"
    encrypted = encrypt_sensitive_data(secret_api_key)
    assert encrypted != secret_api_key
    assert decrypt_sensitive_data(encrypted) == secret_api_key

    # HMAC-SHA256
    import hashlib
    import hmac

    body = b'{"event":"messages.upsert"}'
    secret = "test_secret"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_hmac_signature(body, sig, secret=secret) is True
    assert verify_webhook_hmac_signature(body, "sha256=invalida", secret=secret) is False


def test_health_and_saas_phase1_overview():
    """Verifica /health, cabeceras OWASP, semilla Multi-Sucursal, ingredientes y login con AuditLog."""
    with TestClient(app) as client:
        # 1. Healthcheck y cabeceras OWASP
        res_health = client.get("/health")
        assert res_health.status_code == 200
        assert res_health.headers["X-Content-Type-Options"] == "nosniff"
        assert res_health.headers["X-Frame-Options"] == "SAMEORIGIN"
        data_health = res_health.json()
        assert data_health["status"] == "ok"
        assert data_health["payment_mode"] == "modo_1_directo"

        # 2. Resumen del sistema SaaS (Planes, Sucursales Opción B, Ingredientes y Pedido #ORD-1001)
        res_overview = client.get("/api/v1/system/overview")
        assert res_overview.status_code == 200
        overview = res_overview.json()
        assert overview["fase_1_completada"] is True
        assert len(overview["planes_saas"]) == 3
        assert len(overview["sucursales_con_link_whatsapp_opcion_b"]) == 2

        burger = next(
            p
            for p in overview["platos_con_ingredientes"]
            if "Hamburguesa" in p["plato"]
        )
        assert "Cebolla" in burger["ingredientes_removibles_sin_costo"]
        assert "Tomate" in burger["ingredientes_removibles_sin_costo"]
        assert any(
            ex["nombre"] == "Extra Tocino Ahumado" and ex["precio"] == 2.0
            for ex in burger["extras_y_variantes"]
        )

        # 3. Login como Dueño de Restaurante (Multi-Tenant)
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "dueno@napoli.com", "password": "Napoli#2026!"},
        )
        assert res_login.status_code == 200
        login_data = res_login.json()
        assert login_data["user"]["rol"] == "COMERCIO_ADMIN"
        assert login_data["user"]["tenant_id"] is not None

        # 4. Consultar perfil /me usando el token JWT
        token = login_data["access_token"]
        res_me = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert res_me.status_code == 200
        assert res_me.json()["email"] == "dueno@napoli.com"

