"""
chatbot/understand.py -- NO AI, no pip packages.

Free-form question  ->  the structured question that engine.py already answers.

    "indore ke suppliers dikhao"            -> suppliers where city = "indore"
    "pending PO kitne hain"                 -> how many purchase orders where approved status = "pending"
    "shiv ka mobile number batao"           -> suppliers where name contains "shiv"   (+ show only contact no)
    "which supplier has the most POs"       -> count purchase orders by supplier

How it knows what you mean (all automatic, from your models):
  1. model words      : every model name / verbose name / EXTRA_ALIASES
  2. field words      : every field name, + the synonym table below (mobile -> Contact_No ...)
  3. values           : leftover words are looked up IN YOUR DATA ("indore" is found in City)
  4. Hindi words      : tables below (kitne, dikhao, is mahine ...)
When a question is already in the structured form it is passed through untouched.
"""
import logging
import re
from datetime import date, timedelta

log = logging.getLogger("chatbot")

# =============================================================== CONFIG (edit me)
# alias of model -> nothing to do here; model words come from engine.py (EXTRA_ALIASES)

# the date field that "today / this month" should use (engine would pick the FIRST date field)
PRIMARY_DATE = {"Purchase.PurchasePO": "PoDate", "Purchase.NewJobWorkPoInfo": "PoDate",
                "Purchase.Indent": "Date"}

# "stock-like" tables: totals use this field when the user does not name one
DEFAULT_MEASURE = {
    "Store.RMStock": "qty",
    "Store.RMStockLot": "qty",
    # Production totals should default to the actual production quantity field.
    "Production.ProductionEntry": "prod_qty",
}

# extra words per model: {"App.Model": {"user word": "RealFieldName"}}
FIELD_ALIASES = {
    "All_Masters.Item": {"address": "Address_Line_1", "email": "Email_Id", "state": "Region",
                         "pin": "Pin_Code", "pincode": "Pin_Code", "mobile": "Contact_No",
                         "phone": "Contact_No", "contact": "Contact_No"},
    "All_Masters.ItemTable": {"name": "Name_Description", "description": "Name_Description",
                              "item name": "Name_Description", "part number": "part_no"},
    "All_Masters.Work_Center_Model": {"name": "WorkCenterName", "code": "WorkCenterCode",
                                      "type": "WorkCenterType"},
     "All_Masters.Supplier": {
        "name": "Name",
        "supplier name": "Name",
        "mobile": "Contact_No",
        "phone": "Contact_No",
        "contact": "Contact_No",
        "email": "Email_Id",
    },

    "All_Masters.Customer": {
        "name": "Name",
        "customer name": "Name",
        "mobile": "Contact_No",
        "phone": "Contact_No",
        "contact": "Contact_No",
        "email": "Email_Id",
    },

    "Purchase.PurchasePO": {
        "status": "Approved_Status",
        "amount": "GR_Total",
        "value": "GR_Total",
        "total": "GR_Total",
        "po amount": "GR_Total",
        "supplier": "Supplier",
        "vendor": "Supplier",
        "po type": "field",
    },
    "production": "Production.ProductionEntry",
    "productions": "Production.ProductionEntry",

    "production entry": "Production.ProductionEntry",
    "production entries": "Production.ProductionEntry",

    "rework": "Production.ProductionEntry",
    "rejection": "Production.ProductionEntry",
    "reject": "Production.ProductionEntry",
    "Sales.Invoice": {
        "customer": "bill_to",
        "invoice amount": "GR_Total",
        "amount": "GR_Total",
        "value": "GR_Total",
        "irn": "irn",
        "irn status": "irn_status",
    },
    "Store.RMStock": {
        "stock": "qty",
        "balance": "qty",
        "available stock": "qty",
        "rm stock": "qty",
    },
    "Purchase.NewJobWorkPoInfo": {"amount": "GR_Total", "value": "GR_Total"},
    "Purchase.Indent": {"status": "Auth"},
    # ProductionEntry uses separate fields for production, rework and rejection.
    "Production.ProductionEntry": {
        "production quantity": "prod_qty",
        "production qty": "prod_qty",
        "rework": "rework_qty",
        "rework quantity": "rework_qty",
        "rejected": "reject_qty",
        "rejected quantity": "reject_qty",
        "reject": "reject_qty",
        "reject quantity": "reject_qty",
    },
}

# generic user word -> regexes of REAL field names (first field that matches wins, any model)
SYN = {
    "name": [r"^name$", r"^[a-z]+name$", r"name_description", r"_name$", r"^name_"],
    "mobile": [r"contact_?no", r"mobile", r"phone"], "phone": [r"contact_?no", r"mobile", r"phone"],
    "contact": [r"contact_?no", r"contact"], "email": [r"email"], "mail": [r"email"],
    "address": [r"^address", r"address"], "city": [r"^city$", r"cityname"],
    "state": [r"^region$", r"state"], "pin": [r"pin_?code"], "pincode": [r"pin_?code"],
    "status": [r"status", r"^auth$"], "amount": [r"gr_?total", r"total_?amount", r"amount", r"^total$"],
    "value": [r"gr_?total", r"amount", r"^total$"], "rate": [r"^rate$", r"price", r"_rate$"],
    "price": [r"price", r"^rate$"], "qty": [r"qty", r"quantity"], "quantity": [r"quantity", r"qty"],
    "supplier": [r"^supplier$", r"supplier", r"party", r"vendor"], "vendor": [r"vendor", r"supplier"],
    "customer": [r"customer", r"buyer", r"party", r"client", r"bill_to"],
    "item": [r"^item$", r"item", r"part_?no"], "part": [r"part_?no", r"part_?code"],
    "plant": [r"plant"], "department": [r"department", r"dept"], "category": [r"category"],
    "designation": [r"designation"], "operator": [r"operator"], "machine": [r"machine"],
    "type": [r"^type$", r"type"], "description": [r"desc"], "code": [r"code"],
    "weight": [r"weight", r"_wt$", r"wt$"],
    "quotation": [r"quot_?no"], "quot": [r"quot_?no"], "enquiry": [r"enquiry_?no"], "indent": [r"indent_?no"],
    "rfq": [r"rfq"], "tax": [r"gst_?taxes", r"tax"], "taxes": [r"gst_?taxes", r"tax"],
}

# which text fields to look at first when a bare word like "indore" must be found in the data
NAME_PRIORITY = [r"^name$|name$|^name", r"city", r"region|state", r"supplier|customer|party|vendor|buyer",
                 r"address", r"status|auth", r"plant|department|designation|category|type",
                 r"code|no$|number", r"desc", r"email"]
MEASURE_PRIORITY = [r"grand.?total|gr_?total|total.?amount|net.?amount|invoice.?amount|amount",
                    r"total|value", r"qty|quantity", r"price|rate", r"weight|wt"]

