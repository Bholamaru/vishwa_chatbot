from rest_framework import serializers
from .models import onwardchallan
from .models import transportdetails
from .models import vehicaldetails
from .models import outwardchallan, OnwardChallanItem
from Store.serializers import GrnGenralDetailSerializer
from Store.models import GrnGenralDetail
from .models import *
# from .views import generate_unique_challan_number

class OnwardChallanSerializer(serializers.ModelSerializer):
    class Meta:
        model = onwardchallan
        fields = '__all__'
class transportdetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model=transportdetails
        fields='__all__'
class vehicaldetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model=vehicaldetails
        fields='__all__'
class outwardchallanSerializer(serializers.ModelSerializer):
    class Meta:
        model=outwardchallan
        fields='__all__'




from .models import Store
class OnwardChallanItemSerializer(serializers.ModelSerializer):
    grndetails=GrnGenralDetailSerializer(source='items',many=True, read_only=True)
    
    class Meta:
        model  = OnwardChallanItem
        fields = [
            "item_code", "type", "description",
            "store", "suppRefNo", "qtyNo", "qtyKg",
            "process", "pkg", "wRate", "wValue",
            "grndetails","stock",
        ]
        extra_kwargs = {
            "stock": {"write_only": True}  
        }
    

    def validate(self, attrs):
        qtyNo = attrs.get("qtyNo")
        stock = attrs.get("stock")

        try:
            qtyNo = float(qtyNo)
            stock = float(stock)
        except (TypeError, ValueError):
            raise serializers.ValidationError({"stock": "Invalid stock or quantity value."})

        if qtyNo > stock:
            raise serializers.ValidationError({
                "qtyNo": f"Quantity ({qtyNo}) cannot be greater than available stock ({stock})."
            })

        return attrs

    def create(self, validated_data):
        validated_data.pop("stock", None)  
        return OnwardChallanItem.objects.create(**validated_data)

from Store.models import NewGrnList
from decimal import Decimal
class OnwardChallanSerializer(serializers.ModelSerializer):
    items = OnwardChallanItemSerializer(many=True)

    class Meta:
        model  = onwardchallan
        fields = [
            "challan_no", "challan_date", "challan_time",
            "DC_no", "Transport_name", "vehical_no",
            "Estimated_value", "DC_date", "EWay_bill_no",
            "eway_bill_date", "rev_charges", "rec_ch_amt",
            "Eway_bill_Qty", "remarks", "plant", "series",
            "vender", "items","id"
        ]

    
    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        challan = onwardchallan.objects.create(**validated_data)

        for item_data in items_data:
            item = OnwardChallanItem.objects.create(challan=challan, **item_data)
            # Subtract qtyNo from GRNQty
            store = item.store.strip()
            qty = Decimal(item.qtyNo or 0)
            grn_items = NewGrnList.objects.filter(HeatNo__iexact=store)
            for grn_item in grn_items:
                current_qty = Decimal(grn_item.GrnQty or 0)
                grn_item.GrnQty = max(current_qty - qty, Decimal(0))
                grn_item.save(update_fields=["GrnQty"])
                print(f"✅ GRN {grn_item.id} updated: {current_qty} → {grn_item.GrnQty}")

        return challan

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", [])
        # Update challan fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Delete old items and restore their qty to GRN
        old_items = OnwardChallanItem.objects.filter(challan=instance)
        for old_item in old_items:
            store = old_item.store.strip()
            qty = Decimal(old_item.qtyNo or 0)
            grn_items = NewGrnList.objects.filter(HeatNo__iexact=store)
            for grn_item in grn_items:
                grn_item.GrnQty = Decimal(grn_item.GrnQty or 0) + qty
                grn_item.save(update_fields=["GrnQty"])
                print(f"♻️ GRN {grn_item.id} restored: +{qty}")
        old_items.delete()

        # Create new items and subtract qtyNo
        for item_data in items_data:
            item = OnwardChallanItem.objects.create(challan=instance, **item_data)
            store = item.store.strip()
            qty = Decimal(item.qtyNo or 0)
            grn_items = NewGrnList.objects.filter(HeatNo__iexact=store)
            for grn_item in grn_items:
                current_qty = Decimal(grn_item.GrnQty or 0)
                grn_item.GrnQty = max(current_qty - qty, Decimal(0))
                grn_item.save(update_fields=["GrnQty"])
                print(f"✅ GRN {grn_item.id} updated: {current_qty} → {grn_item.GrnQty}")

        return instance


    # def create(self, validated_data):
    #     items_data = validated_data.pop("items", [])
    #     challan = onwardchallan.objects.create(**validated_data)
    #     OnwardChallanItem.objects.bulk_create([
    #         OnwardChallanItem(challan=challan, **item) for item in items_data
    #     ])
    #     return challan





