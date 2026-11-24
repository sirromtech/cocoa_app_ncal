import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import steps as stepdefs


def rule(key):
    return settings.COCOA[key]


class Cooperative(models.Model):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=12, unique=True, help_text="Short code, e.g. WSC")
    district = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Cluster(models.Model):
    """A cluster group (hamlet) inside a cooperative society."""
    cooperative = models.ForeignKey(Cooperative, on_delete=models.CASCADE, related_name="clusters")
    name = models.CharField(max_length=120)
    hamlet = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["cooperative__name", "name"]
        unique_together = [("cooperative", "name")]

    def __str__(self):
        return f"{self.name} ({self.cooperative.code})"


class Farmer(models.Model):
    GENDER = [("M", "Male"), ("F", "Female")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="farmer")
    member_number = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    gender = models.CharField(max_length=1, choices=GENDER, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    village = models.CharField(max_length=120, blank=True)
    cooperative = models.ForeignKey(Cooperative, on_delete=models.PROTECT, related_name="farmers")
    cluster = models.ForeignKey(Cluster, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="farmers")
    active = models.BooleanField(default=True)
    joined_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["member_number"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return f"{self.member_number} – {self.full_name}"


class Batch(models.Model):
    """One complete fermentation + drying cycle. Gets its own QR code."""

    class Status(models.TextChoices):
        FERMENTING = "FERMENTING", "Fermenting"
        DRYING = "DRYING", "Drying"
        COMPLETED = "COMPLETED", "Packed – awaiting depot"
        RECEIVED = "RECEIVED", "Received at depot"
        ACCEPTED = "ACCEPTED", "Accepted for export"
        REJECTED = "REJECTED", "Not accepted"

    LOCKED = {"RECEIVED", "ACCEPTED", "REJECTED"}  # farmer can no longer edit

    SOURCE = [
        ("OWN", "Own garden"),
        ("PURCHASED", "Purchased wet beans"),
        ("CONTRIBUTION", "Cluster / group contribution"),
    ]
    FERMENTED_BY = [
        ("INDIVIDUAL", "Individual farmer"),
        ("CLUSTER", "Cluster group"),
        ("SOCIETY", "Cooperative society members"),
    ]
    DRYING = [
        ("SOLAR", "Solar drier (3 days)"),
        ("FERMENTRY", "Fermentry + solar drier"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True)
    farmer = models.ForeignKey(Farmer, on_delete=models.PROTECT, related_name="batches")
    bean_source = models.CharField(max_length=12, choices=SOURCE, default="OWN")
    source_detail = models.CharField(max_length=200, blank=True,
                                     help_text="Seller name or contributing members")
    fermented_by = models.CharField(max_length=12, choices=FERMENTED_BY, default="INDIVIDUAL")
    drying_method = models.CharField(max_length=10, choices=DRYING, default="SOLAR")
    wet_weight_kg = models.DecimalField(max_digits=9, decimal_places=2)
    dry_weight_kg = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    bag_count = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.FERMENTING)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    client_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name_plural = "batches"

    def __str__(self):
        return self.code

    @property
    def step_defs(self):
        return stepdefs.steps_for(self.drying_method)

    @property
    def yield_pct(self):
        if self.dry_weight_kg and self.wet_weight_kg:
            return float(self.dry_weight_kg) / float(self.wet_weight_kg) * 100
        return None

    @property
    def is_locked(self):
        return self.status in self.LOCKED

    def steps_by_type(self):
        return {s.step_type: s for s in self.steps.all()}

    def current_step_def(self):
        by = self.steps_by_type()
        for d in self.step_defs:
            s = by.get(d["code"])
            if not s or not s.ended_at:
                return d
        return None

    def compliance(self):
        """Check each step against the client work instruction."""
        tol = rule("FERMENT_TOLERANCE_HOURS")
        by = self.steps_by_type()
        rows = []
        for d in self.step_defs:
            s = by.get(d["code"])
            row = {"def": d, "step": s, "issues": [], "photos": list(s.photos.all()) if s else []}
            if not s or not s.ended_at:
                row["state"] = "current" if (s and s.started_at) else "pending"
                rows.append(row)
                continue
            dur = s.duration_hours
            if d["kind"] == "ferment" and dur is not None and abs(dur - d["hours"]) > tol:
                row["issues"].append(f"{dur:.0f} h – required {d['hours']} h")
            if d["kind"] == "dry" and dur is not None and dur < 4:
                row["issues"].append("Drying period too short")
            if not row["photos"]:
                row["issues"].append("No photo")
            if d["kind"] in ("ferment", "dry", "pack") and s.weight_end_kg is None:
                row["issues"].append("No weight")
            row["state"] = "warn" if row["issues"] else "ok"
            rows.append(row)
        return rows

    def is_compliant(self):
        return all(r["state"] == "ok" for r in self.compliance())

    def refresh_depot_status(self):
        bags = list(self.bags.all())
        if not bags:
            return
        tests = [b.get_test() for b in bags]
        if any(t is None for t in tests):
            new = self.Status.RECEIVED
        elif all(t.moisture_ok for t in tests):
            new = self.Status.ACCEPTED
        else:
            new = self.Status.REJECTED
        if new != self.status:
            self.status = new
            self.save(update_fields=["status", "updated_at"])


class ProcessStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="steps")
    step_type = models.CharField(max_length=20, choices=stepdefs.CHOICES)
    sequence = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    weight_start_kg = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    weight_end_kg = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    client_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["batch", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "step_type"], name="unique_step_per_batch"),
        ]

    def __str__(self):
        return f"{self.batch.code} · {self.label}"

    @property
    def definition(self):
        return stepdefs.BY_CODE.get(self.step_type, {"label": self.step_type, "hours": 0, "kind": ""})

    @property
    def label(self):
        return self.definition["label"]

    @property
    def duration_hours(self):
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() / 3600
        return None

    @property
    def weight_loss_pct(self):
        if self.weight_start_kg and self.weight_end_kg:
            return (1 - float(self.weight_end_kg) / float(self.weight_start_kg)) * 100
        return None


