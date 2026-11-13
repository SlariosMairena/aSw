"""
API de Pasarela de Pagos Digitales (Modo 2).
Incluye generación de enlaces de pago seguros y recepción de webhooks con firma HMAC.
"""
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any

from app.core.database import get_db
from app.models.saas_models import Pedido
from app.services.payment_service import simulated_gateway

router = APIRouter(prefix="/api/v1/pagos", tags=["Pagos Online"])


class WebhookPayload(BaseModel):
    session_id: str
    pedido_id: int
    resultado: str = Field(default="EXITOSO")  # EXITOSO | RECHAZADO
    demo_mode: bool = True
    mensaje_error: Optional[str] = None


@router.post("/checkout-link/{pedido_id}")
def generar_link_pago(
    pedido_id: int,
    db: Session = Depends(get_db)
):
    """Genera un link de pago único para que el cliente pague online por WhatsApp."""
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )

    session_data = simulated_gateway.create_checkout_session(db, pedido)
    return session_data


@router.post("/webhook-simulado")
def procesar_webhook_simulado(
    payload: WebhookPayload,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    db: Session = Depends(get_db)
):
    """
    Webhook que recibe la confirmación asíncrona de la pasarela de pagos.
    Verifica firma criptográfica y actualiza el pedido a PAGADO en tiempo real.
    """
    res = simulated_gateway.process_webhook(
        db=db,
        payload=payload.model_dump(),
        signature=x_signature or ""
    )

    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error", "Error procesando el pago")
        )

    return res

