#!/bin/bash
# Collecte les metriques COROS du jour (sommeil, HRV, RHR, fatigue) en JSON.
set -a; source /home/opc/mcp/secrets/coros.env; set +a
cd /home/opc/mcp/coros-mcp
exec .venv/bin/python - << "PY"
import asyncio, json, datetime
from coros_mcp.coros_api import try_auto_login, fetch_daily_records, fetch_sleep
async def main():
    auth = await try_auto_login()
    today = datetime.date.today()
    d0 = (today - datetime.timedelta(days=7)).strftime("%Y%m%d")
    d1 = today.strftime("%Y%m%d")
    daily = await fetch_daily_records(auth, d0, d1)
    sleep = await fetch_sleep(auth, d0, d1)
    out = {"today": d1,
      "daily": [{"date": r.date, "hrv": r.avg_sleep_hrv, "hrv_baseline": r.baseline,
                 "rhr": r.rhr, "tired_rate": r.tired_rate, "load_ratio": r.training_load_ratio,
                 "ati": r.ati, "cti": r.cti} for r in daily],
      "sleep": [{"date": r.date, "minutes": r.total_duration_minutes,
                 "deep": r.phases.deep_minutes, "awake": r.phases.awake_minutes,
                 "avg_hr": r.avg_hr} for r in sleep]}
    print(json.dumps(out, ensure_ascii=False))
asyncio.run(main())
PY
