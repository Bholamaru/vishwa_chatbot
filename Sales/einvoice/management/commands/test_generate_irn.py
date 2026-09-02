"""
einvoice/management/commands/test_generate_irn.py

Run with: python manage.py test_generate_irn <invoice_id>

Calls auth + generate_irn for a real Invoice from your DB against
STAGING, and prints the raw response — does NOT save anything. Use
this to capture the actual response shape before wiring up
save_irn_to_invoice().
"""

import json

from django.core.management.base import BaseCommand, CommandError

from Sales.models import Invoice  # TODO: fix import path to your actual app
from einvoice.services.generate_irn import generate_irn, EInvoiceGenerationError
from einvoice.services.auth import EInvoiceAuthError


class Command(BaseCommand):
    help = "Test the Generate IRN flow against staging without saving results."

    def add_arguments(self, parser):
        parser.add_argument("invoice_id", type=int)

    def handle(self, *args, **options):
        try:
            invoice = Invoice.objects.get(pk=options["invoice_id"])
        except Invoice.DoesNotExist:
            raise CommandError(f"No invoice with id {options['invoice_id']}")

        self.stdout.write(f"Generating IRN for invoice {invoice.invoice_no}...")

        try:
            response_data = generate_irn(invoice)
        except (EInvoiceGenerationError, EInvoiceAuthError) as exc:
            self.stderr.write(self.style.ERROR(f"Failed: {exc}"))
            if getattr(exc, "response_data", None):
                self.stdout.write(json.dumps(exc.response_data, indent=2))
            return

        self.stdout.write(self.style.SUCCESS("Success. Raw response:"))
        self.stdout.write(json.dumps(response_data, indent=2))
