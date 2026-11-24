"""Offline sync API used by the farmer PWA.

Every record carries a client-generated UUID, so pushing the same data twice
is harmless (idempotent). Conflicts are resolved last-write-wins using the
phone's `client_updated_at`. Once a batch reaches the depot it is locked
and farmer edits are ignored.
"""
import json
import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from . import steps as stepdefs
from .models import Batch, Farmer, ProcessStep, StepPhoto
from .serializers import batch_dict, photo_dict, step_dict

MAX_PHOTO_BYTES = 8 * 1024 * 1024


class BadData(Exception):
    pass


def api_farmer(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "auth"}, status=401)
        farmer = (Farmer.objects.select_related("cooperative", "cluster")
                  .filter(user=request.user, active=True).first())
        if not farmer:
            return JsonResponse({"error": "This account is not linked to a farmer."}, status=403)
        request.farmer = farmer
        return view(request, *args, **kwargs)
    return inner


# ---------- parsing helpers ----------

def p_uuid(v):
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise BadData(f"Invalid id: {v}")


def p_dt(v, required=False):
    if v in (None, ""):
        if required:
            raise BadData("Missing date")
        return None
    dt = parse_datetime(str(v))
    if dt is None:
        raise BadData(f"Invalid date: {v}")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def p_dec(v, lo=0, hi=100000, places="0.01"):
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        raise BadData(f"Invalid number: {v}")
    if d < lo or d > hi:
        raise BadData(f"Number out of range: {v}")
    return d


def p_choice(v, choices, default=None):
    keys = [c[0] for c in choices]
    if v in keys:
        return v
    if default is not None:
        return default
    raise BadData(f"Invalid choice: {v}")


# ---------- upserts ----------

def upsert_batch(farmer, data, user):
    bid = p_uuid(data.get("id"))
    cu = p_dt(data.get("client_updated_at"))
    obj = Batch.objects.filter(pk=bid).first()
    if obj:
        if obj.farmer_id != farmer.id:
            raise BadData("Batch belongs to another farmer")
        if obj.is_locked:
            return obj
        if obj.client_updated_at and cu and cu <= obj.client_updated_at:
            return obj  # server already has a newer copy
    else:
        obj = Batch(id=bid, farmer=farmer)
        code = str(data.get("code") or "").strip().upper()[:34] or f"{farmer.member_number}-{bid.hex[:6]}"
        if Batch.objects.filter(code=code).exists():
            code = f"{code}-{bid.hex[:4].upper()}"
        obj.code = code

    obj.bean_source = p_choice(data.get("bean_source"), Batch.SOURCE, "OWN")
    obj.source_detail = str(data.get("source_detail") or "")[:200]
    obj.fermented_by = p_choice(data.get("fermented_by"), Batch.FERMENTED_BY, "INDIVIDUAL")
    obj.drying_method = p_choice(data.get("drying_method"), Batch.DRYING, "SOLAR")
    wet = p_dec(data.get("wet_weight_kg"))
    if wet is None:
        raise BadData("Wet weight is required")
    obj.wet_weight_kg = wet
    obj.dry_weight_kg = p_dec(data.get("dry_weight_kg"))
    bc = data.get("bag_count")
    obj.bag_count = int(bc) if bc not in (None, "") else None
    status = data.get("status")
    if status in ("FERMENTING", "DRYING", "COMPLETED"):
        obj.status = status
    obj.started_at = p_dt(data.get("started_at"), required=True)
    obj.completed_at = p_dt(data.get("completed_at"))
    obj.notes = str(data.get("notes") or "")[:2000]
    obj.client_updated_at = cu or timezone.now()
    obj.save()
    return obj