class StepPhoto(models.Model):
    KIND = [("START", "Start"), ("END", "End"), ("OTHER", "Other")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step = models.ForeignKey(ProcessStep, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="steps/%Y/%m/")
    kind = models.CharField(max_length=5, choices=KIND, default="END")
    taken_at = models.DateTimeField(default=timezone.now)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["taken_at"]


class Bag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="bags")
    bag_number = models.PositiveSmallIntegerField()
    weight_kg = models.DecimalField(max_digits=7, decimal_places=2)
    received_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)

    class Meta:
        ordering = ["batch", "bag_number"]
        unique_together = [("batch", "bag_number")]

    @property
    def code(self):
        return f"{self.batch.code}-B{self.bag_number:02d}"

    def get_test(self):
        try:
            return self.test
        except DepotTest.DoesNotExist:
            return None

    def __str__(self):
        return self.code


class DepotTest(models.Model):
    """Moisture + cut test done at the depot by a Quality Assessor (per bag)."""

    RESULT = [
        ("PREMIUM", "Approved – premium"),
        ("APPROVED", "Approved for export"),
        ("REJECTED", "Not accepted – re-dry"),
    ]

    bag = models.OneToOneField(Bag, on_delete=models.CASCADE, related_name="test")
    moisture_pct = models.DecimalField(max_digits=4, decimal_places=1)
    beans_cut = models.PositiveSmallIntegerField(default=300, help_text="Number of beans cut")
    purple = models.PositiveSmallIntegerField(default=0, help_text="Purple / under-fermented")
    slaty = models.PositiveSmallIntegerField(default=0)
    mouldy = models.PositiveSmallIntegerField(default=0)
    insect = models.PositiveSmallIntegerField(default=0, help_text="Insect damaged")
    germinated = models.PositiveSmallIntegerField(default=0)
    cut_defect_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0, editable=False)
    moisture_ok = models.BooleanField(default=False, editable=False)
    premium = models.BooleanField(default=False, editable=False)
    result = models.CharField(max_length=8, choices=RESULT, editable=False, default="REJECTED")
    remarks = models.CharField(max_length=255, blank=True)
    tested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL)
    tested_at = models.DateTimeField(default=timezone.now)

    @property
    def defects(self):
        return self.purple + self.slaty + self.mouldy + self.insect + self.germinated

    def save(self, *args, **kwargs):
        if self.beans_cut:
            self.cut_defect_pct = (Decimal(self.defects) * 100 / Decimal(self.beans_cut)).quantize(Decimal("0.1"))
        else:
            self.cut_defect_pct = Decimal("0")
        m = float(self.moisture_pct)
        self.moisture_ok = rule("MOISTURE_MIN") <= m <= rule("MOISTURE_MAX")
        self.premium = self.moisture_ok and float(self.cut_defect_pct) <= rule("CUT_TEST_PREMIUM_MAX")
        if self.premium:
            self.result = "PREMIUM"
        elif self.moisture_ok:
            self.result = "APPROVED"
        else:
            self.result = "REJECTED"
        super().save(*args, **kwargs)