# =============================================================== HINGLISH
_F = r"[a-z_]+(?:\s(?:code|no|type|name|id|rate|time|date|group|term|desc|line|1))?"
_V = r"\"[^\"]+\"|[^\s\"]+"
PATTERNS = [
    (r"\b(?:pichh?l[ae]|pichhla|last)\s+(?:ek\s+)?(?:mahine|mahina)\b", "last month"),
    (r"\b(?:is|iss|ye|yeh|isi)\s+(?:mahine|mahina)\b", "this month"),
    (r"\b(?:pichh?l[ae]|last)\s+(?:hafte|hafta)\b", "last week"),
    (r"\b(?:is|iss|ye|yeh|isi)\s+(?:hafte|hafta)\b", "this week"),
    (r"\b(?:pichh?l[ae]|last)\s+(?:saal|sal)\b", "last year"),
    (r"\b(?:is|iss|ye|yeh|isi)\s+(?:saal|sal)\b", "this year"),
    (r"\bpichh?l[ae]\s+(\d+)\s+din\b", r"last \1 days"),
    (r"\baa?j\b", "today"),
    (r"\bkal\b", "yesterday"),
    (r"(?<![\d\-])\b(\d[\d,.]*)\s+(?:aur|and)\s+(\d[\d,.]*)\s+ke\s+beech(?:\s+(?:me|mein))?\b", r"between \1 and \2"),
    (r"(?<![\d\-])\b(\d[\d,.]*)\s+se\s+(?:zyada|jyada|jada|upar|oopar|bada|badi|adhik)\b", r"greater than \1"),
    (r"(?<![\d\-])\b(\d[\d,.]*)\s+se\s+(?:kam|neeche|niche|chhota|chota|chhoti)\b", r"less than \1"),
    (r"\bsabse\s+(?:zyada|jyada|jada|bada|badi|bara|adhik)\b", "highest"),
    (r"\bsabse\s+(?:kam|chhota|chhoti|chota)\b", "lowest"),
    (r"\b(?:pehle|pahle)\s+(\d+)\b", r"first \1"),
    (r"\b(?:aakhri|akhri|naye|nayi)\s+(\d+)\b", r"latest \1"),
    (r"\b(?:mobile|phone|fone|contact)\s+(?:number|no|nambar|num)\b", "contact no"),
    (rf"\b({_F})\s+ke\s+hisa?a?b\s+se\b", r"by \1"),
    (rf"\b({_F})\s+wise\b", r"by \1"),
    (rf"\b({_F})\s+({_V})\s+se\s+shuru(?:\s+(?:hone\s+wale|hone\s+wala|hota\s+hai|hote\s+hain|hoti\s+hai))?", r"\1 starts with \2"),
    (rf"\b({_F})\s+({_V})\s+se\s+khatam(?:\s+(?:hone\s+wale|hone\s+wala|hota\s+hai|hote\s+hain|hoti\s+hai))?", r"\1 ends with \2"),
    (rf"\b({_F})\s+(?:me|mein)\s+({_V})\s+(?:hai|ho|hain|aata\s+hai)\b", r"\1 contains \2"),
    (rf"\b(?:jinka|jiska|jiski|jinki|jinke|jiske)\s+({_F})\s+({_V})\s+nahi(?:\s+hai|\s+hain)?", r"\1 != \2"),
    (rf"\b(?:jinka|jiska|jiski|jinki|jinke|jiske)\s+({_F})\s+({_V})\s+(?:hai|hain|ho)\b", r"\1 = \2"),
    (rf"\b({_F})\s+({_V})\s+(?:wale|wali|waale|waali)\b", r"\1 = \2"),
]
WORDS = {
    "kitne": "how many", "kitni": "how many", "kitna": "how many", "kul": "total", "ausat": "average",
    "sabhi": "all", "sab": "all", "saare": "all", "saari": "all", "poore": "all",
    "naam": "name", "nam": "name", "shehar": "city", "sheher": "city", "pata": "address", "pate": "address",
    "mobile": "contact no", "phone": "contact no", "fone": "contact no",
    "vendor": "supplier", "vendors": "suppliers", "grahak": "customer",
    "po": "purchase order", "purchase": "purchase orders",
    "sale": "invoice", "sales": "invoices",
    "maal": "item", "kharid": "purchase", "bikri": "sales",
    "invoice": "invoice", "stock": "stock", "grn": "grn", "mrn": "mrn",
    "mashin": "machine", "mashine": "machine", "machin": "machine",
    "karmchari": "operator", "karigar": "operator", "mazdoor": "operator", "worker": "operator",
    "workers": "operators", "employee": "operator", "employees": "operators", "staff": "operators",
    "vibhag": "department", "rajya": "state", "desh": "country", "tarikh": "date", "tareekh": "date",
    "kimat": "rate", "keemat": "rate", "daam": "rate", "bhav": "rate", "aur": "and", "pura": "total",
    "production": "production",
    "rework": "rework",
    "rejection": "rejection",
    "reject": "reject",
}
DROP = set("""ka ki ke ko ne me mein par pe se hai hain ho hu hun tha thi hua hui huye kya dikhao dikhaiye
dikha batao bataiye bata nikalo nikaalo dijiye chahiye dekho dekhiye karo kariye kijiye wale wali waale waali
jinka jiska jiski jinke jiske jisme jinki jinko jisko jo jahan jaha""".split())


def _outside_quotes(text, fn):
    parts = re.split(r"(\"[^\"]*\"|'[^']*')", text)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


PHRASES = [
    (r"\b(?:waiting for|awaiting|yet to be|not yet)\s+approv(?:al|ed)\b|\bapproval pending\b|\bpending approval\b", "pending"),
    (r"\bnot verified(?: yet)?\b", "unverified"),
    (r"\b(?:biggest|largest|costliest)\b", "highest"),
    (r"\b(?:other than|apart from|except for|except|excluding|ke alawa|ke ilawa|ko chhodkar|chhodkar)\b", "not"),
    (r"\b(?:kaha|kahan|kidhar)\b", "address city"),
    (r"\bpending po\b", "purchase orders where approved status = pending"),
    (r"\bpending purchase order\b", "purchase orders where approved status = pending"),
    (r"\bpending invoice\b", "invoices where status = pending"),
    (r"\bpending irn\b", "irn pending"),
    (r"\btoday purchase\b", "purchase orders today"),
    (r"\btoday po\b", "purchase orders today"),
    (r"\btoday sales\b", "invoices today"),
    (r"\btoday invoice\b", "invoices today"),
    (r"\btotal purchase\b", "total amount of purchase orders"),
    (r"\btotal sales\b", "total amount of invoices"),
    (r"\bpurchases?\b(?!\s+(?:orders?|quantity|lead|gl))", "purchase orders"),
]


