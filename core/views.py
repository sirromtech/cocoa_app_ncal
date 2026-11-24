import csv
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from . import reports as rpt
from . import steps as stepdefs
from .forms import DepotTestForm, FarmerForm
from .models import Bag, Batch, Cluster, Cooperative, DepotTest, Farmer, ProcessStep
from .qr import qr_svg

staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_staff, login_url="login")


def _is_farmer_user(user):
    return user.is_authenticated and Farmer.objects.filter(user=user).exists()


class CocoaLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        nxt = self.get_redirect_url()
        if nxt:
            return nxt
        return reverse("dashboard") if self.request.user.is_staff else reverse("app_shell")


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect("dashboard" if request.user.is_staff else "app_shell")


# ---------------- Offline farmer app (PWA) ----------------

@login_required
@ensure_csrf_cookie
def app_shell(request):
    """Static shell – all farmer data is loaded from IndexedDB / the sync API,
    so this page works offline once cached by the service worker."""
    ctx = {
        "step_defs": {"SOLAR": stepdefs.steps_for("SOLAR"), "FERMENTRY": stepdefs.steps_for("FERMENTRY")},
        "app_config": {
            "org": settings.COCOA["ORG_SHORT"],
            "tolerance": settings.COCOA["FERMENT_TOLERANCE_HOURS"],
            "moisture_min": settings.COCOA["MOISTURE_MIN"],
            "moisture_max": settings.COCOA["MOISTURE_MAX"],
            "cut_max": settings.COCOA["CUT_TEST_PREMIUM_MAX"],
            "sources": Batch.SOURCE,
            "fermented_by": Batch.FERMENTED_BY,
            "drying": Batch.DRYING,
            "statuses": Batch.Status.choices,
        },
    }
    resp = render(request, "app/shell.html", ctx)
    resp["Cache-Control"] = "no-cache"
    return resp


def service_worker(request):
    resp = render(request, "sw.js", content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def offline(request):
    return render(request, "offline.html")


# ---------------- Staff: dashboard ----------------

def _overdue_steps(limit=15):
    now = timezone.now()
    tol = timedelta(hours=settings.COCOA["FERMENT_TOLERANCE_HOURS"])
    out = []
    qs = (ProcessStep.objects.filter(ended_at__isnull=True, started_at__isnull=False,
                                     batch__status__in=["FERMENTING", "DRYING"])
          .select_related("batch__farmer__cooperative"))
    for s in qs:
        due = s.started_at + timedelta(hours=s.definition["hours"]) + tol
        if now > due:
            out.append({"step": s, "late_hours": (now - due).total_seconds() / 3600})
    out.sort(key=lambda r: -r["late_hours"])
    return out[:limit]


@staff_required
def dashboard(request):
    qs = Batch.objects.all()
    tests = DepotTest.objects.all()
    n_tests = tests.count()
    agg = qs.aggregate(wet=Sum("wet_weight_kg"), dry=Sum("dry_weight_kg"))
    kpis = {
        "farmers": Farmer.objects.filter(active=True).count(),
        "active": qs.filter(status__in=["FERMENTING", "DRYING"]).count(),
        "awaiting": qs.filter(status="COMPLETED").count(),
        "accepted": qs.filter(status="ACCEPTED").count(),
        "wet": agg["wet"] or 0,
        "dry": agg["dry"] or 0,
        "approval_pct": rpt.pct(tests.filter(moisture_ok=True).count(), n_tests),
        "premium_pct": rpt.pct(tests.filter(premium=True).count(), n_tests),
        "tests": n_tests,
    }
    year_start = timezone.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    coop_rows = rpt.summarise("cooperative", qs.filter(started_at__gte=year_start))
    return render(request, "core/dashboard.html", {
        "kpis": kpis,
        "overdue": _overdue_steps(),
        "recent_batches": qs.select_related("farmer__cooperative", "farmer__cluster")[:10],
        "recent_tests": tests.select_related("bag__batch__farmer").order_by("-tested_at")[:8],
        "coop_rows": coop_rows,
        "nav": "dashboard",
    })


# ---------------- Staff: batches ----------------

@staff_required
def batch_list(request):
    qs = Batch.objects.select_related("farmer__cooperative", "farmer__cluster")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    coop = request.GET.get("coop", "")
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(farmer__member_number__icontains=q) |
                       Q(farmer__first_name__icontains=q) | Q(farmer__last_name__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if coop:
        qs = qs.filter(farmer__cooperative_id=coop)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/batch_list.html", {
        "page": page, "q": q, "status": status, "coop": coop, "params": params.urlencode(),
        "statuses": Batch.Status.choices, "coops": Cooperative.objects.all(), "nav": "batches",
    })


