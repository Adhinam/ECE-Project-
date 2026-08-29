# Event Type Reconciliation

## Context

During internal validation, we identified that queue-depth telemetry events could be represented differently between challenge-facing event schemas and internal storage representations.

## Resolution

The export layer was updated so that queue-depth telemetry is emitted using the correct challenge event type when exported.

## Scope

- No model retraining
- No tracking regeneration
- No inference reruns
- No detection output modifications
- Export-layer correction only

## Validation

Validation was performed using internal development data and database records.

The correction ensures that exported challenge-format events preserve the intended semantic meaning of queue-depth telemetry while leaving stored event records unchanged.

## Notes

Future ingestion pipelines should map queue-depth telemetry explicitly during ingestion to avoid requiring export-time reconciliation.
