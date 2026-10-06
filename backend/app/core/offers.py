"""Ofertas aceptadas en el chat: filas de gold credit_offers, escritas solo por la API (nunca por el LLM).

Cada fila se agrega primero a un archivo JSONL local (una fila por linea, el respaldo) y, si hay `sink`, se escribe tambien
en <gold>.credit_offers en Databricks con MERGE por offer_id (app/data/databricks_repository.py). Si esa escritura falla
la oferta no se pierde: queda en el JSONL y scripts/sync_credit_offers.py la sube despues. Una oferta puede escribirse dos
veces (al aceptarse y al derivarse con su ticket): la ultima fila de cada offer_id es la vigente.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from app.policy.engine import jsonable

logger = logging.getLogger("chat.offers")


class OfferStore:
    def __init__(self, path: Path, sink: Callable[[dict], None] | None = None):
        self.path = path
        self.sink = sink
        self._lock = threading.Lock()

    def save(self, row: dict) -> None:
        clean = jsonable(row)
        line = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.sink is not None:
            try:
                self.sink(clean)
            except Exception as exc:    # el JSONL ya la guardo; no se le niega la oferta al cliente por esto
                logger.warning("offer_sync_failed",
                               extra={"fields": {"offer_id": clean.get("offer_id"), "error": type(exc).__name__}})

    def latest(self) -> dict[str, dict]:
        """offer_id -> ultima fila escrita."""
        if not self.path.exists():
            return {}
        out: dict[str, dict] = {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    out[row["offer_id"]] = row
        return out