def _can_view_batch(user, batch):
    return user.is_authenticated and (user.is_staff or (batch.farmer.user_id == user.id))


def _batch_context(request, batch):
    return {
        "batch": batch,
        "rows": batch.compliance(),
        "bags": [(bag, bag.get_test()) for bag in batch.bags.all()],
        "verify_url": request.build_absolute_uri(reverse("verify", args=[batch.code])),
    }


def _get_batch(code):
    return get_object_or_404(
        Batch.objects.select_related("farmer__cooperative", "farmer__cluster")
        .prefetch_related("steps__photos", "bags__test"), code=code)


@login_required
def batch_detail(request, code):
    batch = _get_batch(code)
    if not _can_view_batch(request.user, batch):
        raise Http404
    ctx = _batch_context(request, batch)
    ctx["nav"] = "batches"
    return render(request, "core/batch_detail.html", ctx)


def batch_qr(request, code):
    batch = get_object_or_404(Batch, code=code)
    svg = qr_svg(request.build_absolute_uri(reverse("verify", args=[batch.code])))
    resp = HttpResponse(svg, content_type="image/svg+xml")
    resp["Cache-Control"] = "public, max-age=604800"
    return resp


@login_required
def batch_label(request, code):
    batch = _get_batch(code)
    if not _can_view_batch(request.user, batch):
        raise Http404
    per_bag = request.GET.get("bags") == "1"
    n = batch.bags.count() or batch.bag_count or 1
    labels = list(range(1, n + 1)) if per_bag else [None]
    return render(request, "core/batch_label.html", {"batch": batch, "labels": labels, "total": n})


def verify(request, code):
    """Public traceability page opened by scanning the batch QR code."""
    batch = _get_batch(code)
    return render(request, "core/verify.html", _batch_context(request, batch))


# ---------------- Staff: depot ----------------

def _extract_code(raw):
    raw = (raw or "").strip()
    if "/verify/" in raw:
        raw = raw.split("/verify/", 1)[1].strip("/").split("/")[0]
    return raw.upper()


@staff_required
def depot_home(request):
    raw = request.GET.get("code")
    if raw:
        code = _extract_code(raw)
        batch = Batch.objects.filter(code__iexact=code).first()
        if batch:
            return redirect("depot_batch", code=batch.code)
        messages.error(request, f"No batch found with code {code}. If the farmer was offline, ask them to sync first.")
    return render(request, "core/depot_home.html", {
        "awaiting": Batch.objects.filter(status="COMPLETED").select_related("farmer__cooperative")[:30],
        "received": Batch.objects.filter(status="RECEIVED").select_related("farmer__cooperative")[:30],
        "nav": "depot",
    })


@staff_required
def depot_batch(request, code):
    batch = _get_batch(code)
    if request.method == "POST" and request.POST.get("action") == "receive":
        weights = [w.strip() for w in request.POST.getlist("weight") if w.strip()]
        try:
            values = [float(w) for w in weights]
            if not values or any(v <= 0 or v > 200 for v in values):
                raise ValueError
        except ValueError:
            messages.error(request, "Enter a weight (kg) between 0 and 200 for every bag.")
            return redirect("depot_batch", code=code)
        with transaction.atomic():
            start = (batch.bags.aggregate(m=Max("bag_number"))["m"] or 0) + 1
            for i, w in enumerate(weights):
                Bag.objects.create(batch=batch, bag_number=start + i, weight_kg=w, received_by=request.user)
            if batch.status in ("FERMENTING", "DRYING", "COMPLETED"):
                batch.status = Batch.Status.RECEIVED
                batch.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{len(weights)} bag(s) received. Now record the moisture and cut tests.")
        return redirect("depot_batch", code=code)
    ctx = _batch_context(request, batch)
    ctx["nav"] = "depot"
    return render(request, "core/depot_batch.html", ctx)


