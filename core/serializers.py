"""Plain dict serialisers used by the offline sync API (no DRF dependency)."""


def iso(dt):
    return dt.isoformat() if dt else None


def num(d):
    return float(d) if d is not None else None


def test_dict(t):
    if not t:
        return None
    return {
        "moisture_pct": num(t.moisture_pct),
        "cut_defect_pct": num(t.cut_defect_pct),
        "moisture_ok": t.moisture_ok,
        "premium": t.premium,
        "result": t.result,
        "result_label": t.get_result_display(),
        "tested_at": iso(t.tested_at),
    }


def batch_dict(b):
    return {
        "id": str(b.id),
        "code": b.code,
        "bean_source": b.bean_source,
        "source_detail": b.source_detail,
        "fermented_by": b.fermented_by,
        "drying_method": b.drying_method,
        "wet_weight_kg": num(b.wet_weight_kg),
        "dry_weight_kg": num(b.dry_weight_kg),
        "bag_count": b.bag_count,
        "status": b.status,
        "started_at": iso(b.started_at),
        "completed_at": iso(b.completed_at),
        "notes": b.notes,
        "client_updated_at": iso(b.client_updated_at),
        "bags": [
            {"bag_number": bag.bag_number, "weight_kg": num(bag.weight_kg), "test": test_dict(bag.get_test())}
            for bag in b.bags.all()
        ],
    }


def step_dict(s):
    return {
        "id": str(s.id),
        "batch_id": str(s.batch_id),
        "step_type": s.step_type,
        "sequence": s.sequence,
        "started_at": iso(s.started_at),
        "ended_at": iso(s.ended_at),
        "weight_start_kg": num(s.weight_start_kg),
        "weight_end_kg": num(s.weight_end_kg),
        "latitude": num(s.latitude),
        "longitude": num(s.longitude),
        "notes": s.notes,
        "client_updated_at": iso(s.client_updated_at),
    }


def photo_dict(p):
    return {
        "id": str(p.id),
        "step_id": str(p.step_id),
        "kind": p.kind,
        "taken_at": iso(p.taken_at),
        "url": p.image.url,
    }
