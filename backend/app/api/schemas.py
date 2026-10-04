"""Contrato publico de la API (OpenAPI en /docs). Es lo unico que necesita conocer un sitio o canal externo."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    document_number: str = Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9.\-]+$",
                                 description="Numero de documento. Por si solo NO autentica: se exige el reto de preguntas.")
    language: Literal["es", "pt"] = "es"


class OptionOut(BaseModel):
    id: str
    label: str


class QuestionOut(BaseModel):
    id: str
    text: str
    options: list[OptionOut]


class AuthState(BaseModel):
    status: Literal["challenge"] = "challenge"
    attempts_left: int
    questions: list[QuestionOut]


class SessionCreated(BaseModel):
    session_id: str
    token: str = Field(description="Enviar como 'Authorization: Bearer <token>' en las demas llamadas de la sesion.")
    expires_in_seconds: int
    auth: AuthState


class VerifyAnswer(BaseModel):
    question_id: str
    option_id: str


class VerifyRequest(BaseModel):
    answers: list[VerifyAnswer] = Field(min_length=1, max_length=10)


class VerifyResponse(BaseModel):
    status: Literal["authenticated", "failed"]
    attempts_left: int
    questions: list[QuestionOut] | None = Field(default=None, description="Nuevo reto cuando status=failed.")
    greeting: str | None = None
    suggested_replies: list[str] = []


class MessageRequest(BaseModel):
    message: str = Field(min_length=1)
    language: Literal["es", "pt"] | None = Field(default=None, description="Opcional; si falta se detecta.")


class EmailInfo(BaseModel):
    to: str | None = Field(default=None, description="Correo registrado, enmascarado (j***@dominio).")
    status: str = Field(description="`simulated_not_sent` en el prototipo: el correo se registra pero NO se envia.")
    id: str


class MessageResponse(BaseModel):
    reply: str
    language: Literal["es", "pt"]
    intent: str
    outcome: str | None = Field(default=None, description="Resultado de la politica o accion, si aplica.")
    awaiting: str | None = Field(default=None, description="Dato o confirmacion que el asistente espera.")
    suggested_replies: list[str] = []
    handoff_ticket: str | None = None
    proactive_offer: bool = Field(default=False, description="True si la respuesta incluye una oferta proactiva de credito.")
    summary_ready: bool = Field(default=False, description="True si la respuesta incluye el resumen final de la propuesta (hay PDF descargable).")
    email: EmailInfo | None = Field(default=None, description="Correo con el PDF del resumen (simulado en el prototipo).")
    trace_id: str


class SessionInfo(BaseModel):
    state: Literal["challenge", "authenticated", "locked"]
    language: Literal["es", "pt"]
    handoff_ticket: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    trace_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