def upsert_step(farmer, data, user):
    sid = p_uuid(data.get("id"))
    batch = Batch.objects.filter(pk=p_uuid(data.get("batch_id")), farmer=farmer).first()
    if not batch:
        raise BadData("Batch not found – sync the batch first")
    if batch.is_locked:
        return None
    valid = {d["code"]: d for d in batch.step_defs}
    step_type = data.get("step_type")
    if step_type not in valid:
        raise BadData(f"Step {step_type} is not part of this batch")
    cu = p_dt(data.get("client_updated_at"))

    obj = ProcessStep.objects.filter(pk=sid).first()
    if obj and obj.batch_id != batch.id:
        raise BadData("Step belongs to another batch")
    if not obj:
        # Same step recorded on two phones: merge on (batch, step_type)
        obj = ProcessStep.objects.filter(batch=batch, step_type=step_type).first() \
            or ProcessStep(id=sid, batch=batch, step_type=step_type)
    if obj.client_updated_at and cu and cu <= obj.client_updated_at:
        return obj

    obj.sequence = valid[step_type]["sequence"]
    obj.started_at = p_dt(data.get("started_at"))
    obj.ended_at = p_dt(data.get("ended_at"))
    if obj.started_at and obj.ended_at and obj.ended_at < obj.started_at:
        raise BadData("End time is before start time")
    obj.weight_start_kg = p_dec(data.get("weight_start_kg"))
    obj.weight_end_kg = p_dec(data.get("weight_end_kg"))
    obj.latitude = p_dec(data.get("latitude"), -90, 90, "0.000001")
    obj.longitude = p_dec(data.get("longitude"), -180, 180, "0.000001")
    obj.notes = str(data.get("notes") or "")[:1000]
    obj.recorded_by = user
    obj.client_updated_at = cu or timezone.now()
    obj.save()
    return obj


# ---------- endpoints ----------

@require_GET
@api_farmer
def bootstrap(request):
    f = request.farmer
    batches = list(f.batches.prefetch_related("steps__photos", "bags__test")[:300])
    steps, photos = [], []
    for b in batches:
        for s in b.steps.all():
            steps.append(step_dict(s))
            photos.extend(photo_dict(p) for p in s.photos.all())
    c = settings.COCOA
    return JsonResponse({
        "farmer": {
            "member_number": f.member_number,
            "name": f.full_name,
            "first_name": f.first_name,
            "phone": f.phone,
            "village": f.village,
            "cooperative": f.cooperative.name,
            "cooperative_code": f.cooperative.code,
            "cluster": f.cluster.name if f.cluster else "",
        },
        "batches": [batch_dict(b) for b in batches],
        "steps": steps,
        "photos": photos,
        "thresholds": {
            "moisture_min": c["MOISTURE_MIN"],
            "moisture_max": c["MOISTURE_MAX"],
            "cut_premium_max": c["CUT_TEST_PREMIUM_MAX"],
            "ferment_tolerance_hours": c["FERMENT_TOLERANCE_HOURS"],
        },
        "server_time": timezone.now().isoformat(),
    })


@require_POST
@api_farmer
def sync(request):
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    out = {"batches": {"ok": [], "failed": []}, "steps": {"ok": [], "failed": []}}
    for kind, fn in (("batches", upsert_batch), ("steps", upsert_step)):
        for item in payload.get(kind) or []:
            rid = str(item.get("id")) if isinstance(item, dict) else None
            try:
                with transaction.atomic():
                    fn(request.farmer, item, request.user)
                out[kind]["ok"].append(rid)
            except (BadData, IntegrityError, ValidationError, ValueError, TypeError, AttributeError) as e:
                out[kind]["failed"].append({"id": rid, "error": str(e)})
    return JsonResponse(out)


@require_POST
@api_farmer
def upload_photo(request):
    try:
        pid = p_uuid(request.POST.get("id"))
    except BadData as e:
        return JsonResponse({"error": str(e)}, status=400)

    existing = StepPhoto.objects.filter(pk=pid).first()
    if existing:
        return JsonResponse(photo_dict(existing))

    step = None
    try:
        step = ProcessStep.objects.filter(pk=p_uuid(request.POST.get("step_id")),
                                          batch__farmer=request.farmer).first()
        if not step:
            step = ProcessStep.objects.filter(batch_id=p_uuid(request.POST.get("batch_id")),
                                              batch__farmer=request.farmer,
                                              step_type=request.POST.get("step_type")).first()
    except BadData:
        pass
    if not step:
        return JsonResponse({"error": "Step not synced yet"}, status=409)

    f = request.FILES.get("image")
    if not f:
        return JsonResponse({"error": "No image"}, status=400)
    if f.size > MAX_PHOTO_BYTES:
        return JsonResponse({"error": "Image too large"}, status=400)
    try:
        from PIL import Image
        Image.open(f).verify()
        f.seek(0)
    except Exception:
        return JsonResponse({"error": "Not a valid image"}, status=400)

    try:
        taken = p_dt(request.POST.get("taken_at")) or timezone.now()
    except BadData:
        taken = timezone.now()
    photo = StepPhoto(id=pid, step=step, taken_at=taken,
                      kind=p_choice(request.POST.get("kind"), StepPhoto.KIND, "END"))
    photo.image.save(f"{step.batch.code}-{step.step_type}-{pid.hex[:8]}.jpg", f, save=True)
    return JsonResponse(photo_dict(photo), status=201)
