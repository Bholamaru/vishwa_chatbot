from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..models import (
    RMStock,
    RMStockLot,
    RMStockTransaction,
    MaterialIssueAllocation,
)


def decimal_value(value):
    try:
        return Decimal(str(value or 0))
    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):
        return Decimal("0")


def normalize(value):
    return (value or "").strip()


def extract_item_code(value):

    value = normalize(value)

    if "|" in value:
        return value.split("|")[0].strip()

    if "-" in value:
        return value.split("-")[0].strip()

    return value



@transaction.atomic
def add_stock(
    item_code,
    heat_no,
    qty,
    source,
    source_id=None,
    source_table=None,
    remark=None
):

    item_code = normalize(item_code)
    heat_no = normalize(heat_no)
    qty = decimal_value(qty)

    if not item_code:
        raise ValueError(
            "Item code is required."
        )

    if not heat_no:
        raise ValueError(
            "Heat number is required."
        )

    if qty <= 0:
        raise ValueError(
            "Stock quantity must be greater than zero."
        )

    # --------------------------------------------
    # Get / create aggregate stock
    # --------------------------------------------

    stock = (
        RMStock.objects
        .select_for_update()
        .filter(
            item_code__iexact=item_code,
            heat_no__iexact=heat_no
        )
        .first()
    )

    if stock:

        stock.qty += qty

        stock.save(
            update_fields=[
                "qty",
                "updated_at"
            ]
        )

    else:

        stock = RMStock.objects.create(
            item_code=item_code,
            heat_no=heat_no,
            qty=qty
        )

    # --------------------------------------------
    # Create source lot
    # --------------------------------------------

    lot = RMStockLot.objects.create(
        rm_stock=stock,
        item_code=item_code,
        heat_no=heat_no,
        qty=qty,
        source=source,
        source_id=source_id,
        source_table=source_table
    )

    # --------------------------------------------
    # Transaction
    # --------------------------------------------

    transaction_type = (
        "OPENING"
        if source == "OPENING"
        else "JOBWORK_GRN"
    )

    RMStockTransaction.objects.create(
        item_code=item_code,
        heat_no=heat_no,
        transaction_type=transaction_type,
        qty=qty,
        reference_id=source_id,
        reference_table=source_table,
        balance_after=stock.qty,
        remark=remark
    )

    return stock, lot


@transaction.atomic
def deduct_stock(
    item_code,
    heat_no,
    qty,
    material_challan,
    material_challan_table,
    remark=None
):

    item_code = normalize(item_code)
    heat_no = normalize(heat_no)
    qty = decimal_value(qty)

    if qty <= 0:
        raise ValueError(
            "Issue quantity must be greater than zero."
        )

    # --------------------------------------------
    # Lock aggregate stock
    # --------------------------------------------

    stock = (
        RMStock.objects
        .select_for_update()
        .filter(
            item_code__iexact=item_code,
            heat_no__iexact=heat_no
        )
        .first()
    )

    if not stock:

        raise ValueError(
            f"No stock found for "
            f"{item_code} / {heat_no}"
        )

    if stock.qty < qty:

        raise ValueError(
            f"Insufficient stock for "
            f"{item_code} / {heat_no}. "
            f"Available: {stock.qty}, "
            f"Required: {qty}"
        )

    remaining = qty

    # --------------------------------------------
    # FIFO lots
    # --------------------------------------------

    lots = (
        RMStockLot.objects
        .select_for_update()
        .filter(
            item_code__iexact=item_code,
            heat_no__iexact=heat_no,
            qty__gt=0
        )
        .order_by("created_at", "id")
    )

    for lot in lots:

        if remaining <= 0:
            break

        available = lot.qty

        deduction = min(
            available,
            remaining
        )

        lot.qty = available - deduction

        lot.save(
            update_fields=[
                "qty",
                "updated_at"
            ]
        )

        # ----------------------------------------
        # Allocation
        # ----------------------------------------

        MaterialIssueAllocation.objects.create(
            material_challan=material_challan,
            material_challan_table=material_challan_table,
            stock_lot=lot,
            item_code=item_code,
            heat_no=heat_no,
            qty=deduction,
            source=lot.source
        )

        remaining -= deduction

    if remaining > 0:

        raise ValueError(
            f"Unable to allocate "
            f"{remaining} quantity."
        )

    # --------------------------------------------
    # Update total stock
    # --------------------------------------------

    stock.qty -= qty

    stock.save(
        update_fields=[
            "qty",
            "updated_at"
        ]
    )

    # --------------------------------------------
    # Transaction
    # --------------------------------------------

    RMStockTransaction.objects.create(
        item_code=item_code,
        heat_no=heat_no,
        transaction_type="MATERIAL_ISSUE",
        qty=-qty,
        reference_id=material_challan.id,
        reference_table="MaterialChallan",
        balance_after=stock.qty,
        remark=remark
    )

    return stock



