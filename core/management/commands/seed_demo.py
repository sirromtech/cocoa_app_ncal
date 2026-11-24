"""Create demo cooperatives, clusters, staff and farmer logins for testing.

    python manage.py seed_demo
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import steps as stepdefs
from core.models import Batch, Cluster, Cooperative, Farmer, ProcessStep

User = get_user_model()


class Command(BaseCommand):
    help = "Load demo data (safe to run more than once)."

    def handle(self, *args, **opts):
        admin, created = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True})
        if created:
            admin.set_password("admin123")
            admin.save()
        assessor, created = User.objects.get_or_create(username="assessor", defaults={"is_staff": True,
                                                                                     "first_name": "Depot"})
        if created:
            assessor.set_password("assessor123")
            assessor.save()

        coops = []
        for name, code in [("Demo Cooperative Society A", "DCA"), ("Demo Cooperative Society B", "DCB")]:
            c, _ = Cooperative.objects.get_or_create(code=code, defaults={"name": name, "district": "Wewak"})
            coops.append(c)
        clusters = []
        for coop in coops:
            for n in ("Cluster 1", "Cluster 2"):
                cl, _ = Cluster.objects.get_or_create(cooperative=coop, name=n)
                clusters.append(cl)

        farmers = []
        for i, (first, last) in enumerate([("Demo", "Farmer One"), ("Demo", "Farmer Two"), ("Demo", "Farmer Three")]):
            num = f"DEMO{i + 1:03d}"
            cl = clusters[i % len(clusters)]
            f = Farmer.objects.filter(member_number=num).first()
            if not f:
                u = User.objects.create_user(username=num, password="1234", first_name=first, last_name=last)
                f = Farmer.objects.create(user=u, member_number=num, first_name=first, last_name=last,
                                          cooperative=cl.cooperative, cluster=cl, village="Demo village")
            farmers.append(f)

        f = farmers[0]
        if not f.batches.exists():
            start = timezone.now() - timedelta(hours=60)
            b = Batch.objects.create(code=f"{f.member_number}-DEMO1", farmer=f, wet_weight_kg=120,
                                     started_at=start, client_updated_at=timezone.now())
            defs = stepdefs.steps_for("SOLAR")
            ProcessStep.objects.create(batch=b, step_type=defs[0]["code"], sequence=0, started_at=start,
                                       ended_at=start + timedelta(hours=48), weight_start_kg=120, weight_end_kg=112)
            ProcessStep.objects.create(batch=b, step_type=defs[1]["code"], sequence=1,
                                       started_at=start + timedelta(hours=48), weight_start_kg=112)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready.\n"
            "  Staff:   admin / admin123   (superuser)\n"
            "           assessor / assessor123\n"
            "  Farmers: DEMO001, DEMO002, DEMO003  –  PIN 1234"))