def normalize(question):
    low = " " + str(question).strip().lower() + " "
    low = re.sub(r"(\w)'s\b", r"\1", low)                    # month's -> month
    for pat, rep in PHRASES + PATTERNS:
        low = re.sub(pat, rep, low)

    def words(t):
        agg = re.search(r"\b(total|sum|average|avg|highest|lowest|maximum|minimum|kul|ausat)\b", low)
        t = re.sub(r"\bkitn[aei]\b", " " if agg else "how many", t)
        t = re.sub(
            r"[a-z_]+",
            lambda m: (
                m.group(0)
                if m.group(0) == "purchase" and re.match(r"\s+orders?\b", t[m.end():])
                else WORDS.get(m.group(0), m.group(0))
            ),
            t,
        )
        return re.sub(r"[a-z_]+", lambda m: "" if m.group(0) in DROP else m.group(0), t)
    low = _outside_quotes(low, words)
    return re.sub(r"\s+", " ", low).strip().rstrip("?!").strip()


# =============================================================== SCHEMA (plain python, no Django)
def alias_variants(name, verbose=None):
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    out = {name.lower(), name.lower().replace("_", " "), re.sub(r"\s+", " ", spaced).lower().strip()}
    if verbose:
        out.add(str(verbose).lower())
    return out


class FieldInfo:
    """kind: text | num | date | bool | fk | id | other"""
    def __init__(self, name, kind, choices=None, verbose=None):
        self.name, self.kind, self.verbose = name, kind, verbose
        self.choices = [str(c) for c in choices] if choices else None


class ModelInfo:
    def __init__(self, label, aliases, fields):
        self.label, self.aliases = label, aliases
        self.fields = {f.name: f for f in fields}
        self.native, self.falias, self.synonyms = set(), {}, set()
        for f in fields:                                   # words engine.py already knows
            for a in alias_variants(f.name, f.verbose):
                self.falias.setdefault(a, f.name)
                self.native.add(a)
        for a, fname in FIELD_ALIASES.get(label, {}).items():
            if fname in self.fields and a not in self.falias:
                self.falias[a] = fname
                self.synonyms.add(a)
        for word, rxs in SYN.items():                      # generic synonyms
            if word in self.falias:
                continue
            for rx in rxs:
                hit = sorted((n for n in self.fields if re.search(rx, n, re.I)
                              and self.fields[n].kind not in ("id",)), key=len)
                if hit:
                    self.falias[word] = hit[0]
                    self.synonyms.add(word)
                    break
        ABBR = {"quot": "quotation", "enq": "enquiry", "qty": "quantity", "desc": "description",
                "dept": "department", "amt": "amount", "supp": "supplier", "no": "number"}
        for _ in range(2):
            for a, fname in list(self.falias.items()):
                toks = a.split()
                for i, t in enumerate(toks):
                    if t in ABBR:
                        alt = " ".join(toks[:i] + [ABBR[t]] + toks[i + 1:])
                        if alt not in self.falias:
                            self.falias[alt] = fname
                            self.synonyms.add(alt)
        for a, fname in list(self.falias.items()):                 # suppliers, departments ...
            if " " not in a and len(a) > 3 and not a.endswith("s") and a + "s" not in self.falias:
                self.falias[a + "s"] = fname
                self.native.add(a + "s") if a in self.native else self.synonyms.add(a + "s")
        self.choice_index = {}
        for f in fields:
            for c in f.choices or []:
                self.choice_index.setdefault(c.lower(), []).append(f.name)
        self.bool_words = {}
        for f in fields:
            if f.kind == "bool":
                w = re.sub(r"^is_?", "", f.name.lower())
                self.bool_words[w] = (f.name, True)
                self.bool_words["not " + w] = (f.name, False)
                self.bool_words["un" + w] = (f.name, False)
        self.sorted_falias = sorted(self.falias, key=len, reverse=True)

    def canon(self, fname):                                # how engine.py can spell the field
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", fname).replace("_", " ")
        return re.sub(r"\s+", " ", spaced).lower().strip()


class Schema:
    def __init__(self, models, alias_label):
        self.models = {m.label: m for m in models}
        self.alias_label = alias_label
        self.sorted_aliases = sorted(alias_label, key=len, reverse=True)


# =============================================================== PARSER
STOPWORDS = set("""the a an is are was were am be of to for from in on at by with and or not no please show give
me my all list display tell what which who whom whose has have had do does did can could will would should
that this these those it its there their here also only just then than i we you they he she any some every
each details detail info information record records data master masters get find search where how many
much total sum average avg mean count number highest lowest maximum minimum max min top bottom first latest
recent last most least best per wise order sort sorted ordered desc descending asc ascending between
greater less more equal equals contains containing like starts ends above below over under today yesterday
week month year days day ago vs versus made created raised placed issued done given sent have do our your us located situated based available present exist exists name's""".split())
UNITS = {"lakh": 100000, "lakhs": 100000, "lac": 100000, "lacs": 100000, "crore": 10000000,
         "crores": 10000000, "thousand": 1000, "k": 1000}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
NEUTRAL_ALIAS = {"All_Masters.Item": "party"}      # add  "party": "All_Masters.Item"  to EXTRA_ALIASES

OPS = [
    (r"between\s+(\S+)\s+and\s+(\S+)", "between"),
    (r"(?:greater than or equal to|at least|>=)\s*(\S+)", "gte"),
    (r"(?:less than or equal to|at most|<=)\s*(\S+)", "lte"),
    (r"(?:greater than|more than|above|over|>)\s*(\S+)", "gt"),
    (r"(?:less than|below|under|<)\s*(\S+)", "lt"),
    (r"(?:not equal to|is not|isnt|!=)\s*(\S+)", "ne"),
    (r"(?:contains|containing|like)\s+(\S+)", "contains"),
    (r"starts with\s+(\S+)", "starts"),
    (r"ends with\s+(\S+)", "ends"),
    (r"(?:equal to|equals|is|=|:)\s*(\S+)", "eq"),
]
NUMERIC_OPS = [o for o in OPS if o[1] in ("between", "gte", "lte", "gt", "lt")]
GROUP_BEFORE = re.compile(r"(?:\bby|\bper|\beach|\bfor each|\bgroup(?:ed)? by)\s*$")
ORDER_BEFORE = re.compile(r"\b(?:order|sort|sorted|ordered)\s+by\s*$")
WHICH_BEFORE = re.compile(r"\b(?:which|who|what)\s*$")
AGG_WORD = {"sum": "total", "avg": "average", "max": "highest", "min": "lowest"}


def wre(a):
    """whole-word regex for an alias (a hyphen is part of a word: 'rfq-1' is not 'rfq')"""
    return rf"(?<![\w\-]){re.escape(a)}(?![\w\-])"