from rest_framework import serializers
from .models import Invoice, InvoiceItemdetails,GstdetailsInvoice

class GstdetailsInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GstdetailsInvoice
        fields = '__all__'
        extra_kwargs = {
            'invoice': {'required': False}
        }


class InvoiceItemdetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItemdetails
        fields = '__all__'
        extra_kwargs = {
            'invoice': {'required': False}  
        }


# class InvoiceSerializer(serializers.ModelSerializer):
#     items = InvoiceItemdetailsSerializer(many=True, required=False)  
#     GSTdetails = GstdetailsInvoiceSerializer(many=True, required=False)


#     class Meta:
#         model = Invoice
#         fields = '__all__'

#     def create(self, validated_data):
#         items_data = validated_data.pop('items', [])
#         gst_data = validated_data.pop('GSTdetails', [])

#         # Create main Invoice
#         invoice = Invoice.objects.create(**validated_data)

#         # Create related items
#         for item in items_data:
#             InvoiceItemdetails.objects.create(invoice=invoice, **item)

#         for gst in gst_data:
#             GstdetailsInvoice.objects.create(invoice=invoice, **gst)

#         return invoice


from django.db import transaction
from rest_framework import serializers

from All_Masters.models import BOMItem, ItemTable
from Production.models import ProductionEntry
from .models import Invoice, InvoiceItemdetails

# # deduct the prod_qty-inv_qty
# class InvoiceSerializer(serializers.ModelSerializer):

#     items = InvoiceItemdetailsSerializer(many=True)
#     GSTdetails = GstdetailsInvoiceSerializer(many=True)

#     class Meta:
#         model = Invoice
#         fields = "__all__"

#     @transaction.atomic
#     def create(self, validated_data):

#         # -----------------------------------------
#         # Get nested invoice data
#         # -----------------------------------------
#         items_data = validated_data.pop("items", [])
#         gst_data = validated_data.pop("GSTdetails", [])

#         # -----------------------------------------
#         # Create Invoice
#         # -----------------------------------------
#         invoice = Invoice.objects.create(**validated_data)

#         # -----------------------------------------
#         # Create Invoice Items
#         # -----------------------------------------
#         for item_data in items_data:

#             invoice_item = InvoiceItemdetails.objects.create(
#                 invoice=invoice,
#                 **item_data
#             )

#             # -----------------------------------------
#             # Deduct invoice qty from production
#             # -----------------------------------------
#             self.deduct_invoice_qty_from_production(
#                 invoice_item
#             )

#         # -----------------------------------------
#         # Create GST details
#         # -----------------------------------------
#         for gst_item in gst_data:
#             GstdetailsInvoice.objects.create(
#                 invoice=invoice,
#                 **gst_item
#             )

#         return invoice

#     def deduct_invoice_qty_from_production(self, invoice_item):

#         part_code = invoice_item.part_code
#         invoice_qty = float(invoice_item.inv_qty or 0)

#         if not part_code:
#             return

#         if invoice_qty <= 0:
#             return

#         # -----------------------------------------
#         # 1. Find Item
#         # -----------------------------------------
#         item = ItemTable.objects.filter(
#             Part_Code__iexact=part_code
#         ).first()

