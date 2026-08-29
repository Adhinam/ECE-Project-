"""Anomaly detection rules — z-score helpers (legacy + shared)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnomalyCandidate:
    metric_name: str
    observed: float
    expected: float
    z_score: float
    severity: str


def z_score(observed: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return (observed - mean) / std


def detect_anomaly(
    *,
    metric_name: str,
    observed: float,
    mean: float,
    std: float,
    threshold: float = 2.5,
) -> AnomalyCandidate | None:
    """Return candidate if |z| exceeds threshold (severity WARN / CRITICAL)."""
    z = z_score(observed, mean, std)
    if abs(z) < threshold:
        return None
    severity = "CRITICAL" if abs(z) >= 3.5 else "WARN"
    return AnomalyCandidate(
        metric_name=metric_name,
        observed=observed,
        expected=mean,
        z_score=z,
        severity=severity,
    )
