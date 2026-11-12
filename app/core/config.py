"""
Configuración central del SaaS (Compatible con Neon.tech, Render, Docker y Desarrollo Local).
"""
import os
from functools import lru_cache
from typing import List
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Configuración tipada y centralizada de la plataforma SaaS."""

    def __init__(self) -> None:
        # 1. Entorno y Servidor
        self.APP_NAME: str = os.getenv("APP_NAME", "SaaS Bot de Pedidos WhatsApp")
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        self.PORT: int = int(os.getenv("PORT", "8000"))
        self.BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
        self.WHATSAPP_BOT_PUBLIC_NUMBER: str = os.getenv(
            "WHATSAPP_BOT_PUBLIC_NUMBER", "5215500000000"
        )

        # 2. Base de Datos (Neon.tech PostgreSQL Serverless o SQLite local)
        raw_db_url: str = os.getenv(
            "DATABASE_URL", "sqlite:///./saas_whatsapp_bot.db"
        ).strip()
        self.DATABASE_URL: str = self._normalize_database_url(raw_db_url)
        # Esquema aislado en PostgreSQL para no mezclar tablas si compartes el servidor Neon con otro proyecto (ej. bicu_db)
        self.DB_SCHEMA: str = os.getenv("DB_SCHEMA", "saas_whatsapp").strip()

        # 3. Seguridad ISO/IEC 27001 & OWASP ASVS
        self.SECRET_KEY: str = os.getenv(
            "SECRET_KEY",
            "dev-secret-key-cambiar-en-produccion-con-64-caracteres-minimo-2026",
        )
        self.ENCRYPTION_MASTER_KEY: str = os.getenv(
            "ENCRYPTION_MASTER_KEY",
            "dev-encryption-master-key-aes256-cambiar-en-produccion-2026",
        )
        self.WEBHOOK_HMAC_SECRET: str = os.getenv(
            "WEBHOOK_HMAC_SECRET",
            "dev-webhook-hmac-sha256-secret-cambiar-en-produccion-2026",
        )
        self.JWT_ALGORITHM: str = "HS256"
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
        )
        self.REFRESH_TOKEN_EXPIRE_DAYS: int = int(
            os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
        )
        self.COOKIE_SECURE: bool = (
            os.getenv("COOKIE_SECURE", "false").lower() == "true"
            or self.ENVIRONMENT == "production"
        )

        # Cuenta inicial SUPER_ADMIN (Dueño del SaaS)
        self.SUPERADMIN_EMAIL: str = os.getenv(
            "SUPERADMIN_EMAIL", "admin@tusaas.com"
        )
        self.SUPERADMIN_PASSWORD: str = os.getenv(
            "SUPERADMIN_PASSWORD", "AdminSaaS#2026!Seguro"
        )
        self.SUPERADMIN_NAME: str = os.getenv(
            "SUPERADMIN_NAME", "Administrador General SaaS"
        )

        # 4. Enrutador Multi-IA (Varias IAs Gratis + Opción Empresarial de Pago)
        priority_raw = os.getenv(
            "AI_PROVIDER_PRIORITY", "gemini,groq,openrouter,openai"
        )
        self.AI_PROVIDER_PRIORITY: List[str] = [
            p.strip().lower() for p in priority_raw.split(",") if p.strip()
        ]
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
        self.GROQ_MODEL: str = os.getenv(
            "GROQ_MODEL", "llama-3.3-70b-versatile"
        )
        self.OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
        self.OPENROUTER_MODEL: str = os.getenv(
            "OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"
        )
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
        self.OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        # 5. Conexión WhatsApp (Solo Nivel 1 QR y Nivel 2 Meta Cloud API)
        self.WHATSAPP_PROVIDER: str = os.getenv(
            "WHATSAPP_PROVIDER", "evolution_qr"
        )
        self.WHATSAPP_EVOLUTION_BASE_URL: str = os.getenv(
            "WHATSAPP_EVOLUTION_BASE_URL", "http://localhost:8080"
        )
        self.WHATSAPP_EVOLUTION_API_KEY: str = os.getenv(
            "WHATSAPP_EVOLUTION_API_KEY", ""
        )
        self.WHATSAPP_EVOLUTION_INSTANCE: str = os.getenv(
            "WHATSAPP_EVOLUTION_INSTANCE", "saas_bot_principal"
        )
        self.WHATSAPP_META_ACCESS_TOKEN: str = os.getenv(
            "WHATSAPP_META_ACCESS_TOKEN", ""
        )
        self.WHATSAPP_META_PHONE_NUMBER_ID: str = os.getenv(
            "WHATSAPP_META_PHONE_NUMBER_ID", ""
        )
        self.WHATSAPP_META_VERIFY_TOKEN: str = os.getenv(
            "WHATSAPP_META_VERIFY_TOKEN", "token_verificacion_meta_2026"
        )
        self.WHATSAPP_META_APP_SECRET: str = os.getenv(
            "WHATSAPP_META_APP_SECRET", ""
        )

        # 6. Métodos de Pago y Reservaciones de Mesa
        # "modo_1_directo" (Efectivo + Transferencia con comprobante)
        # "modo_2_pasarela_api" (Activa API de pagos y habilita Reservación de Mesas con anticipo)
        self.PAYMENT_OPERATION_MODE: str = os.getenv(
            "PAYMENT_OPERATION_MODE", "modo_1_directo"
        )
        self.ENABLE_TABLE_RESERVATIONS_WITH_PAYMENT: bool = (
            os.getenv("ENABLE_TABLE_RESERVATIONS_WITH_PAYMENT", "false").lower()
            == "true"
            or self.PAYMENT_OPERATION_MODE == "modo_2_pasarela_api"
        )

    @staticmethod
    def _normalize_database_url(url: str) -> str:
        """
        Adapta automáticamente cadenas de conexión de Neon.tech (postgres:// o postgresql://)
        al driver moderno psycopg v3 (postgresql+psycopg://) manteniendo sslmode=require.
        """
        if not url:
            return "sqlite:///./saas_whatsapp_bot.db"
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        # Si es una URL de Neon.tech y no incluye sslmode, agregar sslmode=require por seguridad ISO 27001
        if "neon.tech" in url and "sslmode=" not in url:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}sslmode=require"
        return url

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgresql")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