# @transaction.atomic
# def restore_material_issue(
#     material_challan
# ):

#     allocations = (
#         MaterialIssueAllocation.objects
#         .select_for_update()
#         .filter(
#             material_challan=material_challan
#         )
#     )

#     for allocation in allocations:

#         lot = (
#             RMStockLot.objects
#             .select_for_update()
#             .get(
#                 id=allocation.stock_lot_id
#             )
#         )

#         stock = (
#             RMStock.objects
#             .select_for_update()
#             .get(
#                 id=lot.rm_stock_id
#             )
#         )

#         qty = allocation.qty

#         lot.qty += qty

#         lot.save(
#             update_fields=[
#                 "qty",
#                 "updated_at"
#             ]
#         )

#         stock.qty += qty

#         stock.save(
#             update_fields=[
#                 "qty",
#                 "updated_at"
#                 ]
#             )

#         RMStockTransaction.objects.create(
#             item_code=allocation.item_code,
#             heat_no=allocation.heat_no,
#             transaction_type=(
#                 "MATERIAL_ISSUE_RESTORE"
#             ),
#             qty=qty,
#             reference_id=material_challan.id,
#             reference_table="MaterialChallan",
#             balance_after=stock.qty,
#             remark=(
#                 "Material Issue restored"
#             )
#         )

@transaction.atomic
def restore_material_issue(material_challan):

    allocations = (
        MaterialIssueAllocation.objects
        .select_for_update()
        .filter(
            material_challan=material_challan
        )
        .select_related("stock_lot")
    )

    for allocation in allocations:

        # -----------------------------------------
        # Lock the original lot
        # -----------------------------------------

        lot = (
            RMStockLot.objects
            .select_for_update()
            .get(
                id=allocation.stock_lot_id
            )
        )

        # -----------------------------------------
        # Lock aggregate stock
        # -----------------------------------------

        stock = (
            RMStock.objects
            .select_for_update()
            .get(
                id=lot.rm_stock_id
            )
        )

        qty = allocation.qty

        # -----------------------------------------
        # Restore LOT
        # -----------------------------------------

        lot.qty += qty

        lot.save(
            update_fields=[
                "qty",
                "updated_at"
            ]
        )

        # -----------------------------------------
        # Restore TOTAL STOCK
        # -----------------------------------------

        stock.qty += qty

        stock.save(
            update_fields=[
                "qty",
                "updated_at"
            ]
        )

        # -----------------------------------------
        # Transaction
        # -----------------------------------------

        RMStockTransaction.objects.create(
            item_code=allocation.item_code,
            heat_no=allocation.heat_no,
            transaction_type="MATERIAL_ISSUE_RESTORE",
            qty=qty,
            reference_id=material_challan.id,
            reference_table="MaterialChallan",
            balance_after=stock.qty,
            remark="Material Issue deleted/restored"
        )

    # -----------------------------------------
    # Delete allocations after restoration
    # -----------------------------------------

    allocations.delete()