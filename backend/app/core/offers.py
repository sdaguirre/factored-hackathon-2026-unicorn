"""Ofertas aceptadas en el chat: filas de gold credit_offers, escritas solo por la API (nunca por el LLM).

En el prototipo se agregan a un archivo JSONL local (una fila por linea) y scripts/sync_credit_offers.py las sube a
<gold>.credit_offers en Databricks con MERGE por offer_id. Una oferta puede escribirse dos veces (al aceptarse y al derivarse
con su ticket): la ultima fila de cada offer_id es la vigente. Asi el contenedor no necesita credenciales de Databricks.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.policy.engine import jsonable


class OfferStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def save(self, row: dict) -> None:
        line = json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

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
