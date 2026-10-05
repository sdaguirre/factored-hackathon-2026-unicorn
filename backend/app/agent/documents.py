"""Documentos necesarios para avanzar con una solicitud: que se exige, que ya tiene el banco y que falta.

La lista y las condiciones salen de las reglas del agente (required_documents en policy/agent_rules.yaml), no del LLM. El chat no recibe
archivos: el cliente confirma que cuenta con cada documento y un asesor los verifica despues.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.data.repository import CustomerRepository


@dataclass
class DocumentPlan:
    required: list[str]
    on_file: list[str]
    need: list[str] = field(default_factory=list)       # exigidos y que el banco aun no tiene


def required_documents(rules: dict, product: str, income_declared: bool, household: bool = False) -> list[str]:
    cfg = rules["required_documents"]
    docs = list(cfg.get(product, cfg["personal_loan"]))
    if income_declared:
        docs += [d for d in cfg.get("if_income_declared", []) if d not in docs]
    if household:
        docs += [d for d in cfg.get("if_household_income", []) if d not in docs]
    return docs


def plan(rules: dict, repo: CustomerRepository, customer_id: str, product: str, income_declared: bool,
         household: bool = False) -> DocumentPlan:
    required = required_documents(rules, product, income_declared, household)
    have = repo.documents_on_file(customer_id)
    return DocumentPlan(required=required, on_file=[d for d in required if d in have],
                        need=[d for d in required if d not in have])
