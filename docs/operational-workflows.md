# Operational workflows Bronze–Silver–Gold

## Bronze compaction

`bronze_compaction_daily` runs at 03:30 Asia/Ho_Chi_Minh. It selects one ingestion partition per source that is at least two days old, requires at least seven active files, streams and verifies the compacted object, records the compaction audit, archives the source files, verifies archive size, then removes only verified source copies from the active prefix.

Runtime knobs in `/home/womcloud/airflow/airflow.env`:

```text
LAKEHOUSE_COMPACTION_AGE_DAYS=2
LAKEHOUSE_COMPACTION_MIN_FILES=7
```

The DAG must be unpaused after deployment. A compaction failure is fail-closed and must not be treated as a successful Bronze run.

## Silver review queue

`silver_review_queue_daily` runs at 06:15 and rewrites `data/operational/pending_review.csv` from unresolved `silver_entity_links`. The SQLite registry now owns a durable `silver_review_decisions` audit table.

Manual decision command:

```bash
lakehouse_env/bin/python -m src.etl.review_queue \
  --state-db data/state_store.db \
  --queue data/operational/pending_review.csv \
  decide <observation_id> --decision ACCEPT \
  --reviewer <operator> --candidate-entity-id <unified_job_id> \
  --note 'verified by operator'
```

Use `REJECT` when the candidate is not the same entity. A decision is atomic, changes the effective observation/link status, and records reviewer, decision, candidate and timestamp. The queue is refreshed by the next scheduled sync.

## Gold automation

`LAKEHOUSE_GOLD_ENABLED=true` is required in the Airflow service environment. The `daily_gold_mart_build` DAG must be unpaused only when the latest Silver release is consumable. It performs its own release verification and publishes only after all blocking DQ checks pass.

The active release, load run, DQ rows and daily snapshot must be read back after the first automated run.

## Alerting

The failure/retry callbacks already write a redacted append-only ledger. Real delivery is implemented for Telegram and Discord, but cannot be enabled without a channel credential. Do not put either secret in Git, shell history or Telegram chat.

Required non-secret/secret runtime settings are:

```text
ALERT_ENABLED=true
ALERT_CHANNEL=telegram
- `TELEGRAM_BOT_TOKEN` (secret from the deployment secret store)
- `TELEGRAM_CHAT_ID` (approved destination)
```

Until the token and approved destination are present in the deployment secret store, leave `ALERT_ENABLED=false`. `alert_configuration_status()` reports readiness without exposing values. A redaction test is not evidence of delivery; after configuration, send one controlled test alert and verify the response from the channel.