#         if not item:
#             raise serializers.ValidationError(
#                 f"Item not found for part code: {part_code}"
#             )

#         # -----------------------------------------
#         # 2. Find last BOM operation
#         # -----------------------------------------
#         bom_items = BOMItem.objects.filter(
#             item=item
#         )

#         last_op_no = -1
#         last_bom = None

#         for bom in bom_items:

#             if not bom.OPNo:
#                 continue

#             try:
#                 op_no = int(bom.OPNo.strip())
#             except (ValueError, TypeError):
#                 continue

#             if op_no > last_op_no:
#                 last_op_no = op_no
#                 last_bom = bom

#         if not last_bom:
#             raise serializers.ValidationError(
#                 f"No valid BOM operation found for {part_code}"
#             )

#         # -----------------------------------------
#         # 3. Find LAST OP production entries
#         # -----------------------------------------
#         production_entries = ProductionEntry.objects.filter(
#             item__icontains=part_code,
#             operation__startswith=last_bom.OPNo
#         ).order_by("id")

#         if not production_entries.exists():
#             raise serializers.ValidationError(
#                 f"No production found for last operation "
#                 f"{last_bom.OPNo} - {part_code}"
#             )

#         # -----------------------------------------
#         # 4. Deduct invoice quantity
#         # -----------------------------------------
#         remaining_to_deduct = invoice_qty

#         for production in production_entries:

#             current_qty = float(
#                 production.prod_qty or 0
#             )

#             if current_qty <= 0:
#                 continue

#             if remaining_to_deduct <= 0:
#                 break

#             # Quantity available in this production row
#             deduct_qty = min(
#                 current_qty,
#                 remaining_to_deduct
#             )

#             # -----------------------------------------
#             # UPDATE DATABASE
#             # -----------------------------------------
#             production.prod_qty = current_qty - deduct_qty
#             production.save(
#                 update_fields=["prod_qty"]
#             )

#             remaining_to_deduct -= deduct_qty

#         # -----------------------------------------
#         # 5. Not enough production quantity
#         # -----------------------------------------
#         if remaining_to_deduct > 0:

#             raise serializers.ValidationError(
#                 f"Insufficient production quantity for "
#                 f"{part_code}. Required: {invoice_qty}, "
#                 f"Available: {invoice_qty - remaining_to_deduct}"
#             )

#     @transaction.atomic
#     def update(self, instance, validated_data):

#         # -----------------------------------------
#         # Get nested data
#         # -----------------------------------------
#         items_data = validated_data.pop("items", None)
#         gst_data = validated_data.pop("GSTdetails", None)

#         # -----------------------------------------
#         # Update Invoice fields
#         # -----------------------------------------
#         for attr, value in validated_data.items():
#             setattr(instance, attr, value)

#         instance.save()

#         # -----------------------------------------
#         # Update Invoice Items
#         # -----------------------------------------
#         if items_data is not None:

#             for item_data in items_data:

#                 item_id = item_data.get("id")

#                 if item_id:

#                     try:
#                         invoice_item = InvoiceItemdetails.objects.get(
#                             id=item_id,
#                             invoice=instance
#                         )

#                     except InvoiceItemdetails.DoesNotExist:
#                         raise serializers.ValidationError(
#                             f"Invoice item {item_id} not found."
#                         )

#                     # Old quantity
#                     old_inv_qty = float(
#                         invoice_item.inv_qty or 0
#                     )

#                     # New quantity
#                     new_inv_qty = float(
#                         item_data.get(
#                             "inv_qty",
#                             old_inv_qty
#                         ) or 0
#                     )

#                     # Difference
#                     qty_difference = (
#                         new_inv_qty - old_inv_qty
#                     )

#                     # -----------------------------------------
#                     # Quantity increased
#                     # Example: 10 -> 15
#                     # Deduct only 5
#                     # -----------------------------------------
#                     if qty_difference > 0:

#                         self.deduct_qty_from_production(
#                             part_code=invoice_item.part_code,
#                             quantity=qty_difference
#                         )

#                     # -----------------------------------------
#                     # Quantity decreased
#                     # Example: 15 -> 10
#                     # Restore 5
#                     # -----------------------------------------
#                     elif qty_difference < 0:

#                         self.restore_qty_to_production(
#                             part_code=invoice_item.part_code,
#                             quantity=abs(qty_difference)
#                         )

#                     # -----------------------------------------
#                     # Update item fields
#                     # -----------------------------------------
#                     for attr, value in item_data.items():

#                         if attr != "id":
#                             setattr(
#                                 invoice_item,
#                                 attr,
#                                 value
#                             )

#                     invoice_item.save()

#                 else:

#                     # -----------------------------------------
#                     # New Invoice Item
#                     # -----------------------------------------
#                     invoice_item = InvoiceItemdetails.objects.create(
#                         invoice=instance,
#                         **item_data
#                     )

#                     self.deduct_qty_from_production(
#                         part_code=invoice_item.part_code,
#                         quantity=float(
#                             invoice_item.inv_qty or 0
#                         )
#                     )

#         # -----------------------------------------
#         # Update GST Details
#         # -----------------------------------------
#         if gst_data is not None:

#             for gst_item_data in gst_data:

#                 gst_id = gst_item_data.get("id")

#                 if gst_id:

#                     try:
#                         gst_item = GstdetailsInvoice.objects.get(
#                             id=gst_id,
#                             invoice=instance
#                         )

#                     except GstdetailsInvoice.DoesNotExist:
#                         raise serializers.ValidationError(
#                             f"GST detail {gst_id} not found."
#                         )

#                     for attr, value in gst_item_data.items():

#                         if attr != "id":
#                             setattr(
#                                 gst_item,
#                                 attr,
#                                 value
#                             )

#                     gst_item.save()

#                 else:

#                     GstdetailsInvoice.objects.create(
#                         invoice=instance,
#                         **gst_item_data
#                     )

#         return instance

# =====================================================================
# serializers.py — InvoiceSerializer (create logic unchanged, update fixed)
# =====================================================================

