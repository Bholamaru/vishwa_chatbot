"""
einvoice/services/payload_builder.py

Builds the JSON payload for the Generate IRN API from an Invoice instance.
Per-item GST is derived by distributing the invoice-level GstdetailsInvoice
totals across items proportionally to taxable value, and picking CGST+SGST
vs IGST based on whether the buyer's state code matches the seller's.
"""

from django.conf import settings


# Your own registered GSTIN / legal details — these don't change per
# invoice, so pull them from settings rather than the Invoice model.
SELLER_GSTIN = settings.EINVOICE_GSTIN
SELLER_LEGAL_NAME = settings.EINVOICE_SELLER_LEGAL_NAME       # TODO: add to settings
SELLER_TRADE_NAME = settings.EINVOICE_SELLER_TRADE_NAME       # TODO: add to settings
SELLER_ADDR1 = settings.EINVOICE_SELLER_ADDR1                 # TODO
SELLER_ADDR2 = settings.EINVOICE_SELLER_ADDR2                 # TODO
SELLER_LOC = settings.EINVOICE_SELLER_LOC                     # TODO
SELLER_PIN = settings.EINVOICE_SELLER_PIN                     # TODO
SELLER_STCD = settings.EINVOICE_SELLER_STCD                   # TODO (GST state code, e.g. "23" for MP)


def _fmt_date(d):
    """Convert a Python date to the DD/MM/YYYY string the API expects."""
    return d.strftime("%d/%m/%Y") if d else None


def _build_item_list(invoice, gst, same_state):
    """
    Build ItemList from InvoiceItemdetails. The GST rate comes from
    GstdetailsInvoice.igst (used as the single source of truth for the
    full rate), but CGST/SGST/IGST amounts are always CALCULATED from
    that rate and each item's taxable value — never taken directly from
    GstdetailsInvoice's stored amount fields, since those aren't
    guaranteed to be internally consistent with the rate (e.g. test/dummy
    data). This guarantees amounts always match what the portal expects.

    same_state=True  -> apply CGST+SGST (intra-state), each half the rate
    same_state=False -> apply IGST (inter-state), full rate
    """
    items_qs = list(invoice.items.all())
    if not items_qs:
        return []

    gst_rate_cgst = float(gst.cgst) if gst and gst.cgst else 0.0
    gst_rate_sgst = float(gst.sgst) if gst and gst.sgst else 0.0
    gst_rate_igst = float(gst.igst) if gst and gst.igst else 0.0

    items = []
    for idx, item in enumerate(items_qs, start=1):
        qty = item.inv_qty or 0
        unit_price = float(item.rate or 0)
        taxable_value = round(qty * unit_price, 2)

        if same_state:
            item_cgst = round(taxable_value * gst_rate_cgst / 100, 2)
            item_sgst = round(taxable_value * gst_rate_sgst / 100, 2)
            item_igst = 0
            gst_rate = gst_rate_cgst + gst_rate_sgst
        else:
            item_cgst = 0
            item_sgst = 0
            item_igst = round(taxable_value * gst_rate_igst / 100, 2)
            gst_rate = gst_rate_igst

        item_total = round(taxable_value + item_cgst + item_sgst + item_igst, 2)

        items.append({
            "SlNo": str(idx),
            "IsServc": "N",  # TODO: "Y" if this line is a service, not goods
            "PrdDesc": item.description or "",
            "HsnCd": item.hsn_code or "",
            "Qty": qty,
            "FreeQty": 0,
            "Unit": item.type_of_packing or "NOS",
            "UnitPrice": unit_price,
            "TotAmt": taxable_value,
            "Discount": float(item.dis or 0),
            "PreTaxVal": 0,
            "AssAmt": taxable_value,
            "GstRt": gst_rate,
            "CgstAmt": item_cgst,
            "SgstAmt": item_sgst,
            "IgstAmt": item_igst,
            "CesRt": 0,
            "CesAmt": 0,
            "TotItemVal": item_total,
        })
    return items


def build_generate_irn_payload(invoice):
    """
    Build the full Generate IRN payload for a given Invoice instance.
    Raises ValueError if required data is missing.
    """
    if not invoice.invoice_no or not invoice.invoice_Date:
        raise ValueError("Invoice must have invoice_no and invoice_Date set.")

    gst = invoice.GSTdetails.first()  # TODO: confirm this is one-per-invoice
    buyer_stcd = invoice.buyer_state_code or ""
    same_state = buyer_stcd == SELLER_STCD

    item_list = _build_item_list(invoice, gst, same_state)

    total_taxable = sum(i["AssAmt"] for i in item_list)
    total_cgst = sum(i["CgstAmt"] for i in item_list)
    total_sgst = sum(i["SgstAmt"] for i in item_list)
    total_igst = sum(i["IgstAmt"] for i in item_list)
    total_invoice_value = round(total_taxable + total_cgst + total_sgst + total_igst, 2)

    payload = {
        "Version": "1.1",
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": "B2B",   # TODO: derive from buyer type if you support B2C/exports
            "RegRev": "N",
            "EcmGstin": None,
            "IgstOnIntra": "N",
        },
        "DocDtls": {
            "Typ": "INV",
            "No": invoice.invoice_no,
            "Dt": _fmt_date(invoice.invoice_Date),
        },
        "SellerDtls": {
            "Gstin": SELLER_GSTIN,
            "LglNm": SELLER_LEGAL_NAME,
            "TrdNm": SELLER_TRADE_NAME,
            "Addr1": SELLER_ADDR1,
            "Addr2": SELLER_ADDR2,
            "Loc": SELLER_LOC,
            "Pin": SELLER_PIN,
            "Stcd": SELLER_STCD,
        },
        "BuyerDtls": {
            "Gstin": invoice.buyer_gstin or "",
            "LglNm": invoice.buyer_legal_name or invoice.bill_to or "",
            "TrdNm": invoice.buyer_legal_name or invoice.bill_to or "",
            "Pos": invoice.buyer_pos or invoice.buyer_state_code or "",
            "Addr1": invoice.buyer_addr1 or "",
            "Addr2": invoice.buyer_addr2 or "",
            "Loc": invoice.buyer_location or "",
            "Pin": int(invoice.buyer_pincode) if invoice.buyer_pincode else 0,
            "Stcd": invoice.buyer_state_code or "",
        },
        "ItemList": item_list,
        "ValDtls": {
            "AssVal": total_taxable,
            "CgstVal": total_cgst,
            "SgstVal": total_sgst,
            "IgstVal": total_igst,
            "CesVal": 0,
            "StCesVal": 0,
            "Discount": 0,
            "OthChrg": 0,
            "RndOffAmt": 0,
            "TotInvVal": total_invoice_value,
        },
    }

    # E-way bill is generated as a SEPARATE step via generate_ewaybill.py
    # once the IRN exists (not combined into this call) — that keeps the
    # two concerns independent and avoids partial-data validation errors
    # here if transport details aren't ready yet at IRN generation time.

    return payload