class Plan:
    def __init__(self, low):
        self.low = low
        self.model = self.alias = self.message = self.canonical = None
        self.conds = []            # (field, op, value)
        self.count = False
        self.agg = self.measure = self.group = self.order = self.limit = self.rank = self.date = None
        self.proj, self.notes, self.alternatives, self.global_words = [], [], [], []
        self.idnum = None
        self.date_field = None
        self.syn_fields = set()
        self.rewrote = False


def _month_range(y, m):
    first = date(y, m, 1)
    return first, date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def parse_dates(text, mark, free, today=None):
    """-> ('kw', 'this month') | ('range', a, b) | None.   marks the words it used."""
    today = today or date.today()

    def hit(rx):
        for m in re.finditer(rx, text):
            if free(m.start(), m.end()):
                mark(m.start(), m.end())
                return m
        return None
    for rx, kw in [(r"\btoday(?:'s|s)?\b", "today"), (r"\byesterday(?:'s|s)?\b", "yesterday"),
                   (r"\bthis week\b", "this week"), (r"\bthis month\b", "this month"),
                   (r"\blast month\b", "last month"), (r"\bthis year\b", "this year")]:
        if hit(rx):
            return ("kw", kw)
    m = hit(r"\blast (\d+) days?\b")
    if m:
        return ("kw", f"last {m.group(1)} days")
    if hit(r"\blast week\b"):
        s = today - timedelta(days=today.weekday() + 7)
        return ("range", s, s + timedelta(days=6))
    if hit(r"\blast year\b"):
        return ("range", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    if hit(r"\bthis quarter\b"):
        return ("range", date(today.year, (today.month - 1) // 3 * 3 + 1, 1), today)
    m = hit(r"\blast (\d+) months?\b")
    if m:
        k = today.month - 1 - int(m.group(1))
        return ("range", date(today.year + k // 12, k % 12 + 1, 1), today)
    m = hit(r"\b(?:in |during )?(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b")
    if m:
        mi = MONTHS.index(m.group(1)) + 1
        y = int(m.group(2)) if m.group(2) else (today.year if mi <= today.month else today.year - 1)
        return ("range",) + _month_range(y, mi)
    return None


class Parser:
    def __init__(self, low, schema, probe, viewable=None):
        self.low, self.schema, self.probe = low, schema, probe
        self.viewable = viewable or (lambda label: True)
        self.qv = {}

        def stash(m):
            k = f"qv{len(self.qv)}x"
            self.qv[k] = m.group(0)[1:-1].strip()
            return f" {k} "
        self.text = re.sub(r"\"[^\"]*\"|'[^']*'", stash, low)
        self.used = [False] * len(self.text)
        self.P = Plan(low)

    # ------------------------------------------------------------ helpers
    def mark(self, s, e):
        for i in range(s, e):
            self.used[i] = True

    def free(self, s, e):
        return not any(self.used[s:e])

    def has(self, rx):
        for m in re.finditer(rx, self.text):
            if self.free(m.start(), m.end()):
                self.mark(m.start(), m.end())
                return m
        return None

    def take_value(self, pos):
        """value that starts at/after pos -> (value, end) or None.  Plain words may be several words."""
        text = self.text
        m = re.match(r"\s*(\S+)", text[pos:])
        if not m:
            return None
        s, e, tok = pos + m.start(1), pos + m.end(1), m.group(1)
        if not self.free(s, e):
            return None
        if tok in self.qv:
            self.mark(s, e)
            return self.qv[tok], e
        if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", tok):
            num = float(tok.replace(",", ""))
            m2 = re.match(r"\s*([a-z]+)\b", text[e:])
            if m2 and m2.group(1) in UNITS and self.free(e, e + m2.end()):
                num *= UNITS[m2.group(1)]
                e += m2.end()
            self.mark(s, e)
            return (str(int(num)) if num == int(num) else str(num)), e
        if tok in STOPWORDS or re.fullmatch(r"[=<>!:]+", tok):
            return None
        words, end = [tok], e
        while True:
            m3 = re.match(r"\s*(\S+)", text[end:])
            if not m3:
                break
            s3, e3, t3 = end + m3.start(1), end + m3.end(1), m3.group(1)
            if (not self.free(s3, e3) or t3 in STOPWORDS or t3 in self.qv
                    or re.fullmatch(r"[\d,.=<>!:]+", t3)):
                break
            words.append(t3)
            end = e3
        self.mark(s, end)
        return " ".join(words), end

    # ------------------------------------------------------------ steps
    def run(self):
        P, text, schema = self.P, self.text, self.schema
        for m in re.finditer(r"\b(?:how many|total number of|number of|count of|count)\b", text):
            P.count = True
            self.mark(m.start(), m.end())
        self.limit_end = None
        m = re.search(r"\b(top|bottom|first|latest|recent|last)\s+(\d+)\b(?!\s*(?:days?|weeks?|months?|years?))", text)
        if m:
            P.limit = (m.group(1), int(m.group(2)))
            self.limit_end = m.end()
            self.mark(m.start(), m.end())
        m = re.search(r"\b(latest|newest|recent)\b(?!\s+\d)", text)
        if m and not P.limit and self.free(m.start(), m.end()):
            P.limit = ("latest", 0)                      # n decided after the table is known
            self.mark(m.start(), m.end())
            P.rewrote = True
        m = re.search(r"\b(\d+)\s+(highest|top|best|lowest|smallest|cheapest)\b", text)
        if m and self.free(m.start(), m.end()):
            P.limit = ("top" if m.group(2) in ("highest", "top", "best") else "bottom", int(m.group(1)))
            P.rewrote = True
            self.mark(m.start(), m.end())
        P.date = parse_dates(text, self.mark, self.free)
        if not self.pick_model():
            return P
        M = P.model

        # "total production entry/entries" means the number of records,
        # not the sum of a numeric column. If a quantity/measure is named,
        # normal aggregation logic below still handles it as a SUM.
        if M.label == "Production.ProductionEntry" and not P.count:
            asks_for_records = bool(re.search(r"\b(?:entry|entries|record|records)\b", text))
            asks_for_total = bool(re.search(r"\btotal\b", text))
            names_measure = bool(re.search(
                r"\b(?:qty|quantity|rework|reject(?:ed)?|scrap|time|hours|electricity|target|amount|value|weight)\b",
                text,
            ))
            if asks_for_total and asks_for_records and not names_measure:
                P.count = True

        if P.limit and P.limit[1] == 0:
            P.limit = ("latest", 5 if P.alias.endswith("s") else 1)
        self.find_fields()
        for a in M.aliases:                                       # other spellings of this table's name
            for m in re.finditer(wre(a), self.text):
                if self.free(m.start(), m.end()):
                    self.mark(m.start(), m.end())
        for a, s0, e0 in getattr(self, "other", []):
            if self.free(s0, e0):
                self.mark(s0, e0)
                P.notes.append(f"could not combine with '{a}'")
        self.conditions()
        self.group_order_rank()
        self.aggregate()
        self.bare_numeric()
        self.ids()
        self.values()
        self.finish()
        return P

    def pick_model(self):
        P, text, schema = self.P, self.text, self.schema
        cands, spans = {}, []
        for a in schema.sorted_aliases:
            m = re.search(wre(a), text)
            if not m or not self.free(m.start(), m.end()):
                continue
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            spans.append((m.start(), m.end()))
            cands.setdefault(schema.alias_label[a], (a, m.start(), m.end()))
        if not cands:
            import difflib
            fixed = False
            for m in re.finditer(r"[a-z]{5,}", text):
                if m.group(0) in STOPWORDS:
                    continue
                close = difflib.get_close_matches(m.group(0), list(schema.alias_label), n=1, cutoff=0.82)
                if close and self.free(m.start(), m.end()):
                    text = text[:m.start()] + close[0] + text[m.end():]
                    self.text = text
                    self.used = self.used[:m.start()] + [False] * len(close[0]) + self.used[m.end():]
                    fixed = True
                    break
            if fixed:
                return self.pick_model()
            return self.infer_model()

        def score(item):
            label, (a, s, e) = item
            mi = schema.models[label]
            hits = 0
            for fa in mi.falias:
                if fa != "id" and re.search(wre(fa), text):
                    hits += 1 if fa in mi.synonyms else 2          # own field names count more
            return (-hits, s)
        label, (alias, s0, e0) = min(cands.items(), key=score)
        P.model, P.alias = schema.models[label], alias
        M = P.model
        self.mark(s0, e0)
        self.other = [(a, s, e) for other, (a, s, e) in cands.items() if other != label and a not in M.falias]
        return True

    def infer_model(self):
        """no model word ("shiv ka mobile number"): use the field words + the data to find the table."""
        P, text, schema = self.P, self.text, self.schema
        words = [w for w in re.findall(r"[a-z0-9_.@&\-]+", text) if w not in STOPWORDS and not w.isdigit()]
        phrases = []
        cur = []
        for tok in re.findall(r"\S+", text):
            if tok in STOPWORDS or tok.isdigit():
                if cur:
                    phrases.append(" ".join(cur))
                    cur = []
            else:
                cur.append(self.qv.get(tok, tok))
        if cur:
            phrases.append(" ".join(cur))
        best = []
        for label, mi in schema.models.items():
            if not self.viewable(label):
                continue
            fw = [w for w in words if w in mi.falias]
            if not fw:
                continue
            vals = [p for p in phrases if not all(w in mi.falias for w in p.split())]
            nf = [f.name for f in mi.fields.values() if f.kind == "text"][:30]
            got, prio = 0, 99
            for p in vals:
                w = self.strip_fields(p, mi)
                hits = self.probe.hits(label, nf, w) if w else {}
                if hits:
                    got += 1
                    prio = min([prio] + [next((i for i, rx in enumerate(NAME_PRIORITY) if re.search(rx, h, re.I)), 98)
                                         for h in hits])
            if got:
                best.append((got, len(fw), -prio, label))
        best.sort(reverse=True)
        if best and (len(best) == 1 or best[0][:3] != best[1][:3]):
            mi = schema.models[best[0][3]]
            P.model = mi
            P.alias = NEUTRAL_ALIAS.get(mi.label) or mi.aliases[0]
            P.rewrote = True
            return True

        # If every remaining word is a field term, infer a table only when
        # exactly one viewable model matches all terms and at least one term
        # is a model-specific alias (not just a generic word like "quantity").
        # This supports questions such as "total rejected quantity" safely.
        field_only = []
        for label, mi in schema.models.items():
            if not self.viewable(label):
                continue
            matched = [w for w in words if w in mi.falias]
            if words and len(matched) == len(words) and any(w not in SYN for w in matched):
                field_only.append(label)
        if len(field_only) == 1:
            mi = schema.models[field_only[0]]
            P.model = mi
            P.alias = NEUTRAL_ALIAS.get(mi.label) or mi.aliases[0]
            P.rewrote = True
            return True

        P.global_words = [self.qv.get(w, w) for w in re.findall(r"[a-z0-9_.@&\-]+", text)
                          if w not in STOPWORDS and not w.isdigit()]
        return False

    @staticmethod
    def strip_fields(phrase, mi):
        ws = [w for w in phrase.split() if w not in mi.falias]
        return " ".join(ws)

    def find_fields(self):
        M, text = self.P.model, self.text
        self.found = []
        for fa in M.sorted_falias:
            if M.fields[M.falias[fa]].kind == "id":
                continue
            for m in re.finditer(wre(fa), text):
                if self.free(m.start(), m.end()):
                    self.mark(m.start(), m.end())
                    self.found.append((m.start(), m.end(), M.falias[fa], fa))
                    if fa in M.synonyms:
                        self.P.rewrote = True
                        self.P.syn_fields.add(M.falias[fa])
        self.found.sort()
        self.field_used = set()

    def conditions(self):
        """<field> <op> <value>    and    <field> <value>"""
        P, M, text = self.P, self.P.model, self.text
        for fs, fe, fname, fa in self.found:
            before = text[:fs]
            if GROUP_BEFORE.search(before) or ORDER_BEFORE.search(before) or WHICH_BEFORE.search(before):
                continue
            got = None
            for rx, op in OPS:
                m = re.match(r"\s*" + rx, text[fe:])
                if not m:
                    continue
                if op == "between":
                    self.mark(fe, fe + m.end())
                    got = (op, (self.qv.get(m.group(1), m.group(1)), self.qv.get(m.group(2), m.group(2))))
                else:
                    tv = self.take_value(fe + m.start(1))
                    if tv:
                        self.mark(fe, fe + m.start(1))
                        got = (op, tv[0])
                break
            if not got:
                tv = self.take_value(fe)
                if tv:
                    got = ("auto", tv[0])
                    P.rewrote = True
            if got:
                P.conds.append((fname, got[0], got[1]))
                self.field_used.add(fname)

    def group_order_rank(self):
        P, M, text = self.P, self.P.model, self.text
        self.group_cand = None
        for fs, fe, fname, fa in self.found:
            if fname in self.field_used:
                continue
            before = text[:fs]
            if ORDER_BEFORE.search(before):
                m = re.match(r"\s*(desc|descending|asc|ascending)\b", text[fe:])
                P.order = (fname, "desc" if m and m.group(1).startswith("desc") else "asc")
                if m:
                    self.mark(fe, fe + m.end())
                self.field_used.add(fname)
            elif WHICH_BEFORE.search(before) and re.search(r"\b(?:most|highest|maximum|max|least|lowest|minimum|min|top|best)\b", text):
                P.group = fname
                P.rank = "asc" if re.search(r"\b(?:least|lowest|minimum|min|smallest)\b", text) else "desc"
                P.rewrote = True
                self.field_used.add(fname)
            elif GROUP_BEFORE.search(before):
                self.group_cand = fname
                self.field_used.add(fname)
            elif (P.limit and P.limit[0] in ("top", "bottom") and self.limit_end is not None
                  and text[self.limit_end:fs].strip() == "" and M.fields[fname].kind in ("text", "fk")
                  and re.search(r"\bby\b", text[fe:])):
                P.group = fname                                   # top 5 suppliers by PO value
                P.rank = "asc" if P.limit[0] == "bottom" else "desc"
                P.rewrote = True
                self.field_used.add(fname)

    def aggregate(self):
        P, M, text = self.P, self.P.model, self.text
        if not P.count:
            for rx, a in [(r"\b(?:average|avg|mean)\b", "avg"), (r"\b(?:sum|total)\b", "sum"),
                          (r"\b(?:highest|maximum|max|largest)\b", "max"), (r"\b(?:lowest|minimum|min|smallest|cheapest)\b", "min")]:
                if self.has(rx):
                    P.agg = a
                    break
        self.has(r"\b(?:most|least|best|top)\b")
        for fs, fe, fname, fa in self.found:                      # a named numeric field = what to total / sort
            if fname not in self.field_used and M.fields[fname].kind == "num":
                P.measure = P.measure or fname
                self.field_used.add(fname)
        gc = self.group_cand
        if gc:
            if M.fields[gc].kind == "num" and (P.limit or P.agg or P.measure is None):
                P.measure = P.measure or gc                      # "top 5 X by rate" = sort, not group
            else:
                P.group = gc
        if P.rank and P.agg in ("max", "min"):
            P.agg = None
        if (P.group or P.rank) and P.measure and not P.agg and not P.count:
            P.agg = "sum"
        need = P.agg or (P.limit and P.limit[0] in ("top", "bottom")) or (P.rank and P.measure)
        if need and not P.measure:
            P.measure = self.default_measure()
            if not P.measure and P.agg:
                nums = [f.name for f in M.fields.values() if f.kind == "num"]
                P.message = "Which field should I use for that? Numeric fields: " + (", ".join(nums[:12]) or "none") + "."

    def default_measure(self):
        M = self.P.model
        nums = [f.name for f in M.fields.values() if f.kind == "num"]
        dm = DEFAULT_MEASURE.get(M.label)
        pick = next((x for x in nums if dm and x.lower() == dm.lower()), None)
        for rx in MEASURE_PRIORITY[:3]:
            pick = pick or next((x for x in nums if re.search(rx, x, re.I)), None)
        return pick

    def bare_numeric(self):
        """'above 1 lakh', 'between 50,000 and 2,00,000'  (no field named) -> the main amount field"""
        P = self.P
        for rx, op in NUMERIC_OPS:
            for m in re.finditer(rx, self.text):
                if not self.free(m.start(), m.end()):
                    continue
                field = P.measure or self.default_measure()
                if not field:
                    P.notes.append("which field? name it, e.g. 'amount above 5000'")
                    return
                if op == "between":
                    self.mark(m.start(), m.end())
                    P.conds.append((field, op, (self.qv.get(m.group(1), m.group(1)), self.qv.get(m.group(2), m.group(2)))))
                else:
                    tv = self.take_value(m.start(1))
                    if not tv:
                        continue
                    self.mark(m.start(), m.start(1))
                    P.conds.append((field, op, tv[0]))
                P.rewrote = True

    def ids(self):
        P, text = self.P, self.text
        for m in re.finditer(r"(?:#|\bid\s*(?:=|:|is)?\s*)(\d+)\b", text):
            if self.free(m.start(), m.end()):
                P.idnum = int(m.group(1))
                self.mark(m.start(), m.end())
        m = re.search(wre(P.alias) + r"\s+(\d+)\b", text)
        if m and self.free(m.start(1), m.end(1)):
            P.idnum = int(m.group(1))
            self.mark(m.start(1), m.end(1))

    def values(self):
        """leftover words are looked up in the data"""
        P, M, text = self.P, self.P.model, self.text
        groups, cur = [], []
        self.span_of = {}
        for m in re.finditer(r"[a-z0-9_.@&/\-]+", text):
            w = m.group(0)
            if (not self.free(m.start(), m.end()) or w in STOPWORDS or re.fullmatch(r"[\d.,\-]+", w)):
                if cur:
                    groups.append(("words", cur))
                    cur = []
            elif w in self.qv:
                if cur:
                    groups.append(("words", cur))
                    cur = []
                groups.append(("quoted", [self.qv[w]], m.start(), m.end()))
            else:
                cur.append(w)
                self.span_of[w] = self.span_of.get(w, (m.start(), m.end()))
        if cur:
            groups.append(("words", cur))
        fields = [f.name for f in M.fields.values() if f.kind == "text"]
        fields.sort(key=lambda x: next((i for i, rx in enumerate(NAME_PRIORITY) if re.search(rx, x, re.I)), 99))
        fields = fields[:30]
        P_label = M.label

        def place(phrase):
            key = phrase.lower()
            singular = key[:-1] if key.endswith("s") and len(key) > 3 else None
            for k in (key, singular):
                if not k:
                    continue
                if k in M.bool_words:
                    return M.bool_words[k][0], "bool", M.bool_words[k][1]
                if k in M.choice_index:
                    return M.choice_index[k][0], "eq", phrase
                hits = self.probe.hits(P_label, fields, k)
                if hits:
                    order = sorted(hits, key=lambda fn: (not hits[fn][0], fields.index(fn)))
                    return order[0], ("eq" if hits[order[0]][0] else "contains"), k
            return None
        def negated(first, last):
            """'not in indore' / 'indore nahi hai' / 'other than indore'"""
            if first is None or last is None:
                return False
            s0, e0 = first[0], last[1]
            return bool(re.search(r"\b(?:not|no|nahi|nhi)(?:\s+(?:in|from|at|of|equal to|is))?\s*$", text[:s0])
                        or re.match(r"\s*(?:nahi|nhi|not)\b", text[e0:]))
        for kind, ws, *pos in groups:
            i = 0
            while i < len(ws):
                done = False
                for j in range(len(ws), i, -1):
                    got = place(" ".join(ws[i:j]))
                    if got:
                        if pos:
                            first = last = (pos[0], pos[1])
                        else:
                            first, last = self.span_of.get(ws[i]), self.span_of.get(ws[j - 1])
                        if got[1] != "bool" and negated(first, last):
                            got = (got[0], "ne", got[2])
                        P.conds.append(got)
                        P.rewrote = True
                        i, done = j, True
                        break
                    if kind == "quoted":
                        break
                if not done:
                    P.notes.append(f"could not match: {ws[i]}")
                    i += 1

    def finish(self):
        P, M = self.P, self.P.model
        has_filter_field = {c[0] for c in P.conds}
        P.proj = [f for _, _, f, _ in self.found
                  if f not in self.field_used and f not in has_filter_field and f != P.measure]
        P.date_field = None
        if P.date:
            P.date_field = next((f for f in P.proj if M.fields[f].kind == "date"), None)
            if P.date_field:
                P.proj.remove(P.date_field)
                P.rewrote = True
        if P.count or P.agg or P.group or P.rank or P.limit:
            P.proj = []
        if P.proj:
            P.rewrote = True
        if P.date and P.date[0] == "range":
            P.rewrote = True


def parse(low, schema, probe, viewable=None):
    return Parser(low, schema, probe, viewable).run()


# =============================================================== CANONICAL QUESTION
def _q(x):
    return '"' + str(x).replace('"', "") + '"'


def _n(v):
    return str(v).replace(",", "")


def cond_text(M, fname, op, v):
    f = M.canon(fname)
    if op == "bool":
        return f"{f} = {'yes' if v else 'no'}"
    if op == "between":
        return f"{f} between {_n(v[0])} and {_n(v[1])}"
    if op in ("gt", "gte", "lt", "lte"):
        return f"{f} {dict(gt='>', gte='>=', lt='<', lte='<=')[op]} {_n(v)}"
    word = {"ne": "is not", "contains": "contains", "starts": "starts with", "ends": "ends with"}.get(op, "=")
    return f"{f} {word} {_q(v)}"


def build_canonical(P, probe):
    """P.canonical = the sentence engine.py understands (P.alternatives when the user meant 'A or B')."""
    M, label = P.model, P.model.label
    fixed = []
    for fname, op, v in P.conds:
        if op in ("auto", "eq", "contains") and (op == "auto" or fname in P.syn_fields) and M.fields[fname].kind == "text":
            hits = probe.hits(label, [fname], v)
            if not hits:              # "state 23": 23 is in State_Code
                tf = [f.name for f in M.fields.values() if f.kind == "text"][:30]
                allhits = probe.hits(label, tf, v)
                if allhits:
                    fname = sorted(allhits, key=lambda x: (not allhits[x][0], tf.index(x)))[0]
                    hits = {fname: allhits[fname]}
            if op == "auto":
                op = "eq" if hits.get(fname, (False, False))[0] else ("contains" if hits else "eq")
            elif hits and not hits.get(fname, (False, False))[0] and op == "eq":
                op = "contains" if not any(h[0] for h in hits.values()) else "eq"
        fixed.append((fname, op, v))
    byf = {}
    for c in fixed:
        if c[1] in ("eq", "contains"):
            byf.setdefault(c[0], []).append(c)
    or_field = next((f for f, cs in byf.items() if len(cs) > 1), None)
    base = [c for c in fixed if not (or_field and c[0] == or_field and c[1] in ("eq", "contains"))]
    variants = [[c] for c in byf[or_field]] if or_field else [[]]
    if or_field:
        P.rewrote = True
    pf = P.date_field or PRIMARY_DATE.get(label)
    if not (pf and pf in M.fields):
        pf = next((f.name for f in M.fields.values() if f.kind == "date"), None)

    def one(extra):
        al = P.alias
        cond = " and ".join(cond_text(M, *c) for c in base + extra)
        cond = f" where {cond}" if cond else ""
        dt = ""
        if P.date:
            if P.date[0] == "kw":
                dt = f" {M.canon(pf) + ' ' if pf else ''}{P.date[1]}"
            elif pf:
                dt = f" {M.canon(pf)} between {P.date[1].isoformat()} and {P.date[2].isoformat()}"
        by = f" by {M.canon(P.group)}" if P.group else ""
        meas = M.canon(P.measure) if P.measure else None
        order = f" order by {M.canon(P.order[0])} {P.order[1]}" if P.order else ""
        if P.idnum is not None and not cond and not P.agg and not P.count:
            return f"get {al} {P.idnum}"
        if P.rank:
            head = f"total {meas} of {al}" if P.agg and meas else f"count {al}"
            return f"{head}{cond}{by}{dt}"
        if P.group:
            if P.agg and meas:
                return f"{AGG_WORD[P.agg]} {meas} of {al}{cond}{by}{dt}"
            return f"count {al}{cond}{by}{dt}"
        if P.agg and meas:
            return f"{AGG_WORD[P.agg]} {meas} of {al}{cond}{dt}"
        if P.count:
            return f"how many {al}{cond}{dt}"
        if P.limit and P.limit[0] in ("top", "bottom") and meas:
            return f"first {P.limit[1]} {al}{cond}{dt} order by {meas} {'desc' if P.limit[0] == 'top' else 'asc'}"
        if P.limit:
            k = "first" if P.limit[0] in ("first", "top", "bottom") else "latest"
            return f"{k} {P.limit[1]} {al}{cond}{dt}{order}"
        return f"show all {al}{cond}{dt}{order}"
    P.canonical = one(variants[0])
    if or_field:
        P.alternatives = [one(v) for v in variants]
    return P


def describe(P):
    s = "Understood as: " + (P.canonical or "")
    if len(P.alternatives) > 1:
        s = "Understood as: " + "  OR  ".join(P.alternatives)
    return s


def name_field(M):
    for rx in NAME_PRIORITY[:1] + [r"desc", r"code|no$|number"]:
        hit = next((f.name for f in M.fields.values() if f.kind == "text" and re.search(rx, f.name, re.I)), None)
        if hit:
            return hit
    return None


# =============================================================== DJANGO SIDE
_SCHEMA = {}
_CACHE = {}


def get_schema(engine):
    """Built once from engine.models (every model, every field)."""
    if "s" in _SCHEMA:
        return _SCHEMA["s"]
    from django.db import models as dm
    from .engine import _is_sensitive, _is_text, _kind
    by_label, alias_label = {}, {}
    for alias, m in engine.models.items():
        by_label.setdefault(m._meta.label, m)
        alias_label[alias] = m._meta.label
    for alias, label in NEUTRAL_EXTRA.items():
        if label in by_label:
            alias_label[alias] = label
    infos = []
    for label, m in by_label.items():
        fields = []
        for f in m._meta.concrete_fields:
            if _is_sensitive(f.name):
                continue
            if f.primary_key:
                kind = "id"
            elif f.is_relation:
                kind = "fk"
            elif isinstance(f, dm.BooleanField):
                kind = "bool"
            else:
                kind = _kind(f) or ("text" if _is_text(f) else "other")
            fields.append(FieldInfo(f.name, kind, [c[0] for c in f.choices] if f.choices else None, f.verbose_name))
        al = sorted((a for a, l in alias_label.items() if l == label), key=lambda a: (not a.endswith("s"), len(a)))
        infos.append(ModelInfo(label, al, fields))
    _SCHEMA["s"] = Schema(infos, alias_label)
    _SCHEMA["models"] = by_label
    return _SCHEMA["s"]


NEUTRAL_EXTRA = {"party": "All_Masters.Item", "parties": "All_Masters.Item"}


class DjangoProbe:
    """'does this word exist in that column?'  (one cheap exists() query, cached for a minute)"""
    def __init__(self, engine, user):
        self.user = user
        get_schema(engine)
        self.models = _SCHEMA["models"]

    def hits(self, label, fields, text):
        import time
        from django.db.models import Q
        from .engine import can_view, scope_queryset
        key = (self.user.pk, label, tuple(fields), text.lower())
        got = _CACHE.get(key)
        if got and time.time() - got[0] < 60:
            return got[1]
        model = self.models[label]
        out = {}
        if fields and can_view(self.user, model):
            qs = scope_queryset(self.user, model, model._default_manager.all())
            q = Q()
            for f in fields:
                q |= Q(**{f + "__icontains": text})
            if qs.filter(q).exists():
                for f in fields:
                    if qs.filter(**{f + "__icontains": text}).exists():
                        out[f] = (qs.filter(**{f + "__iexact": text}).exists(), True)
        _CACHE[key] = (time.time(), out)
        return out


def _any_hit(schema, user, phrase):
    from django.db.models import Q
    from .engine import can_view
    for label, mi in schema.models.items():
        model = _SCHEMA["models"][label]
        fields = [f.name for f in mi.fields.values() if f.kind == "text"][:30]
        if fields and can_view(user, model):
            q = Q()
            for f in fields:
                q |= Q(**{f + "__icontains": phrase})
            if model._default_manager.filter(q).exists():
                return True
    return False


def _global_search(engine, user, P, schema, probe):
    """no table named, no table guessed: where does this word exist?"""
    from django.db.models import Q
    from .engine import can_view, scope_queryset
    words = [w for w in P.global_words if len(w) >= 2]
    if not words:
        return None
    phrase = " ".join(words)
    if phrase.endswith("s") and len(phrase) > 4 and not _any_hit(schema, user, phrase):
        phrase = phrase[:-1]
    rows = []
    for label, mi in schema.models.items():
        model = _SCHEMA["models"][label]
        fields = [f.name for f in mi.fields.values() if f.kind == "text"]
        fields.sort(key=lambda x: next((i for i, rx in enumerate(NAME_PRIORITY) if re.search(rx, x, re.I)), 99))
        fields = fields[:30]
        if not fields or not can_view(user, model):
            continue
        q = Q()
        for f in fields:
            q |= Q(**{f + "__icontains": phrase})
        qs = scope_queryset(user, model, model._default_manager.filter(q))
        n = qs.count()
        if n:
            first = qs.order_by("pk").first()
            rows.append({"table": str(model._meta.verbose_name_plural).title(), "matches": n,
                         "example": str(first)[:60], "ask": f"{mi.aliases[0]} where ... (or: show all {mi.aliases[0]})"})
    if not rows:
        import difflib
        names = sorted({a for a in schema.alias_label if len(a) > 3})
        close = []
        for w in words:
            close += difflib.get_close_matches(w, names, n=2, cutoff=0.7)
        hint = (" Did you mean: " + ", ".join(dict.fromkeys(close)) + "?") if close else ""
        return {"ok": False, "type": "text", "message":
                f"I could not find '{phrase}' in any table.{hint} Try: 'show all suppliers', "
                "'suppliers where city = \"indore\"', 'how many machines'."}
    rows.sort(key=lambda r: -r["matches"])
    return {"ok": True, "type": "table", "message": f"'{phrase}' found in {len(rows)} table(s). Ask about one of them.",
            "columns": ["table", "matches", "example", "ask"], "rows": rows[:15], "total": len(rows)}


def answer_free(engine, question, user):
    """dict answer, or None = 'nothing to improve, let engine.py answer the normalized question'."""
    from .engine import can_view
    low = normalize(question)
    try:
        from . import custom_rules
        if any(re.search(p, low) for p, _ in custom_rules.RULES):
            return None                                        # irn / stock ... have their own rules
    except Exception:
        pass
    schema = get_schema(engine)

    # Search exact document numbers (PO, invoice, GRN, MRN, work order, item, RM)
    # across the text fields of viewable models before normal free-form parsing.
    doc_match = re.search(
        r"\b(?:PO|INV|GRN|MRN|WO|ITEM|RM)[-_]?[A-Z0-9]+\b",
        question,
        re.I,
    )
    if doc_match:
        class Temp:
            pass

        temp = Temp()
        temp.global_words = [doc_match.group(0)]
        probe = DjangoProbe(engine, user)
        return _global_search(engine, user, temp, schema, probe)

    probe = DjangoProbe(engine, user)
    viewable = lambda label: can_view(user, _SCHEMA["models"][label])
    P = Parser(low, schema, probe, viewable).run()
    if P.model is None:
        return _global_search(engine, user, P, schema, probe)
    if P.message:
        return {"ok": False, "type": "text", "message": P.message}
    build_canonical(P, probe)
    if not P.rewrote and not P.notes:
        return None
    cols = None
    if P.proj:
        nf = name_field(P.model)
        cols = ["id"] + ([nf] if nf and nf not in P.proj else []) + P.proj
    queries = P.alternatives or [P.canonical]
    results = []
    for qtext in queries:
        try:
            results.append(engine._answer(qtext, user, cols))
        except ValueError as e:
            results.append({"ok": False, "type": "text", "message": str(e)})
    res = results[0]
    if len(results) > 1 and all(r.get("ok") for r in results):
        if all(r["type"] == "scalar" for r in results):
            tot = sum(r["value"] for r in results)
            res = {"ok": True, "type": "scalar", "value": tot, "message": f"Total: {tot}"}
        elif all(r["type"] == "table" for r in results):
            seen, rows = set(), []
            for r in results:
                for row in r["rows"]:
                    k = row.get("id", id(row))
                    if k not in seen:
                        seen.add(k)
                        rows.append(row)
            res = dict(results[0], rows=rows[:20], total=len(rows),
                       message=f"Found {len(rows)} (any of: {', '.join(c[2] for c in P.conds if c[0] and c[1] in ('eq', 'contains'))})")
    res["understood"] = describe(P)
    if P.notes:
        res["message"] = (res.get("message") or "") + " (" + "; ".join(P.notes) + ")"
    return res
