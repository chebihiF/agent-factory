"""Totaux de tokens par agent et par modèle, sur 24 h et 7 jours glissants."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from dashboard import db

MAIN_THREAD_LABEL = "(fil principal)"
_GROUPS = {"agent": "agent_id", "model": "model"}
_QUERY = (
    "SELECT {col}, SUM(CASE WHEN ts >= ? THEN count ELSE 0 END), SUM(count) "
    "FROM token_usage WHERE ts >= ? GROUP BY {col} ORDER BY 3 DESC, 1"
)


@dataclass(frozen=True)
class Total:
    label: str
    last_24h: int
    last_7d: int


@dataclass(frozen=True)
class Report:
    by_agent: list[Total]
    by_model: list[Total]


@dataclass(frozen=True)
class Receipt:
    """Bilan des lots OTLP reçus depuis la création de la base (#184)."""

    last_at: str | None
    batches: int
    accepted: int
    rejected: int


def receipt(conn: sqlite3.Connection) -> Receipt:
    return Receipt(*db.otlp_receipt(conn))


def diagnosis(r: Receipt) -> str | None:
    """Pourquoi la page est vide et que faire ; None si des points ont été retenus."""
    if r.batches == 0:
        return (
            "Aucun envoi OTLP reçu. Vérifier sur le poste que Claude Code exporte ses métriques "
            "(CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_METRICS_EXPORTER=otlp, "
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/json, "
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://<dashboard>/v1/metrics, "
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS=Authorization=Bearer <jeton>), "
            "puis relancer Claude Code. Un jeton faux ou absent est refusé (401) avant tout "
            "enregistrement : consulter les journaux du serveur."
        )
    if r.accepted:
        return None
    if r.rejected:
        return (
            f"Des envois sont arrivés mais {r.rejected} points de tokens ont été rejetés "
            "(session.id, model ou type manquant ou invalide, ou temporalité cumulative). "
            "Vérifier OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta et "
            "OTEL_METRICS_INCLUDE_SESSION_ID=true."
        )
    return (
        "Des envois sont arrivés mais sans compteur claude_code.token.usage. "
        "Vérifier la version de Claude Code et OTEL_METRICS_EXPORTER=otlp."
    )


def _cutoff(now: datetime, delta: timedelta) -> str:
    return (now - delta).isoformat(timespec="microseconds")


def _group(conn: sqlite3.Connection, group: str, now: datetime) -> list[Total]:
    sql = _QUERY.format(col=_GROUPS[group])
    params = (_cutoff(now, timedelta(days=1)), _cutoff(now, timedelta(days=7)))
    return [
        Total(MAIN_THREAD_LABEL if label is None else label, int(day), int(week))
        for label, day, week in conn.execute(sql, params)
    ]


def totals(conn: sqlite3.Connection, now: datetime) -> Report:
    """Agent absent (`agent_id` NULL) : regroupé sous « (fil principal) »."""
    return Report(by_agent=_group(conn, "agent", now), by_model=_group(conn, "model", now))


def thousands(value: int) -> str:
    """1234567 -> « 1 234 567 » (espace fine insécable)."""
    return f"{value:,}".replace(",", " ")
