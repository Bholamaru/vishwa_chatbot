"""
chatbot/engine.py  --  Rule-based ERP chatbot (no AI, no third-party API).

It auto-discovers EVERY Django model in the project, so new models work
without changing any code. It understands plain-English questions such as:

  show all invoices
  how many customers
  invoices where status is paid and amount > 5000
  total amount of invoices this month
  average price of products
  highest salary of employees
  count orders by status
  top 5 products by price
  latest 10 purchase orders
  get customer 15
  search "rahul" in customers
  stock between 10 and 50 of products
"""
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db import models
from django.db.models import Avg, Count, Max, Min, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

# ----------------------------------------------------------------- settings
EXCLUDED_APPS = {
    "admin", "contenttypes", "sessions", "authtoken", "token_blacklist",
    "django_celery_beat", "django_celery_results",
}
# whole models the chatbot must never touch (app_label.ModelName)
EXCLUDED_MODELS = {
    "auth.User", "auth.Group", "auth.Permission",
    "Settings.MyModel", "Settings.UserPermission", "Settings.Module",
}
# any field whose name contains one of these is never read or shown
SENSITIVE = ("password", "token", "secret", "api_key", "otp", "salt",
             "aadhar", "pan_no", "panno", "bankaccount", "bank_account", "ifsc", "salary")
MAX_ROWS = 20
MAX_COLS = 10
# short names users may type -> model CLASS name.  Add your own.
# friendly name -> "AppLabel.ModelName".  STARTER LIST - please confirm each one.
EXTRA_ALIASES = {
     "purchase order": "Purchase.PurchasePO",
        "indent": "Purchase.Indent",
    
    
        # All Master 
        "item": "All_Masters.ItemMaster",
        "item master": "All_Masters.ItemMaster",
        "customer": "All_Masters.Item",
        "supplier": "All_Masters.Item",
        "work order": "Production.WorkOrderEntry",
        "production": "Production.ProductionEntry",
        "operator": "All_Masters.Add_New_Operator_Model",
        "machine": "All_Masters.Work_Center_Model",
        "bom": "All_Masters.BOMItem",
        "gst": "All_Masters.TaxDetails",
        "item master":"All_Masters.ItemTable",
        "cycle time master":"All_Masters.Cycle_Time_Master_Model",
        "po": "Purchase.PurchasePO",
        "jobwork po":"Purchase.NewJobWorkPoInfo",
    
        # Store 
        "gate entry":"Store.GeneralDetails",
        "purchase grn":"Store.GrnGenralDetail",
        "stock": "Store.RMStock",
        "rm stock": "Store.RMStock",
        "stock lot": "Store.RMStockLot",
        "stock transaction": "Store.RMStockTransaction",
        "grn": "Store.GrnGenralDetail",
        "mrn": "Store.NewMrn",
        "57f4 inward challan":"Store.InwardChallan2",
        "Material Issue challan":"Store.MaterialIssue",
        "jobwrok inward challan":"Store.JobworkInwardChallan",
        "Fg moment":"Store.FGMovement",
        "opening stock fg":"Store.OpeningStockFG",
        "opening stock rm":"Store.OpeningStockRM",
        "delivery challan":"Store.DeliveryChallan",
    
    
        # Maintenance
        "tool management":"Maintenance.ToolManagement",
    
        # Production
        "production entry":"Production.ProductionEntry",
        "rework entry":"Production.ReworkProduction",
    
        # Quality
        "inprocess qc":"Quality.InwardtestQCinfo",
        "sales return qc":"Quality.SalesReturnQcInfo",
    
        # Sales
        "invoice": "Sales.Invoice",
        "sales order": "Sales.NewSalesOrder",
        "so": "Sales.NewSalesOrder",
        "customer sales order":"Sales.NewSalesOrder",
        "pending Irn list ":"Sales.Invoice",
        "cancel irn list":"Sales.Invoice",
    
    
}


# app label -> top-level Module name in Settings.Module (exact match, case-insensitive)
APP_MODULE = {
    "All_Masters": "Masters", "Purchase": "Purchase", "Store": "Store",
    "Production": "Production", "Quality": "Quality", "Sales": "Sales",
    "Planning": "Planning", "Account": "Accounts", "Maintenance": "Maintenance",
    "Dashboard": "Dashboard",
}