@staff_required
def bag_test(request, bag_id):
    bag = get_object_or_404(Bag.objects.select_related("batch__farmer"), pk=bag_id)
    instance = bag.get_test()
    form = DepotTestForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.bag = bag
        t.tested_by = request.user
        t.tested_at = timezone.now()
        t.save()
        bag.batch.refresh_depot_status()
        messages.success(request, f"Bag {bag.bag_number}: {t.get_result_display()} "
                                  f"(moisture {t.moisture_pct}%, cut defects {t.cut_defect_pct}%).")
        return redirect("depot_batch", code=bag.batch.code)
    return render(request, "core/bag_test.html", {"bag": bag, "form": form, "nav": "depot"})


# ---------------- Staff: reports ----------------

def _parse_date(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@staff_required
def reports(request):
    scope = request.GET.get("scope", "cooperative")
    if scope not in dict(rpt.SCOPES):
        scope = "cooperative"
    today = timezone.localdate()
    d_from = _parse_date(request.GET.get("from")) or today.replace(month=1, day=1)
    d_to = _parse_date(request.GET.get("to")) or today
    coop = request.GET.get("coop", "")

    qs = Batch.objects.filter(started_at__date__gte=d_from, started_at__date__lte=d_to)
    if coop:
        qs = qs.filter(farmer__cooperative_id=coop)
    rows = rpt.summarise(scope, qs)
    totals = rpt.totals(rows)

    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="cocoa-{scope}-report-{d_from}-{d_to}.csv"'
        w = csv.writer(resp)
        w.writerow([dict(rpt.SCOPES)[scope], "Group", "Farmers", "Batches", "Completed", "Wet kg", "Dry kg",
                    "Yield %", "Bags tested", "Approved %", "Premium %", "Avg moisture %", "Avg cut defects %",
                    "Process compliance %"])
        for r in rows:
            w.writerow([r["label"], r["sub"], r["farmer_count"], r["batches"], r["completed"], r["wet"], r["dry"],
                        r["yield_pct"], r["tested"], r["approval_pct"], r["premium_pct"], r["avg_moisture"],
                        r["avg_cut"], r["compliance_pct"]])
        return resp

    params = request.GET.copy()
    params["format"] = "csv"
    return render(request, "core/reports.html", {
        "rows": rows, "totals": totals, "scope": scope, "scopes": rpt.SCOPES,
        "d_from": d_from, "d_to": d_to, "coop": coop, "coops": Cooperative.objects.all(),
        "csv_query": params.urlencode(), "nav": "reports",
    })


# ---------------- Staff: farmer registry ----------------

@staff_required
def farmer_list(request):
    qs = Farmer.objects.select_related("cooperative", "cluster").annotate(n_batches=Count("batches"))
    q = request.GET.get("q", "").strip()
    coop = request.GET.get("coop", "")
    if q:
        qs = qs.filter(Q(member_number__icontains=q) | Q(first_name__icontains=q) |
                       Q(last_name__icontains=q) | Q(village__icontains=q))
    if coop:
        qs = qs.filter(cooperative_id=coop)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/farmer_list.html", {
        "page": page, "q": q, "coop": coop, "coops": Cooperative.objects.all(),
        "params": params.urlencode(), "nav": "farmers",
    })


@staff_required
def farmer_create(request):
    form = FarmerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        farmer = form.save()
        messages.success(request, f"Farmer {farmer} registered. They can now log in with member number "
                                  f"{farmer.member_number} and their PIN.")
        return redirect("farmer_list")
    return render(request, "core/farmer_form.html", {
        "form": form, "clusters": Cluster.objects.select_related("cooperative"), "nav": "farmers",
    })
