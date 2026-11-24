from django.contrib import admin

from .models import Bag, Batch, Cluster, Cooperative, DepotTest, Farmer, ProcessStep, StepPhoto


class ClusterInline(admin.TabularInline):
    model = Cluster
    extra = 1


@admin.register(Cooperative)
class CooperativeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "district")
    search_fields = ("name", "code")
    inlines = [ClusterInline]


@admin.register(Cluster)
class ClusterAdmin(admin.ModelAdmin):
    list_display = ("name", "cooperative", "hamlet")
    list_filter = ("cooperative",)
    search_fields = ("name", "hamlet")


@admin.register(Farmer)
class FarmerAdmin(admin.ModelAdmin):
    list_display = ("member_number", "first_name", "last_name", "cooperative", "cluster", "village", "active")
    list_filter = ("cooperative", "cluster", "active", "gender")
    search_fields = ("member_number", "first_name", "last_name", "village", "phone")
    raw_id_fields = ("user",)


class StepInline(admin.TabularInline):
    model = ProcessStep
    extra = 0
    fields = ("step_type", "started_at", "ended_at", "weight_start_kg", "weight_end_kg", "notes")


class BagInline(admin.TabularInline):
    model = Bag
    extra = 0
    fields = ("bag_number", "weight_kg", "received_at")


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("code", "farmer", "status", "bean_source", "drying_method", "wet_weight_kg",
                    "dry_weight_kg", "started_at")
    list_filter = ("status", "bean_source", "drying_method", "fermented_by", "farmer__cooperative")
    search_fields = ("code", "farmer__member_number", "farmer__first_name", "farmer__last_name")
    date_hierarchy = "started_at"
    inlines = [StepInline, BagInline]


@admin.register(StepPhoto)
class StepPhotoAdmin(admin.ModelAdmin):
    list_display = ("step", "kind", "taken_at", "uploaded_at")


@admin.register(DepotTest)
class DepotTestAdmin(admin.ModelAdmin):
    list_display = ("bag", "moisture_pct", "cut_defect_pct", "result", "tested_by", "tested_at")
    list_filter = ("result",)
