import json, time
from pathlib import Path
import CONAD_CAPODRISE_ENGINE as e

if not e.FULL_DB.exists():
    raise RuntimeError("Missing validated Conad full DB")

visual = e.apply_visual_verified_offers()
refs = e.apply_fixed_reference_prices()
app = e.build_app_db()
full = e.database_stats(e.FULL_DB)

audit = {}
if e.AUDIT.exists():
    try:
        audit = json.loads(e.AUDIT.read_text(encoding="utf-8"))
    except Exception:
        audit = {}

audit.update({
    "status": "OK_QUICK_OVERLAY",
    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "store_code": e.STORE_CODE,
    "store_name": e.STORE_NAME,
    "store_address": e.STORE_ADDRESS,
    "visual_verified_offers": visual,
    "fixed_reference_prices": refs,
    "full_db": full,
    "app_db": app,
    "quick_overlay_only": True,
})
e.AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({
    "status": audit["status"],
    "visual_inserted": len(visual.get("inserted", [])),
    "visual_expired": len(visual.get("expired", [])),
    "reference_fallbacks": full["reference_fallbacks"],
    "full_rows": full["rows"],
    "app_rows": app["rows"],
}, ensure_ascii=False))
