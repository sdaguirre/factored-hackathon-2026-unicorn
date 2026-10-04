"""Resumen estructurado para el agente humano. Sin cadena de pensamiento del modelo: solo hechos verificados,
acciones ejecutadas y la evidencia del motor de politica."""
from __future__ import annotations

from app.core.sessions import Session

TRANSCRIPT_TAIL = 8


def build_summary(session: Session, ticket_id: str, created_at: str, reason: str, evaluation: dict | None,
                  open_questions: list[str]) -> dict:
    return {
        "ticket_id": ticket_id,
        "created_at": created_at,
        "reason": reason,
        "customer": {"customer_id": session.customer_id, "country": session.country, "language": session.language,
                     "authenticated": True, "auth_method": "kba_transactions_and_account_opening"},
        "request": session.slots.get("pending_request"),
        "declared_income_unverified": session.slots.get("declared_income"),
        "verified_facts": session.slots.get("verified_facts", []),
        "evaluation": evaluation,
        "actions_taken": list(session.actions),
        "open_questions": open_questions,
        "transcript_tail": session.history[-TRANSCRIPT_TAIL:],
    }
