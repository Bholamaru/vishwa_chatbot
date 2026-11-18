"""
chatbot/custom_rules.py -- business questions that need real logic, not just
filter/sum.  Each rule is (regex, handler).  handler(low, user, match) returns
a result dict, or None to let the generic engine handle the question.
Add one function per report: low stock, pending POs, FG stock, ...
"""
import re

from django.apps import apps
from django.db.models import Q, Sum

MAX_ROWS = 20
_SKIP = (r"\b(where|between|lot|transaction|opening|fg|group|by|per|order|sort|"
         r"count|how many|average|highest|lowest|history|movement|search|find)\b|[<>=]")


def rm_stock(low, user, m):
    """'show todays stock', 'current stock', 'stock of RM-101'  -> Store.RMStock balances."""
    from .engine import can_view, scope_queryset
    if re.search(_SKIP, low):
        return None
    model = apps.get_model("Store", "RMStock")
    if not can_view(user, model):
        return {"ok": False, "type": "text", "message": "You don't have permission to view stock."}
    qs = scope_queryset(user, model, model._default_manager.all())
    item = re.search(r"\b(?:of|for)\s+([\w\-./]+)", low)
    if item:
        qs = qs.filter(item_code__icontains=item.group(1))
    rows = list(qs.values("item_code").annotate(total_qty=Sum("qty"))
                .filter(total_qty__gt=0).order_by("item_code")[:MAX_ROWS])
    grand = qs.aggregate(t=Sum("qty"))["t"]
    return {"ok": True, "type": "table",
            "message": f"Current RM stock (as of now): total qty {grand or 0}. For FG/WIP stock, ask separately.",
            "columns": ["item_code", "total_qty"], "rows": rows, "total": len(rows)}


def not_ready_stock(low, user, m):
    """Stop the generic engine from answering FG/WIP/... questions with RM data."""
    if re.search(r"\bopening stock\b", low) and re.search(r"\b(rm|fg)\b", low):
        return None
    return {"ok": False, "type": "text", "message": (
        "I can answer RM stock now. For FG / WIP / consumable / subcontract stock I need "
        "the calculation rule first. For opening stock, say 'opening stock rm' or 'opening stock fg'.")}


def low_stock(low, user, m):
    """'low stock', 'items below minimum level' -> RM balance vs Planning min level."""
    from .engine import can_view, scope_queryset
    rm = apps.get_model("Store", "RMStock")
    mm = apps.get_model("Planning", "UpdateItemWiseMinMax")
    if not (can_view(user, rm) and can_view(user, mm)):
        return {"ok": False, "type": "text", "message": "You don't have permission to view this."}
    bal = {r["item_code"]: r["t"] or 0 for r in
           scope_queryset(user, rm, rm._default_manager.all())
           .values("item_code").annotate(t=Sum("qty"))}
    rows = []
    for r in mm._default_manager.filter(min_level__gt=0).values(
            "item_code", "description", "min_level", "re_order_level"):
        have = bal.get(r["item_code"], 0)
        if have < r["min_level"]:
            rows.append({"item_code": r["item_code"], "description": r["description"],
                         "stock": have, "min_level": r["min_level"],
                         "short_by": r["min_level"] - have})
    rows.sort(key=lambda x: x["short_by"], reverse=True)
    return {"ok": True, "type": "table",
            "message": f"{len(rows)} items are below minimum level (RM stock vs Min/Max planning)",
            "columns": ["item_code", "description", "stock", "min_level", "short_by"],
            "rows": rows[:MAX_ROWS], "total": len(rows)}


IRN_COLS = ["invoice_no", "invoice_Date", "bill_to", "buyer_legal_name", "irn", "irn_status",
            "ack_no", "ack_date", "irn_error", "irn_cancel_reason", "irn_cancel_date"]


def irn_report(low, user, m):
    """IRN (e-invoice) questions on Sales.Invoice: generated / pending / cancelled / error."""
    from .engine import can_view, scope_queryset, _date_range, _py_filter, _dt, _is_text
    if re.search(r"\b(where|between)\b|[<>=]", low):
        return None  # let the generic engine handle explicit conditions
    inv = apps.get_model("Sales", "Invoice")
    if not can_view(user, inv):
        return {"ok": False, "type": "text", "message": "You don't have permission to view invoices."}
    qs = scope_queryset(user, inv, inv._default_manager.all())

    def empty(f):
        return Q(**{f"{f}__isnull": True}) | Q(**{f: ""})
    cancelled = Q(irn_status__icontains="cancel") | ~empty("irn_cancel_date")

    dr = _date_range(low)
    if dr:
        df = inv._meta.get_field("invoice_Date")
        if _is_text(df):
            qs = _py_filter(qs, df, lambda v: (d := _dt(v)) is not None and dr[0] <= d <= dr[1])
        else:
            look = "__date__range" if df.get_internal_type() == "DateTimeField" else "__range"
            qs = qs.filter(**{df.name + look: dr})

    if re.search(r"cancel", low):
        title, qs = "IRN cancelled", qs.filter(cancelled)
    elif re.search(r"\b(pending|not|nahi|nhi|without|missing|bina)\b", low):
        title, qs = "IRN pending (not generated)", qs.filter(empty("irn")).exclude(cancelled)
    elif re.search(r"error|fail", low):
        title, qs = "IRN error", qs.filter(~empty("irn_error"))
    elif re.search(r"generat|done|success|complete", low):
        title, qs = "IRN generated", qs.exclude(empty("irn")).exclude(cancelled)
    else:  # summary of all
        gen = qs.exclude(empty("irn")).exclude(cancelled).count()
        pend = qs.filter(empty("irn")).exclude(cancelled).count()
        canc = qs.filter(cancelled).count()
        rows = [{"status": "IRN generated", "count": gen},
                {"status": "IRN pending (not generated)", "count": pend},
                {"status": "IRN cancelled", "count": canc},
                {"status": "Total invoices", "count": qs.count()}]
        return {"ok": True, "type": "table", "message": "Invoice IRN summary",
                "columns": ["status", "count"], "rows": rows, "total": 4}

    cols = [c for c in IRN_COLS if c in {f.name for f in inv._meta.concrete_fields}]
    total = qs.count()
    rows = list(qs.order_by("-pk").values(*cols)[:MAX_ROWS])
    shown = f" (showing {len(rows)})" if total > len(rows) else ""
    return {"ok": True, "type": "table", "message": f"{total} invoices: {title}{shown}",
            "columns": cols, "rows": rows, "total": total}


RULES = [
    (r"\birn\b", irn_report),
    (r"\b(low stock|below (?:the )?(?:min|minimum|reorder|re-order)|shortage|reorder)\b", low_stock),
    (r"\b(?:fg|finished goods?|wip|consumable|subcon(?:tract)?)\s+stock\b|\bopening stock\b", not_ready_stock),
    (r"\bstock\b", rm_stock),
]