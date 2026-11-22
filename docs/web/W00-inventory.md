# W00 — Inventory (IT Insight Web)

**Checked:** 2026-11-22, runtime probes on the current VPS checkout (UTC+07).
**Status:** web/API only. No production service, Nginx route or database change was made.

## Repository

| Item | Value |
| --- | --- |
| Project | `/home/womcloud/IT-Job-Data-Lakehouse` |
| Branch | `main` |
| HEAD at inventory | `846311c5a22bd1bbb1083dfb3e250442a60bbcd2` |
| Remote | `git@github.com:quanbao2603/IT-Job-Data-Lakehouse.git` |
| Web draft | `apps/gold-web/` (untracked at inventory, implemented from this spec) |

`apps/gold-web` did not exist in the tracked tree before this work; the Windows draft
(`C:\Users\ADMIN\...\lakehouse-ai-web`, branch `codex/ai-web-vps`) was **not** reachable
from the VPS, so the module was implemented here from the specification.

## Runtime resources

```text
Host           vm10221800 (160.236.192.170)
Kernel         Linux 5.15.0-173-generic
CPU            6 logical cores
RAM            9.7 GiB total, ~6.2 GiB available at probe
Disk           73 GiB total, 44 GiB available (41% used)
Python         /usr/bin/python3.10 (3.10.12) — web venv target
Node / npm     v22.23.4 / 10.9.9
PostgreSQL     127.0.0.1:5432, database lakehouse, reader role gold_reader
MinIO          127.0.0.1:9000 / 127.0.0.1:9001 (untouched)
Airflow        scheduler + webserver active; webserver on 0.0.0.0:8080 (untouched)
Nginx          only site `9router` enabled; **no `insights` location exists yet**
TLS            certificate location not inspected; no Nginx change attempted
Ports          8010 free before local run (web API bind target)
               8011 used only by a temporary local prefix proxy during verification
```

## Gold read verification (read-only)

Using the protected runtime environment already on the server; no credential printed:

```text
reader user                 gold_reader
database                    lakehouse
transaction                 REPEATABLE READ, READ ONLY
active Gold release         gold_20261119_silver_release_manual_silver_replay_bc10f45
Silver release              silver_release_manual_silver_replay_bc10f45
snapshot_date               2026-11-19
snapshot_cutoff_at          2026-11-19T21:36:19+07:00
gold_control.load_run       SUCCESS (latest run for the active release)
DQ rows / failures          32 / 0
snapshot rows               3.092 (3.092 distinct unified_job_id)
companies (non-UNKNOWN)     1.198
skills (non-UNKNOWN)        131
salary samples / median     926 / 20.000.000 VND midpoint
```

These are live observations at probe time, not fixtures and not hardcoded into the app.
The served snapshot is 3 days old relative to 2026-11-22, so the API and UI mark it
`stale=true` (`WEB_STALE_AFTER_DAYS=1`) instead of calling it fresh.

## Safety boundary

This task did **not**:

- alter PostgreSQL schema, data, roles or passwords;
- run crawler, Bronze, Silver or Gold write jobs;
- create or enable a systemd service;
- modify Nginx, DNS or TLS;
- expose port 8010/8011 publicly;
- integrate any AI model or paid API.
