"""Summary production reports for farmers, cluster groups and cooperatives."""
from decimal import Decimal

SCOPES = [("cooperative", "Cooperative"), ("cluster", "Cluster group"), ("farmer", "Farmer")]


def _key(scope, b):
    f = b.farmer
    if scope == "farmer":
        return f.id, f"{f.member_number} – {f.full_name}", f.cooperative.name
    if scope == "cluster":
        if f.cluster_id:
            return f.cluster_id, f.cluster.name, f.cooperative.name
        return f"none-{f.cooperative_id}", "No cluster", f.cooperative.name
    return f.cooperative_id, f.cooperative.name, f.cooperative.district


def pct(a, b):
    return round(a * 100 / b, 1) if b else None


def summarise(scope, batch_qs):
    batches = (batch_qs.select_related("farmer__cooperative", "farmer__cluster")
               .prefetch_related("steps__photos", "bags__test"))
    groups = {}
    for b in batches:
        key, label, sub = _key(scope, b)
        g = groups.setdefault(key, {
            "label": label, "sub": sub, "batches": 0, "completed": 0,
            "wet": Decimal(0), "dry": Decimal(0), "wet_for_yield": Decimal(0),
            "bags": 0, "tested": 0, "approved": 0, "premium": 0,
            "moist_sum": 0.0, "cut_sum": 0.0, "finished": 0, "compliant": 0,
            "farmers": set(),
        })
        g["batches"] += 1
        g["farmers"].add(b.farmer_id)
        g["wet"] += b.wet_weight_kg or 0
        if b.status not in ("FERMENTING", "DRYING"):
            g["completed"] += 1
            g["finished"] += 1
            if b.is_compliant():
                g["compliant"] += 1
        if b.dry_weight_kg:
            g["dry"] += b.dry_weight_kg
            g["wet_for_yield"] += b.wet_weight_kg
        for bag in b.bags.all():
            g["bags"] += 1
            t = bag.get_test()
            if t:
                g["tested"] += 1
                g["moist_sum"] += float(t.moisture_pct)
                g["cut_sum"] += float(t.cut_defect_pct)
                g["approved"] += 1 if t.moisture_ok else 0
                g["premium"] += 1 if t.premium else 0

    rows = []
    for g in groups.values():
        g["farmer_count"] = len(g.pop("farmers"))
        g["yield_pct"] = pct(float(g["dry"]), float(g["wet_for_yield"]))
        g["approval_pct"] = pct(g["approved"], g["tested"])
        g["premium_pct"] = pct(g["premium"], g["tested"])
        g["avg_moisture"] = round(g["moist_sum"] / g["tested"], 1) if g["tested"] else None
        g["avg_cut"] = round(g["cut_sum"] / g["tested"], 1) if g["tested"] else None
        g["compliance_pct"] = pct(g["compliant"], g["finished"])
        rows.append(g)
    rows.sort(key=lambda r: (str(r["sub"]), r["label"]))
    return rows


def totals(rows):
    t = {k: sum(r[k] for r in rows) for k in
         ("batches", "completed", "bags", "tested", "approved", "premium", "farmer_count")}
    t["wet"] = sum((r["wet"] for r in rows), Decimal(0))
    t["dry"] = sum((r["dry"] for r in rows), Decimal(0))
    wy = sum((r["wet_for_yield"] for r in rows), Decimal(0))
    t["yield_pct"] = pct(float(t["dry"]), float(wy))
    t["approval_pct"] = pct(t["approved"], t["tested"])
    t["premium_pct"] = pct(t["premium"], t["tested"])
    return t