def _modules(user):
    """Module names this user may open (cached on the request's user object)."""
    if not hasattr(user, "_chatbot_mods"):
        try:
            from Settings.models import UserPermission
            user._chatbot_mods = {
                str(n).strip().lower() for n in UserPermission.objects
                .filter(user__user=user).values_list("module__name", flat=True)}
        except Exception:
            user._chatbot_mods = set()
    return user._chatbot_mods


def can_view(user, model):
    """Permission hook, based on your Settings.UserPermission table. Fails closed."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    want = APP_MODULE.get(model._meta.app_label)
    return bool(want) and want.lower() in _modules(user)

def detect_item_type(question):
    Item = apps.get_model("All_Masters", "Item")

    available_types = (
        Item.objects.order_by()
        .values_list("type", flat=True)
        .distinct()
    )

    normalized_question = re.sub(
        r"[^a-z0-9]+", " ", question.lower()
    ).strip()

    for item_type in available_types:
        if not item_type:
            continue

        normalized_type = re.sub(
            r"[^a-z0-9]+", " ", item_type.lower()
        ).strip()

        if not normalized_type:
            continue

        pattern = (
            r"\b" +
            r"\s+".join(re.escape(word)
                        for word in normalized_type.split()) +
            r"s?\b"
        )

        if re.search(pattern, normalized_question):
            return item_type

    return None
def scope_queryset(user, model, qs):
    """
    Row-level scope hook (multi-company / branch / tenant ERPs).
    Example:
        if any(f.name == "company" for f in model._meta.concrete_fields):
            return qs.filter(company=user.company)
    """
    return qs


# ------------------------------------------------------------------ helpers
STOP = {"for", "the", "a", "an", "all", "me", "my", "any", "of", "to", "show",
        "get", "list", "is", "are", "in", "on", "please"}
WORD_OPS = (r"greater than or equal to|at least|less than or equal to|at most|"
            r"greater than|more than|above|over|less than|below|under|"
            r"not equal to|is not|isnt|contains|containing|like|starts with|"
            r"ends with|equal to|equals|is")
SYM_OPS = r">=|<=|!=|>|<|=|:"
OP_MAP = {
    "greater than or equal to": "gte", "at least": "gte", ">=": "gte",
    "less than or equal to": "lte", "at most": "lte", "<=": "lte",
    "greater than": "gt", "more than": "gt", "above": "gt", "over": "gt", ">": "gt",
    "less than": "lt", "below": "lt", "under": "lt", "<": "lt",
    "not equal to": "ne", "is not": "ne", "isnt": "ne", "!=": "ne",
    "contains": "icontains", "containing": "icontains", "like": "icontains",
    "starts with": "istartswith", "ends with": "iendswith",
    "equal to": "exact", "equals": "exact", "is": "exact", "=": "exact", ":": "exact",
}
COND_RE = re.compile(
    r"^\s*(?:between\s+(?P<b1>\S+)\s+and\s+(?P<b2>\S+)"
    r"|(?:(?P<w>" + WORD_OPS + r")\s+|(?P<s>" + SYM_OPS + r")\s*)"
    r"(?P<val>\"[^\"]+\"|'[^']+'|\S+))"
)
NUMERIC = (models.IntegerField, models.DecimalField, models.FloatField)


def _variants(name):
    n = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
    out = {n}
    if n.endswith("ies"):
        out.add(n[:-3] + "y")
    elif n.endswith("s"):
        out.add(n[:-1])
    if n.endswith("y"):
        out.add(n[:-1] + "ies")
    out.add(n + "s")
    return out


def _is_sensitive(name):
    return any(s in name.lower() for s in SENSITIVE)


def _fields(model):
    """alias -> field, for every readable concrete field."""
    d = {}
    for f in model._meta.concrete_fields:
        if _is_sensitive(f.name):
            continue
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", f.name).replace("_", " ")
        for a in {f.name.lower(), f.name.lower().replace("_", " "),
                  re.sub(r"\s+", " ", spaced).lower().strip(),   # GrnDate -> "grn date"
                  str(f.verbose_name).lower()}:
            d.setdefault(a, f)
    return d


NUM_NAME = re.compile(r"(qty|quantity|rate|amt|amount|total|value|wt|weight|price|kg|nos|charges|cost)", re.I)
NOT_NUM = re.compile(r"(type|remark|code|name|desc|reason|status|ref|no$|number)", re.I)
DATE_NAME = re.compile(r"date|_dt$", re.I)
MAX_SCAN = 50000   # most rows the bot will read in Python for text-typed columns
MAX_IDS = 20000
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y",
                "%d %b %Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y")
AGG_FUNCS = {"sum": Sum, "avg": Avg, "max": Max, "min": Min}


def _is_text(f):
    return isinstance(f, (models.CharField, models.TextField))


def _kind(f):
    """'num' / 'date' / None.  Real columns by type; text columns by NAME
    (your ERP stores many qty / rate / date values in CharFields)."""
    if f.is_relation or f.primary_key:
        return None
    if isinstance(f, NUMERIC):
        return "num"
    if isinstance(f, models.DateField):
        return "date"
    if _is_text(f):
        if DATE_NAME.search(f.name):
            return "date"
        if NUM_NAME.search(f.name) and not NOT_NUM.search(f.name):
            return "num"
    return None


def _is_numeric(f):
    return _kind(f) == "num"


def _num(v):
    if v is None:
        return None
    try:
        d = Decimal(str(v)) if isinstance(v, (int, float, Decimal)) else \
            Decimal(re.sub(r"[,\s\u20b9]", "", str(v)))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:19]
    for chunk in (s, s[:10]):
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(chunk, fmt).date()
            except ValueError:
                pass
    return None


def _text_test(kind, op, raw, raw2=None):
    """Python test for a text column that really holds numbers or dates."""
    conv = _num if kind == "num" else _dt
    what = "number" if kind == "num" else "date (try YYYY-MM-DD)"

    def parse(x):
        r = conv(str(x).strip().strip("\"'").rstrip("?.,;"))
        if r is None:
            raise ValueError(f"'{x}' is not a valid {what}.")
        return r
    a = parse(raw)
    if op == "between":
        b = parse(raw2)
        test = lambda v: a <= v <= b
    else:
        test = {"exact": lambda v: v == a, "ne": lambda v: v != a,
                "gt": lambda v: v > a, "gte": lambda v: v >= a,
                "lt": lambda v: v < a, "lte": lambda v: v <= a}[op]
    return lambda raw_v: (c := conv(raw_v)) is not None and test(c)


def _py_filter(qs, field, test):
    n = qs.count()
    if n > MAX_SCAN:
        raise ValueError(f"That needs scanning {n} rows. Please narrow it first "
                         "(e.g. add a plant, item or status condition).")
    ids = [pk for pk, v in qs.values_list("pk", field.name).iterator() if test(v)]
    if len(ids) > MAX_IDS:
        raise ValueError(f"{len(ids)} rows match; please narrow the question a bit.")
    return qs.filter(pk__in=ids)


def _date_range(low):
    today = timezone.localdate()
    if re.search(r"\btoday(?:'s|s)?\b", low):
        return today, today
    if re.search(r"\byesterday(?:'s|s)?\b", low):
        y = today - timedelta(days=1)
        return y, y
    if re.search(r"\bthis week\b", low):
        return today - timedelta(days=today.weekday()), today
    if re.search(r"\bthis month\b", low):
        return today.replace(day=1), today
    if re.search(r"\blast month\b", low):
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if re.search(r"\bthis year\b", low):
        return today.replace(month=1, day=1), today
    m = re.search(r"\blast (\d+) days?\b", low)
    if m:
        return today - timedelta(days=int(m.group(1))), today
    return None


# ------------------------------------------------------------------- engine
class ChatbotEngine:
    def __init__(self):
        self.models = {}
        for m in apps.get_models():
            if (m._meta.app_label in EXCLUDED_APPS or m._meta.auto_created
                    or m._meta.label in EXCLUDED_MODELS):
                continue
            names = {re.sub(r"(?<!^)(?=[A-Z])", " ", m.__name__),
                     str(m._meta.verbose_name), str(m._meta.verbose_name_plural)}
            for n in names:
                for v in _variants(n):
                    self.models.setdefault(v, m)
        by_label = {m._meta.label: m for m in apps.get_models()}
        for alias, label in EXTRA_ALIASES.items():
            if label in by_label and label not in EXCLUDED_MODELS:
                for v in _variants(alias):
                    self.models[v] = by_label[label]
        self.sorted_aliases = sorted(self.models, key=len, reverse=True)

    # ---------------------------------------------------------- public API
    def answer(self, question, user):
        try:
            return self._answer(question, user)
        except ValueError as e:
            return {"ok": False, "type": "text", "message": str(e)}

    # -------------------------------------------------------------- inner
    def _answer(self, question, user):
        low = question.strip().lower()
        if not low or low in {"help", "hi", "hello", "hey"} or \
                re.search(r"\bwhat can you do\b|\bexamples?\b", low):
            return self._help()
        if re.search(r"\b(list|show|what|which)\b.*\b(models|tables|modules)\b", low):
            return self._list_models(user)

        from . import custom_rules  # business questions that need real logic
        for pattern, handler in custom_rules.RULES:
            hit = re.search(pattern, low)
            if hit:
                res = handler(low, user, hit)
                if res:
                    return res

        model_low = re.sub(r"\b(?:order|sort)(?:ed)?\s+by\b", " ", low)
        # candidates: every model name found in the sentence (a longer name
        # swallows a shorter one inside it, e.g. "purchase order" vs "order")
        cands, spans = {}, []
        for a in self.sorted_aliases:
            m = re.search(rf"\b{re.escape(a)}\b", model_low)
            if not m or any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            spans.append((m.start(), m.end()))
            cands.setdefault(self.models[a], (a, m.start()))
        found = None
        if cands:
            def score(item):  # most of its own fields mentioned wins, then earliest
                mdl, (_, pos) = item
                hits = sum(1 for fa in _fields(mdl) if fa != "id"
                           and re.search(rf"\b{re.escape(fa)}\b", model_low))
                return (-hits, pos)
            mdl, (a, _) = min(cands.items(), key=score)
            found = (a, mdl)
        if not found:
            return {"ok": False, "type": "text",
                    "message": "I couldn't find which data you mean. Try: "
                               "'show all <model>', 'how many <model>', "
                               "'total <field> of <model>'. Type 'list models' to see all."}
        alias, model = found
        if not can_view(user, model):
            return {"ok": False, "type": "text",
                    "message": f"You don't have permission to view {model._meta.verbose_name_plural}."}

        fields = _fields(model)
        label = str(model._meta.verbose_name_plural).title()

        # ---- field mentions (longest alias first, no overlaps)
        mentions, taken = [], []
        for fa in sorted(fields, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(fa)}\b", low):
                if any(m.start() < e and m.end() > s for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                mentions.append({"s": m.start(), "e": m.end(), "f": fields[fa], "used": False})
        mentions.sort(key=lambda x: x["s"])

        qs = scope_queryset(user, model, model._default_manager.all())

    #    add
        # ---- supplier / customer type filter
        if model._meta.label == "All_Masters.Item":

            if re.search(r"\bsuppliers?\b", low):
                qs = qs.filter(type__iexact="Supplier")

            elif re.search(r"\bcustomers?\b", low):
                qs = qs.filter(type__iexact="Customer")
        pyfilters = []  # conditions on text columns that hold numbers/dates
        # ---- conditions: <field> <op> <value> | <field> between a and b
        for mt in mentions:
            c = COND_RE.match(low[mt["e"]:])
            if not c:
                continue
            f = mt["f"]
            mt["used"] = True
            kind = _kind(f)
            textual = _is_text(f) and kind
            if c.group("b1"):
                if textual:
                    pyfilters.append((f, _text_test(kind, "between", c.group("b1"), c.group("b2"))))
                    continue
                lo, hi = self._kw(f, "gte", c.group("b1")), self._kw(f, "lte", c.group("b2"))
                qs = qs.filter(**{**lo[1], **hi[1]})
                continue
            op = OP_MAP[(c.group("w") or c.group("s")).strip()]
            if textual and op in ("exact", "ne", "gt", "gte", "lt", "lte"):
                pyfilters.append((f, _text_test(kind, op, c.group("val"))))
                continue
            neg, kw = self._kw(f, op, c.group("val"))
            qs = qs.exclude(**kw) if neg else qs.filter(**kw)

        # ---- date keywords (today, this month, last 7 days ...)
        dr = _date_range(low)
        if dr:
            df = next((m["f"] for m in mentions
                       if not m["used"] and _kind(m["f"]) == "date"), None)
            if not df:
                df = next((f for n in ("created_at", "created", "date", "created_on")
                           for f in [fields.get(n)] if f and _kind(f) == "date"), None)
            if not df:
                df = next((f for f in fields.values() if _kind(f) == "date"), None)
            via = None
            if not df:  # item/child table: use the date of its parent (header) record
                for fk in model._meta.concrete_fields:
                    rel = fk.related_model if fk.is_relation else None
                    if rel is None or rel._meta.label in EXCLUDED_MODELS:
                        continue
                    rf = next((x for x in rel._meta.concrete_fields if _kind(x) == "date"), None)
                    if rf:
                        df, via = rf, fk.name
                        break
            if not df:
                raise ValueError(f"{label} have no date field, so I can't filter by date.")
            path = f"{via}__{df.name}" if via else df.name
            if _is_text(df):
                lo_d, hi_d = dr
                pyfilters.append((SimpleNamespace(name=path),
                                  lambda v, lo_d=lo_d, hi_d=hi_d:
                                  (d := _dt(v)) is not None and lo_d <= d <= hi_d))
            else:
                look = f"{path}__date__range" if isinstance(df, models.DateTimeField) \
                    else f"{path}__range"
                qs = qs.filter(**{look: dr})

        # ---- get by id:  "get customer 15", "invoice #7", "id 3"
        idm = re.search(rf"\b{re.escape(alias)}\s+#?(\d+)\b|#(\d+)\b|\bid\s*(?:=|:|is)?\s*(\d+)\b", low)
        if idm:
            qs = qs.filter(pk=int(next(g for g in idm.groups() if g)))

        # ---- search term
        term = self._search_term(low, alias)
        if term:
            text_fields = [f for f in model._meta.concrete_fields
                           if isinstance(f, (models.CharField, models.TextField))
                           and not _is_sensitive(f.name)][:8]
            if not text_fields:
                raise ValueError(f"{label} have no text fields to search.")
            from django.db.models import Q
            q = Q()
            for f in text_fields:
                q |= Q(**{f"{f.name}__icontains": term})
            qs = qs.filter(q)

        for pf, ptest in pyfilters:
            qs = _py_filter(qs, pf, ptest)

        # ---- order by / group by
        order = None
        om = re.search(r"(?:order(?:ed)?|sort(?:ed)?)\s+by\s+", low)
        if om:
            mt = next((m for m in mentions if m["s"] == om.end()), None)
            if mt:
                mt["used"] = True
                desc = re.match(r"\s*(desc|descending)", low[mt["e"]:])
                order = ("-" if desc else "") + mt["f"].name
        group = None
        for gm in re.finditer(r"\b(?:group(?:ed)?\s+by|per|for each|by|wise)\s+", low):
            mt = next((m for m in mentions if m["s"] == gm.end() and not m["used"]), None)
            if mt:
                mt["used"] = True
                plain_by = gm.group(0).strip() == "by"
                if plain_by and (re.search(r"\b(top|latest|recent|first|last)\s+\d+", low)
                                 or _is_numeric(mt["f"])):
                    # "top 5 invoices by amount" is sorting, not grouping
                    desc = "top" in low or re.match(r"\s*(desc|descending)", low[mt["e"]:])
                    order = order or (("-" if desc else "") + mt["f"].name)
                else:
                    group = mt["f"]
                break

        # ---- aggregate intent
        agg = None
        if re.search(r"\b(how many|count|number of)\b", low):
            agg = "count"
        elif re.search(r"\b(average|avg|mean)\b", low):
            agg = "avg"
        elif re.search(r"\b(highest|maximum|max|largest|biggest|most expensive)\b", low):
            agg = "max"
        elif re.search(r"\b(lowest|minimum|min|smallest|cheapest)\b", low):
            agg = "min"
        elif re.search(r"\b(sum|total)\b", low):
            agg = "sum"
        num = next((m["f"] for m in mentions if not m["used"] and _is_numeric(m["f"])), None)

        # "top 5 products by price" / "latest 10 invoices"
        lim = re.search(r"\b(top|latest|recent|first|last|limit)\s+(\d+)\b(?!\s*(?:days?|weeks?|months?|years?))", low)
        limit = min(int(lim.group(2)), MAX_ROWS) if lim else MAX_ROWS

        if agg in ("max", "min") and lim and not group:
            agg = None  # "highest 5 invoices" -> a list, not one number
            if not order and num:
                order = ("-" if lim.group(1) != "lowest" else "") + num.name

        funcs = {"sum": Sum, "avg": Avg, "max": Max, "min": Min}

        if group is not None:
            if agg in funcs and not num:
                raise ValueError(self._need_field(agg, model, label))
            if agg in funcs and _is_text(num):
                rows = self._py_group(qs, group, num, agg)
            else:
                expr = funcs[agg](num.name) if agg in funcs else Count("pk")
                rows = list(qs.values(group.name).annotate(value=expr).order_by("-value")[:MAX_ROWS])
            what = f"{agg or 'count'}{' of ' + num.name if agg in funcs else ''}"
            return {"ok": True, "type": "table",
                    "message": f"{label}: {what} grouped by {group.name}",
                    "columns": [group.name, "value"], "rows": rows, "total": len(rows)}

        if agg == "count":
            return {"ok": True, "type": "scalar", "value": qs.count(),
                    "message": f"Number of {label}: {qs.count()}"}
        if agg in funcs:
            if not num:
                raise ValueError(self._need_field(agg, model, label))
            val, bad = self._agg_value(qs, num, agg)
            if isinstance(val, Decimal):
                val = round(val, 2)
            name = {"sum": "Total", "avg": "Average", "max": "Highest", "min": "Lowest"}[agg]
            note = f" ({bad} rows had non-numeric values and were skipped)" if bad else ""
            return {"ok": True, "type": "scalar", "value": val,
                    "message": f"{name} {num.name.replace('_', ' ')} of {label}: {val}{note}"}

        # ---- plain list
        if order is None:
            if lim and lim.group(1) in ("latest", "recent", "last"):
                dfield = next((fields[n] for n in ("created_at", "created", "date") if n in fields), None)
                order = "-" + (dfield.name if dfield else "pk")
            elif lim and lim.group(1) == "top" and num:
                order = "-" + num.name
        py_order = None
        if order:
            try:
                of = model._meta.get_field(order.lstrip("-"))
            except Exception:
                of = None
            if of is not None and _is_text(of) and _kind(of) == "num":
                py_order = (of, order.startswith("-"))  # text column: sort as numbers
            else:
                qs = qs.order_by(order)
        elif not model._meta.ordering:
            qs = qs.order_by("pk")
        cols = [f.attname for f in model._meta.concrete_fields
                if not _is_sensitive(f.name)][:MAX_COLS]
        total = qs.count()
        if py_order:
            of, desc = py_order
            if total > MAX_SCAN:
                raise ValueError(f"{total} rows to sort; please add a filter first.")
            keyed = [(_num(v), pk) for pk, v in qs.values_list("pk", of.name).iterator()]
            keyed = sorted([x for x in keyed if x[0] is not None],
                           key=lambda x: x[0], reverse=desc)[:limit]
            ids = [pk for _, pk in keyed]
            by_pk = {r.pop("pk"): r for r in
                     model._default_manager.filter(pk__in=ids).values("pk", *cols)}
            rows = [by_pk[i] for i in ids if i in by_pk]
        else:
            rows = list(qs.values(*cols)[:limit])
        msg = f"Found {total} {label}" + (f" (showing {len(rows)})" if total > len(rows) else "")
        return {"ok": True, "type": "table", "message": msg,
                "columns": cols, "rows": rows, "total": total}

    # ------------------------------------------------ text-column aggregates
    @staticmethod
    def _numbers(qs, f):
        n = qs.count()
        if n > MAX_SCAN:
            raise ValueError(f"{n} rows to read; please add a filter (plant, item, date...) first.")
        vals, bad = [], 0
        for v in qs.values_list(f.name, flat=True).iterator():
            d = _num(v)
            if d is None:
                bad += v not in (None, "")
            else:
                vals.append(d)
        return vals, bad

    def _agg_value(self, qs, f, agg):
        if not _is_text(f):
            return qs.aggregate(v=AGG_FUNCS[agg](f.name))["v"], 0
        vals, bad = self._numbers(qs, f)
        if not vals:
            return None, bad
        return {"sum": sum(vals), "avg": sum(vals) / len(vals),
                "max": max(vals), "min": min(vals)}[agg], bad

    @staticmethod
    def _py_group(qs, group, f, agg):
        if qs.count() > MAX_SCAN:
            raise ValueError("Too many rows to group; please add a filter first.")
        buckets = {}
        for g, v in qs.values_list(group.name, f.name).iterator():
            d = _num(v)
            if d is not None:
                buckets.setdefault(g, []).append(d)
        calc = {"sum": sum, "avg": lambda x: sum(x) / len(x), "max": max, "min": min}[agg]
        rows = [{group.name: g, "value": round(calc(vs), 2)} for g, vs in buckets.items()]
        rows.sort(key=lambda r: r["value"], reverse=True)
        return rows[:MAX_ROWS]

    # ----------------------------------------------------------- builders
    def _kw(self, f, op, raw):
        """Return (negate, {orm_lookup: value}) for one condition."""
        raw = raw.strip().strip("\"'").rstrip("?.,;")
        name = f.name
        tail = {"exact": "", "gt": "__gt", "gte": "__gte", "lt": "__lt", "lte": "__lte",
                "icontains": "__icontains", "istartswith": "__istartswith",
                "iendswith": "__iendswith", "ne": ""}
        neg = op == "ne"
        if f.is_relation:
            if raw.isdigit():
                return neg, {f"{name}_id{tail[op]}": int(raw)}
            tf = next((x for x in f.related_model._meta.concrete_fields
                       if isinstance(x, models.CharField) and not _is_sensitive(x.name)), None)
            if not tf:
                raise ValueError(f"Use the id for '{name}' (e.g. {name} = 3).")
            return neg, {f"{name}__{tf.name}__icontains": raw}
        if isinstance(f, models.BooleanField):
            return neg, {name: raw.lower() in ("true", "yes", "y", "1", "active")}
        if f.choices:
            for v, lab in f.choices:
                if raw.lower() in (str(v).lower(), str(lab).lower()):
                    return neg, {f"{name}{tail[op]}": v}
            raise ValueError(f"'{raw}' is not a valid {name}. Options: "
                             + ", ".join(str(lab) for _, lab in f.choices))
        if isinstance(f, (models.CharField, models.TextField)) and op in ("gt", "gte", "lt", "lte"):
            raise ValueError(f"'{name}' is stored as text in the database, so I can't "
                             "compare numbers or dates on it yet.")
        try:
            if isinstance(f, (models.IntegerField,)):
                val = int(raw.replace(",", ""))
            elif isinstance(f, (models.DecimalField, models.FloatField)):
                val = Decimal(raw.replace(",", ""))
            elif isinstance(f, models.DateField):
                val = parse_date(raw)
                if val is None:
                    raise ValueError
                if isinstance(f, models.DateTimeField):
                    return neg, {f"{name}__date{tail[op]}": val}
            else:
                val = raw
                if op == "exact" or op == "ne":
                    return neg, {f"{name}__iexact": val}
        except (ValueError, InvalidOperation):
            raise ValueError(f"'{raw}' is not a valid value for {name}"
                             + (" (use YYYY-MM-DD)" if isinstance(f, models.DateField) else "") + ".")
        return neg, {f"{name}{tail[op]}": val}

    def _search_term(self, low, alias):
        m = re.search(r"\b(?:search|find|look\s?up|named|called)\b(.*)", low)
        if not m:
            return None
        tail = m.group(1)
        quoted = re.search(r"[\"']([^\"']+)[\"']", tail)
        if quoted:
            return quoted.group(1)
        cut = re.split(r"\b(?:in|from|where|with|having|order|sort|group|top|limit|and)\b", tail)[0]
        cut = re.sub(rf"\b{re.escape(alias)}\b", " ", cut)
        words = [w for w in re.findall(r"[\w@.\-+]+", cut)
                 if w not in STOP and w not in self.models]
        return " ".join(words) or None

    @staticmethod
    def _need_field(agg, model, label):
        nums = [f.name for f in model._meta.concrete_fields if _is_numeric(f)]
        return (f"Which field should I use for {agg}? Numeric fields of {label}: "
                + (", ".join(nums) or "none") + ".")

    # ------------------------------------------------------------ helpers
    def _list_models(self, user):
        rows = sorted({f"{m._meta.app_label}.{m.__name__}" for m in set(self.models.values())
                       if can_view(user, m)})
        return {"ok": True, "type": "table", "message": f"{len(rows)} models available",
                "columns": ["model"], "rows": [{"model": r} for r in rows], "total": len(rows)}

    @staticmethod
    def _help():
        return {"ok": True, "type": "text", "message": (
            "Ask me things like:\n"
            "- show all invoices\n- how many customers\n"
            "- invoices where status is paid and amount > 5000\n"
            "- total amount of invoices this month\n- average price of products\n"
            "- highest salary of employees\n- count orders by status\n"
            "- top 5 products by price\n- latest 10 purchase orders\n"
            "- get customer 15\n- search \"rahul\" in customers\n"
            "- stock between 10 and 50 of products\n- list models")}


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = ChatbotEngine()
    return _engine




