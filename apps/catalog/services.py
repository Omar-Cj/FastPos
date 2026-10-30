"""Catalog bulk operations: CSV import / export of the price list."""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.catalog.models import Category, Product

CSV_COLUMNS = ["barcode", "name", "price", "expected_weight_g", "weight_tolerance_g", "category"]
REQUIRED_COLUMNS = {"barcode", "name", "price", "expected_weight_g"}


class CSVFormatError(ValueError):
    """The file as a whole is unusable (encoding, missing header columns)."""


def _parse_row(row: dict) -> dict:
    """Validate one CSV row; raise ValueError with a human message."""
    barcode = (row.get("barcode") or "").strip()
    name = (row.get("name") or "").strip()
    if not barcode:
        raise ValueError("barcode is required.")
    if len(barcode) > 64:
        raise ValueError("barcode is longer than 64 characters.")
    if not name:
        raise ValueError("name is required.")
    if len(name) > 200:
        raise ValueError("name is longer than 200 characters.")
    try:
        price = Decimal((row.get("price") or "").strip())
    except InvalidOperation as exc:
        raise ValueError("price must be a number.") from exc
    if (not price.is_finite() or price < 0 or price >= Decimal("1e10")
            or price != price.quantize(Decimal("0.01"))):
        raise ValueError("price must be a non-negative amount with at most 2 decimals.")

    def _int(field, *, optional=False, positive=False):
        raw = (row.get(field) or "").strip()
        if raw == "" and optional:
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{field} must be a whole number of grams.") from exc
        if value < 0 or (positive and value == 0):
            raise ValueError(f"{field} must be {'positive' if positive else 'zero or more'}.")
        return value

    category = (row.get("category") or "").strip()
    if len(category) > 120:
        raise ValueError("category is longer than 120 characters.")
    return {
        "barcode": barcode,
        "name": name,
        "price": price,
        "expected_weight_g": _int("expected_weight_g", positive=True),
        "weight_tolerance_g": _int("weight_tolerance_g", optional=True),
        "category": category,
    }


def _read_text(file_obj) -> str:
    raw = file_obj.read()
    if isinstance(raw, str):
        return raw.lstrip("﻿")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVFormatError("File must be UTF-8 encoded CSV.") from exc


def import_products_csv(store, file_obj) -> dict:
    """Upsert products of `store` by barcode from a CSV file.

    Header: barcode,name,price,expected_weight_g,weight_tolerance_g,category (the last two optional).
    Blank tolerance = store default; blank category = none; unknown category names are created.
    Invalid rows are reported and skipped; valid rows are applied. `row` is the line number in the file
    (the header is line 1).
    """
    reader = csv.DictReader(io.StringIO(_read_text(file_obj)))
    header = {(h or "").strip() for h in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - header
    if missing:
        raise CSVFormatError(f"Missing column(s): {', '.join(sorted(missing))}.")
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]

    created = updated = 0
    errors = []
    categories = {c.name.lower(): c for c in Category.objects.filter(store=store)}

    with transaction.atomic():
        for line_no, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                continue  # blank line
            try:
                data = _parse_row(row)
            except ValueError as exc:
                barcode = (row.get("barcode") or "").strip()
                errors.append({"row": line_no, "barcode": barcode, "message": str(exc)})
                continue

            cat_name = data.pop("category")
            category = None
            if cat_name:
                category = categories.get(cat_name.lower())
                if category is None:
                    category = Category.objects.create(store=store, name=cat_name)
                    categories[cat_name.lower()] = category

            _, was_created = Product.objects.update_or_create(
                store=store, barcode=data.pop("barcode"), defaults={**data, "category": category}
            )
            if was_created:
                created += 1
            else:
                updated += 1

    return {"created": created, "updated": updated, "errors": errors}


def export_products_csv(queryset) -> str:
    """Render products in the import layout, so the output can be edited and re-imported."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for p in queryset.select_related("category"):
        writer.writerow([
            p.barcode,
            p.name,
            f"{p.price:.2f}",
            p.expected_weight_g,
            "" if p.weight_tolerance_g is None else p.weight_tolerance_g,
            p.category.name if p.category_id else "",
        ])
    return buf.getvalue()
