# Gold PostgreSQL / Power BI connection

## PostgreSQL roles

Gold uses a dedicated database and roles:

- Database: `lakehouse`
- Writer: `gold_writer`
- Reader: `gold_reader`
- Bind: PostgreSQL listens on localhost; do not expose port 5432 publicly.

Create the database and roles as a PostgreSQL administrator:

```bash
sudo -u postgres GOLD_WRITER_PASSWORD='<writer-secret>' \
  GOLD_READER_PASSWORD='<reader-secret>' \
  ./infrastructure/postgres/create_gold_db.sh
```

Do not place the passwords in Git, this document, shell history, or Telegram.

## SSH tunnel for Power BI Desktop

From the workstation that runs Power BI Desktop:

```bash
ssh -N -L 5433:127.0.0.1:5432 <vps-user>@<vps-host>
```

Keep that terminal open while Power BI is connected.

Power BI PostgreSQL connection:

- Server: `127.0.0.1:5433`
- Database: `lakehouse`
- User: `gold_reader`
- SSL: follow the VPS PostgreSQL policy; do not weaken host authentication to make a connection pass.

Use the semantic views only:

- `gold.v_job_snapshot`
- `gold.v_job_platform`
- `gold.v_job_skill`
- `gold.v_change_event`
- `gold.v_market_daily`
- `gold.v_company_hiring`
- `gold.v_skill_pair`
- `gold.v_daily_coverage`

The views are filtered through `gold_control.active_release`, so Power BI does not mix releases. Do not connect dashboards directly to fact tables.

## Read-only verification

```sql
SELECT * FROM gold.v_active_release;
SELECT * FROM gold.v_daily_coverage ORDER BY snapshot_date DESC;
SELECT COUNT(*) FROM gold.v_job_snapshot;
```

`gold_reader` should have `USAGE` on `gold` and `gold_control`, and `SELECT` on their tables/views. It must not own or write the Gold objects.
