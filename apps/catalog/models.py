from django.db import models

from apps.core.models import TimeStampedModel


class Category(TimeStampedModel):
    store = models.ForeignKey("stores.Store", on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=120)
    name_so = models.CharField(max_length=120, null=True, blank=True, help_text="Somali name (optional).")

    class Meta:
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_category_name_per_store")]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    """One line of the price list with a "should weigh" column.

    `expected_weight_g` is what one unit weighs including packaging. `weight_tolerance_g` is the
    per-product forgiveness; null means use `store.default_weight_tolerance_g`.
    """

    store = models.ForeignKey("stores.Store", on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    barcode = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=200)
    name_so = models.CharField(max_length=120, null=True, blank=True, help_text="Somali name (optional).")
    image = models.ImageField(upload_to="products/%Y/%m/", null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    expected_weight_g = models.PositiveIntegerField(help_text="Weight of one unit incl. packaging, in grams.")
    weight_tolerance_g = models.PositiveIntegerField(null=True, blank=True, help_text="Null = store default.")
    requires_weight_check = models.BooleanField(default=True, help_text="False for items too light to weigh.")
    has_eas_tag = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["store", "barcode"], name="uniq_barcode_per_store")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} [{self.barcode}]"

    def effective_tolerance_g(self) -> int:
        if self.weight_tolerance_g is not None:
            return self.weight_tolerance_g
        return self.store.default_weight_tolerance_g