class InvoiceSerializer(serializers.ModelSerializer):

    items = InvoiceItemdetailsSerializer(many=True)
    GSTdetails = GstdetailsInvoiceSerializer(many=True)

    class Meta:
        model = Invoice
        fields = "__all__"

    @transaction.atomic
    def create(self, validated_data):

        items_data = validated_data.pop("items", [])
        gst_data = validated_data.pop("GSTdetails", [])

        invoice = Invoice.objects.create(**validated_data)

        for item_data in items_data:

            # Drop keys that would collide with the explicit kwarg below
            # (nested serializers with fields="__all__" often include the
            # parent FK, and stray "id" values shouldn't be forced on create)
            item_data = {
                k: v for k, v in item_data.items()
                if k not in ("invoice", "id")
            }

            invoice_item = InvoiceItemdetails.objects.create(
                invoice=invoice,
                **item_data
            )

            self.deduct_invoice_qty_from_production(invoice_item)

        for gst_item in gst_data:

            gst_item = {
                k: v for k, v in gst_item.items()
                if k not in ("invoice", "id")
            }

            GstdetailsInvoice.objects.create(
                invoice=invoice,
                **gst_item
            )

        return invoice

    # -----------------------------------------------------------------
    # Shared helper: find the last BOM operation for an item
    # -----------------------------------------------------------------
    def _get_last_bom(self, item, part_code):

        bom_items = BOMItem.objects.filter(item=item)

        last_op_no = -1
        last_bom = None

        for bom in bom_items:

            if not bom.OPNo:
                continue

            try:
                op_no = int(bom.OPNo.strip())
            except (ValueError, TypeError):
                continue

            if op_no > last_op_no:
                last_op_no = op_no
                last_bom = bom

        if not last_bom:
            raise serializers.ValidationError(
                f"No valid BOM operation found for {part_code}"
            )

        return last_bom

    # -----------------------------------------------------------------
    # Entry point used during CREATE (keeps old signature/behaviour)
    # -----------------------------------------------------------------
    def deduct_invoice_qty_from_production(self, invoice_item):

        part_code = invoice_item.part_code
        invoice_qty = float(invoice_item.inv_qty or 0)

        if not part_code or invoice_qty <= 0:
            return

        self.deduct_qty_from_production(part_code, invoice_qty)

    # -----------------------------------------------------------------
    # Reusable deduction logic — used by CREATE and UPDATE
    # -----------------------------------------------------------------
    def deduct_qty_from_production(self, part_code, quantity):

        quantity = float(quantity or 0)

        if not part_code or quantity <= 0:
            return

        item = ItemTable.objects.filter(
            Part_Code__iexact=part_code
        ).first()

        if not item:
            raise serializers.ValidationError(
                f"Item not found for part code: {part_code}"
            )

        last_bom = self._get_last_bom(item, part_code)

        production_entries = ProductionEntry.objects.filter(
            item__icontains=part_code,
            operation__startswith=last_bom.OPNo
        ).order_by("id")

        if not production_entries.exists():
            raise serializers.ValidationError(
                f"No production found for last operation "
                f"{last_bom.OPNo} - {part_code}"
            )

        remaining_to_deduct = quantity

        for production in production_entries:

            current_qty = float(production.prod_qty or 0)

            if current_qty <= 0:
                continue

            if remaining_to_deduct <= 0:
                break

            deduct_qty = min(current_qty, remaining_to_deduct)

            production.prod_qty = current_qty - deduct_qty
            production.save(update_fields=["prod_qty"])

            remaining_to_deduct -= deduct_qty

        if remaining_to_deduct > 0:
            raise serializers.ValidationError(
                f"Insufficient production quantity for "
                f"{part_code}. Required: {quantity}, "
                f"Available: {quantity - remaining_to_deduct}"
            )

    # -----------------------------------------------------------------
    # Reverse of deduction — used when UPDATE lowers a quantity
    # NOTE: production entries don't record *which* invoice deducted
    # from them, so restoring simply credits the qty back onto the
    # first matching production row for the item's last operation.
    # Adjust this if you need per-row deduction history instead.
    # -----------------------------------------------------------------
    def restore_qty_to_production(self, part_code, quantity):

        quantity = float(quantity or 0)

        if not part_code or quantity <= 0:
            return

        item = ItemTable.objects.filter(
            Part_Code__iexact=part_code
        ).first()

        if not item:
            raise serializers.ValidationError(
                f"Item not found for part code: {part_code}"
            )

        last_bom = self._get_last_bom(item, part_code)

        production = ProductionEntry.objects.filter(
            item__icontains=part_code,
            operation__startswith=last_bom.OPNo
        ).order_by("id").first()

        if not production:
            raise serializers.ValidationError(
                f"No production found for last operation "
                f"{last_bom.OPNo} - {part_code}"
            )

        production.prod_qty = float(production.prod_qty or 0) + quantity
        production.save(update_fields=["prod_qty"])

    @transaction.atomic
    def update(self, instance, validated_data):

        items_data = validated_data.pop("items", None)
        gst_data = validated_data.pop("GSTdetails", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if items_data is not None:

            for item_data in items_data:

                item_id = item_data.get("id")

                if item_id:

                    try:
                        invoice_item = InvoiceItemdetails.objects.get(
                            id=item_id,
                            invoice=instance
                        )
                    except InvoiceItemdetails.DoesNotExist:
                        raise serializers.ValidationError(
                            f"Invoice item {item_id} not found."
                        )

                    old_inv_qty = float(invoice_item.inv_qty or 0)
                    new_inv_qty = float(
                        item_data.get("inv_qty", old_inv_qty) or 0
                    )
                    qty_difference = new_inv_qty - old_inv_qty

                    if qty_difference > 0:
                        self.deduct_qty_from_production(
                            part_code=invoice_item.part_code,
                            quantity=qty_difference
                        )
                    elif qty_difference < 0:
                        self.restore_qty_to_production(
                            part_code=invoice_item.part_code,
                            quantity=abs(qty_difference)
                        )

                    for attr, value in item_data.items():
                        if attr != "id":
                            setattr(invoice_item, attr, value)

                    invoice_item.save()

                else:

                    new_item_data = {
                        k: v for k, v in item_data.items()
                        if k not in ("invoice", "id")
                    }

                    invoice_item = InvoiceItemdetails.objects.create(
                        invoice=instance,
                        **new_item_data
                    )

                    self.deduct_qty_from_production(
                        part_code=invoice_item.part_code,
                        quantity=float(invoice_item.inv_qty or 0)
                    )

        if gst_data is not None:

            for gst_item_data in gst_data:

                gst_id = gst_item_data.get("id")

                if gst_id:

                    try:
                        gst_item = GstdetailsInvoice.objects.get(
                            id=gst_id,
                            invoice=instance
                        )
                    except GstdetailsInvoice.DoesNotExist:
                        raise serializers.ValidationError(
                            f"GST detail {gst_id} not found."
                        )

                    for attr, value in gst_item_data.items():
                        if attr != "id":
                            setattr(gst_item, attr, value)

                    gst_item.save()

                else:

                    new_gst_data = {
                        k: v for k, v in gst_item_data.items()
                        if k not in ("invoice", "id")
                    }

                    GstdetailsInvoice.objects.create(
                        invoice=instance,
                        **new_gst_data
                    )

        return instance


# =====================================================================
# views.py — InvoiceViewSet with update() mirroring your create() flow
# =====================================================================


class NewSalesItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewSalesItemdetails
        # fields = '__all__'
        exclude = ["newsaleoreder"]


class NewSalesOrderSerializer(serializers.ModelSerializer):
    item = NewSalesItemSerializer(many=True,required=False)

    class Meta:
        model = NewSalesOrder
        fields = '__all__'

    def create(self, validated_data):
        items_data = validated_data.pop('item', [])
        order = NewSalesOrder.objects.create(**validated_data)

        for item in items_data:
            NewSalesItemdetails.objects.create(
                newsaleoreder=order,
                **item
            )
        return order
    def update(self, instance, validated_data):

        # Get nested items
        items_data = validated_data.pop('item', None)

        # -----------------------------------
        # Update Sales Order fields
        # -----------------------------------

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        # -----------------------------------
        # Update Items
        # -----------------------------------

        if items_data is not None:

            existing_item_ids = []

            for item_data in items_data:

                item_id = item_data.pop('id', None)

                # Existing item → UPDATE
                if item_id:

                    item = NewSalesItemdetails.objects.filter(
                        id=item_id,
                        newsaleoreder=instance
                    ).first()

                    if item:

                        for attr, value in item_data.items():
                            setattr(item, attr, value)

                        item.save()

                        existing_item_ids.append(item.id)

                # No ID → CREATE new item
                else:

                    new_item = NewSalesItemdetails.objects.create(
                        newsaleoreder=instance,
                        **item_data
                    )

                    existing_item_ids.append(new_item.id)

            # -----------------------------------
            # Delete removed items
            # -----------------------------------

            NewSalesItemdetails.objects.filter(
                newsaleoreder=instance
            ).exclude(
                id__in=existing_item_ids
            ).delete()

        return instance

from All_Masters.models import Item       
class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = "__all__"





# class DebitNoteItemSerializer(serializers.ModelSerializer):
#     debit_note = serializers.PrimaryKeyRelatedField(read_only=True)

#     class Meta:
#         model = DebitNoteIteam
#         fields = "__all__"

# class DebitNoteSerializer(serializers.ModelSerializer):
#     items = DebitNoteItemSerializer(many=True)

#     class Meta:
#         model = DebitNote
#         fields = "__all__"

#     def create(self, validated_data):
#         items_data = validated_data.pop("items")
#         debit_note = DebitNote.objects.create(**validated_data)

#         for item in items_data:
#             DebitNoteIteam.objects.create(
#                 debit_note=debit_note,
#                 **item
#             )

#         return debit_note
    
    



from decimal import Decimal
from django.db import transaction
from rest_framework import serializers
class DebitNoteItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = DebitNoteIteam
        fields = "__all__"
        read_only_fields = ("debit_note",)

    def _to_decimal(self, val):
        try:
            return Decimal(str(val).strip())
        except:
            return Decimal("0")

    def _fifo_subtract_from_grn(self, item):
        qty_to_deduct = self._to_decimal(item.quantity)
        if qty_to_deduct <= 0:
            return

        grn_qs = (
            NewGrnList.objects
            .select_for_update()
            .filter(ItemNoCode__iexact=item.item_code.strip(), GrnQty__gt=0)
            .order_by("id")   #  FIFO
        )

        for grn in grn_qs:
            if qty_to_deduct <= 0:
                break

            available = Decimal(grn.GrnQty or 0)

            if available >= qty_to_deduct:
                # enough stock in this GRN
                grn.GrnQty = available - qty_to_deduct
                qty_to_deduct = Decimal("0")
            else:
                # consume full GRN
                grn.GrnQty = Decimal("0")
                qty_to_deduct -= available

            grn.save(update_fields=["GrnQty"])

        # optional safety check
        if qty_to_deduct > 0:
            raise serializers.ValidationError(
                f"Insufficient GRN stock for item {item.item_code}"
            )

    def create(self, validated_data):
        with transaction.atomic():
            item = DebitNoteIteam.objects.create(**validated_data)

            #  FIFO deduction happens here
            self._fifo_subtract_from_grn(item)

            return item
       
class DebitNoteSerializer(serializers.ModelSerializer):
    items = DebitNoteItemSerializer(many=True)

    class Meta:
        model = DebitNote
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items")

        with transaction.atomic():
            debit_note = DebitNote.objects.create(**validated_data)

            for item_data in items_data:
                serializer = DebitNoteItemSerializer(data=item_data)
                serializer.is_valid(raise_exception=True)
                serializer.save(debit_note=debit_note)

        return debit_note


class NewgstsalesItemDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewgstsalesItemDetails
        fields = "__all__"
        read_only_fields = ("id", "new_gst_sales")

class NewgstsalesreturnSerializer(serializers.ModelSerializer):
    items = NewgstsalesItemDetailsSerializer(many=True)

    class Meta:
        model = Newgstsalesreturn
        fields = "__all__"
        

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        sales_return = Newgstsalesreturn.objects.create(**validated_data)

        for item in items_data:
            NewgstsalesItemDetails.objects.create(
                new_gst_sales=sales_return,
                **item
            )
        return sales_return

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", [])

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        instance.items.all().delete()

        for item in items_data:
            NewgstsalesItemDetails.objects.create(
                new_gst_sales=instance,
                **item
            )
        return instance
    


class SalesRateDiffItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesRateDiffItem
        exclude = ['salesdiff']


class SalesRateDiffSerializer(serializers.ModelSerializer):
    item = SalesRateDiffItemSerializer(many=True)

    class Meta:
        model = SalesRateDiff
        fields = '__all__'

    def create(self, validated_data):
        items_data = validated_data.pop('item')
        sales_diff = SalesRateDiff.objects.create(**validated_data)

        for item_data in items_data:
            SalesRateDiffItem.objects.create(salesdiff=sales_diff, **item_data)

        return sales_diff

    def update(self, instance, validated_data):
        items_data = validated_data.pop('item', None)

        # Update parent fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update items (simple approach: delete & recreate)
        if items_data is not None:
            instance.item.all().delete()

            for item_data in items_data:
                SalesRateDiffItem.objects.create(salesdiff=instance, **item_data)

        return instance
    




class GSTJobworkItemDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTJobworkItemDetails
        fields = "__all__"
        read_only_fields = ["invoice"]


class GSTJobworkGSTDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTJobworkGSTDetails
        fields = "__all__"
        read_only_fields = ["invoice"]


class GSTJobworkInvoiceSerializer(serializers.ModelSerializer):
    items = GSTJobworkItemDetailsSerializer(many=True)
    gst_details = GSTJobworkGSTDetailsSerializer()

    class Meta:
        model = GSTJobworkInvoice
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        gst_data = validated_data.pop("gst_details", None)

        # Create invoice
        invoice = GSTJobworkInvoice.objects.create(**validated_data)

        # Create multiple items
        for item in items_data:
            GSTJobworkItemDetails.objects.create(
                invoice=invoice,
                **item
            )

        # Create gst details
        if gst_data:
            GSTJobworkGSTDetails.objects.create(
                invoice=invoice,
                **gst_data
            )

        return invoice

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", [])
        gst_data = validated_data.pop("gst_details", None)

        # Update invoice fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update items (old delete → recreate)
        if items_data:
            instance.items.all().delete()

            for item in items_data:
                GSTJobworkItemDetails.objects.create(
                    invoice=instance,
                    **item
                )

        # Update gst details
        if gst_data:
            GSTJobworkGSTDetails.objects.update_or_create(
                invoice=instance,
                defaults=gst_data
            )

        return instance
    



class GSTJobworkRatediffItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSTJobworkRatediffItem
        fields = "__all__"
        read_only_fields = ["rate_diff"]


class GSTJobworkRateDiffSerializer(serializers.ModelSerializer):
    items = GSTJobworkRatediffItemSerializer(many=True)

    class Meta:
        model = GSTJobworkRateDiff
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])

        # create parent record
        rate_diff = GSTJobworkRateDiff.objects.create(**validated_data)

        # create child items
        for item in items_data:
            GSTJobworkRatediffItem.objects.create(
                rate_diff=rate_diff,
                **item
            )

        return rate_diff

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", [])

        # update parent fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # update child items
        if items_data:
            instance.items.all().delete()

            for item in items_data:
                GSTJobworkRatediffItem.objects.create(
                    rate_diff=instance,
                    **item
                )

        return instance
    


class CustomerPoAmendmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerPoAmendment
        fields = "__all__"

        

class CreadiNoteItemDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreadiNoteItemDetails
        fields = '__all__'
        read_only_fields = ("credit_note",)


class CreditNoteSerializer(serializers.ModelSerializer):
    items = CreadiNoteItemDetailsSerializer(many=True)

    class Meta:
        model = CreditNote
        fields = '__all__'

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])

        credit_note = CreditNote.objects.create(**validated_data)

        for item in items_data:
            CreadiNoteItemDetails.objects.create(
                credit_note=credit_note,
                **item
            )

        return credit_note

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        # Delete old items
        instance.items.all().delete()

        # Create new items
        for item in items_data:
            CreadiNoteItemDetails.objects.create(
                credit_note=instance,
                **item
            )

        return instance
    


class NewProfomaInvoiceItemDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewProfomaInvoiceItemDetails
        fields = '__all__'
        read_only_fields = ("profoma_invice",)


class NewProfomaInvoiceSerializer(serializers.ModelSerializer):
    items = NewProfomaInvoiceItemDetailsSerializer(many=True)

    class Meta:
        model = NewProfomaInvoice
        fields = '__all__'

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])

        invoice = NewProfomaInvoice.objects.create(**validated_data)

        for item in items_data:
            NewProfomaInvoiceItemDetails.objects.create(
                profoma_invice=invoice,
                **item
            )

        return invoice

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        # Delete old items
        instance.items.all().delete()

        # Create new items
        for item in items_data:
            NewProfomaInvoiceItemDetails.objects.create(
                profoma_invice=instance,
                **item
            )

        return instance