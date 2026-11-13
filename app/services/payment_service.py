"""
Arquitectura Modular de Pasarelas de Pago (Modo 2).
Diseñada bajo el patrón Provider / Adapter para conectar fácilmente
Stripe, Pagadito, BAC Credomatic E-Commerce o el Simulador de Pruebas.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import uuid
import hmac
import hashlib
import time
import json
from sqlalchemy.orm import Session

from app.models.saas_models import Pedido, AuditLog, PagoTransaccion
from app.core.config import settings


class BasePaymentGateway(ABC):
    """Clase base abstracta para cualquier pasarela de pago."""

    @abstractmethod
    def create_checkout_session(self, db: Session, pedido: Pedido) -> Dict[str, Any]:
        """Genera una sesión de cobro o link de pago para el pedido."""
        pass

    @abstractmethod
    def process_webhook(self, db: Session, payload: Dict[str, Any], signature: str) -> Dict[str, Any]:
        """Procesa y valida la notificación asíncrona de pago (Webhook con firma)."""
        pass


class SimulatedPaymentGateway(BasePaymentGateway):
    """
    Pasarela de Pago Simulada (Sandbox / Demo Mode).
    Permite probar todo el flujo de cobro con tarjeta, links de pago y webhooks
    sin costo ni contratos bancarios previos.
    """

    GATEWAY_NAME = "SIMULADOR_SANDBOX"

    def create_checkout_session(self, db: Session, pedido: Pedido) -> Dict[str, Any]:
        session_id = f"sim_sess_{uuid.uuid4().hex[:12]}"
        checkout_url = f"/pagos/checkout/{session_id}?pedido_id={pedido.id}"

        # Enlazar o actualizar registro en la tabla pagos_transacciones
        trans = db.query(PagoTransaccion).filter(PagoTransaccion.pedido_id == pedido.id).first()
        if not trans:
            trans = PagoTransaccion(
                tenant_id=pedido.tenant_id,
                pedido_id=pedido.id,
                modo_operacion="MODO_2_API",
                metodo="PASARELA_SIMULADA",
                monto=pedido.total,
                moneda="NIO",
                estado="PENDIENTE",
                proveedor_api=self.GATEWAY_NAME,
                referencia_externa_api=session_id,
                url_link_pago=checkout_url
            )
            db.add(trans)
        else:
            trans.referencia_externa_api = session_id
            trans.url_link_pago = checkout_url
            trans.estado = "PENDIENTE"
            trans.proveedor_api = self.GATEWAY_NAME
        
        db.commit()

        return {
            "gateway": self.GATEWAY_NAME,
            "session_id": session_id,
            "checkout_url": checkout_url,
            "monto": pedido.total,
            "moneda": "NIO",
            "pedido_folio": pedido.folio,
            "estado": "PENDIENTE_PAGO"
        }

    def process_webhook(self, db: Session, payload: Dict[str, Any], signature: str) -> Dict[str, Any]:
        # Verificación criptográfica de firma HMAC simulada
        secret = settings.SECRET_KEY.encode("utf-8")
        token_to_verify = payload.get("session_id", "").encode("utf-8")
        expected_sig = hmac.new(secret, token_to_verify, hashlib.sha256).hexdigest()

        # En sandbox permitimos validación directa si la firma coincide o se envía modo demo
        is_valid = (signature == expected_sig) or (payload.get("demo_mode") is True)
        if not is_valid:
            return {"success": False, "error": "Firma criptográfica inválida (Webhook no autorizado)"}

        pedido_id = payload.get("pedido_id")
        resultado = payload.get("resultado", "EXITOSO")  # EXITOSO | RECHAZADO

        pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
        if not pedido:
            return {"success": False, "error": "Pedido no encontrado"}

        tx_code = f"TX-SIM-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"

        trans = db.query(PagoTransaccion).filter(PagoTransaccion.pedido_id == pedido.id).first()
        if not trans:
            trans = PagoTransaccion(
                tenant_id=pedido.tenant_id,
                pedido_id=pedido.id,
                modo_operacion="MODO_2_API",
                metodo="TARJETA_SIMULADA",
                monto=pedido.total,
                moneda="NIO"
            )
            db.add(trans)

        if resultado == "EXITOSO":
            pedido.estado_pago = "PAGADO"
            pedido.metodo_pago = "PASARELA_DIGITAL_SIMULADA"

            trans.estado = "APROBADO"
            trans.referencia_externa_api = tx_code
            trans.webhook_payload_json = json.dumps(payload)

            # Registrar en auditoría ISO
            log = AuditLog(
                tenant_id=pedido.tenant_id,
                actor_email="webhook@pasarela-simulador.com",
                accion="PAGO_ONLINE_APROBADO",
                recurso_tipo="pedidos",
                recurso_id=str(pedido.id),
                detalles_json=json.dumps({
                    "monto": pedido.total,
                    "tx_id": tx_code,
                    "metodo": "Tarjeta Simulada (Aprobada)"
                })
            )
            db.add(log)
            db.commit()
            db.refresh(pedido)

            return {
                "success": True,
                "estado_pago": "PAGADO",
                "tx_id": tx_code,
                "mensaje": "Pago procesado y comanda confirmada exitosamente"
            }
        else:
            pedido.estado_pago = "RECHAZADO"
            trans.estado = "RECHAZADO"
            trans.webhook_payload_json = json.dumps(payload)
            db.commit()
            return {
                "success": False,
                "estado_pago": "RECHAZADO",
                "error": payload.get("mensaje_error", "Fondos insuficientes o tarjeta rechazada")
            }


# Instancia por defecto del servicio de pagos
simulated_gateway = SimulatedPaymentGateway()
