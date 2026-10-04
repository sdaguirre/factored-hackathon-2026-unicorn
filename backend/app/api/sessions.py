"""Alta de sesion y verificacion de identidad por preguntas de seguridad."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agent import templates
from app.api.schemas import (AuthState, QuestionOut, SessionCreate, SessionCreated, SessionInfo, VerifyRequest,
                             VerifyResponse)
from app.auth import kba
from app.core.security import make_session_token
from app.core.sessions import AUTHENTICATED, LOCKED, Session
from app.deps import AppState, get_state, require_api_key, require_session
from app.errors import ApiError

router = APIRouter(prefix="/v1", tags=["sessions"], dependencies=[Depends(require_api_key)])


def _challenge_for(state: AppState, session: Session) -> kba.Challenge:
    s = state.settings
    if session.candidate is not None:
        ch = kba.build_challenge(state.repo, session.candidate, n=s.auth_questions, lang=session.language, as_of=s.as_of_date)
        if ch is None:
            raise ApiError(409, "AUTH_UNAVAILABLE",
                           "No es posible verificar su identidad por este canal. Contacte a un asesor.")
        return ch
    return kba.decoy_challenge(state.repo, n=s.auth_questions, lang=session.language, as_of=s.as_of_date)


def _questions(ch: kba.Challenge) -> list[QuestionOut]:
    return [QuestionOut(**q) for q in ch.public()]


@router.post("/sessions", response_model=SessionCreated, status_code=201)
def create_session(body: SessionCreate, state: AppState = Depends(get_state)) -> SessionCreated:
    """Inicia una sesion y devuelve el reto de seguridad. La respuesta tiene la misma forma exista o no el documento."""
    doc = body.document_number.strip()
    key = state.lockout.key(doc)
    if state.lockout.is_locked(key):
        raise ApiError(429, "AUTH_LOCKED", "Demasiados intentos. Intente mas tarde o contacte a un asesor.")
    session = state.store.create(body.language)
    session.doc_key = key
    session.candidate = state.repo.find_by_document(doc)
    try:
        session.challenge = _challenge_for(state, session)
    except ApiError:
        state.store.delete(session.id)
        raise
    s = state.settings
    return SessionCreated(
        session_id=session.id,
        token=make_session_token(session.id, state.jwt_secret, s.session_ttl_minutes),
        expires_in_seconds=s.session_ttl_minutes * 60,
        auth=AuthState(attempts_left=s.auth_max_attempts, questions=_questions(session.challenge)),
    )


@router.post("/sessions/{session_id}/verify", response_model=VerifyResponse)
def verify(body: VerifyRequest, session: Session = Depends(require_session),
           state: AppState = Depends(get_state)) -> VerifyResponse:
    s = state.settings
    if session.state == AUTHENTICATED:
        return VerifyResponse(status="authenticated", attempts_left=s.auth_max_attempts - session.auth_attempts_used)
    if session.state == LOCKED or session.challenge is None:
        raise ApiError(403, "AUTH_LOCKED", "Verificacion bloqueada. Contacte a un asesor.")

    answers = {a.question_id: a.option_id for a in body.answers}
    ok = kba.verify(session.challenge, answers) and session.candidate is not None
    if ok:
        c = session.candidate
        session.customer_id, session.first_name, session.country = c.customer_id, c.first_name, c.country
        session.state, session.challenge = AUTHENTICATED, None
        state.lockout.register_success(session.doc_key)
        greeting = templates.render("welcome", session.language, {"first_name": c.first_name})
        return VerifyResponse(status="authenticated", attempts_left=s.auth_max_attempts - session.auth_attempts_used,
                              greeting=greeting, suggested_replies=templates.SUGGESTIONS["start"][session.language])

    session.auth_attempts_used += 1
    locked = state.lockout.register_failure(session.doc_key)
    left = max(s.auth_max_attempts - session.auth_attempts_used, 0)
    if locked or left == 0:
        session.state, session.challenge = LOCKED, None
        raise ApiError(403, "AUTH_LOCKED", "Verificacion fallida y bloqueada. Contacte a un asesor.")
    session.challenge = _challenge_for(state, session)  # nuevo reto con otras preguntas
    return VerifyResponse(status="failed", attempts_left=left, questions=_questions(session.challenge))


@router.get("/sessions/{session_id}", response_model=SessionInfo)
def session_info(session: Session = Depends(require_session)) -> SessionInfo:
    return SessionInfo(state=session.state, language=session.language,
                       handoff_ticket=session.handoff["ticket_id"] if session.handoff else None)


@router.delete("/sessions/{session_id}", status_code=204)
def close_session(session: Session = Depends(require_session), state: AppState = Depends(get_state)) -> None:
    state.store.delete(session.id)
