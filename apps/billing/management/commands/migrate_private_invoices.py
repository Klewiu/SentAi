from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.files import File
from apps.billing.models import BillingInvoice, BillingPayment
from apps.billing.storage import private_invoice_storage


class Command(BaseCommand):
    help = "Copy legacy invoices to private storage, verify bytes, then remove the public copies."

    def handle(self, *args, **options):
        storage = private_invoice_storage()
        root = Path(settings.MEDIA_ROOT).resolve()
        names = set(BillingInvoice.objects.exclude(document="").values_list("document", flat=True))
        names.update(BillingPayment.objects.exclude(invoice_document="").values_list("invoice_document", flat=True))
        # Include orphaned uploads and old test fixtures: a public file is exposed
        # even when no current database record references it.
        legacy_root = (root / "invoices").resolve()
        if not legacy_root.is_relative_to(root):
            raise CommandError("The invoice directory resolves outside MEDIA_ROOT.")
        if legacy_root.exists():
            names.update(str(path.relative_to(root)).replace("\\", "/") for path in legacy_root.rglob("*") if path.is_file())
        for name in names:
            source = (root / name).resolve()
            if not source.is_relative_to(root / "invoices"):
                raise CommandError("An invoice path is outside the legacy invoice directory.")
            if not source.exists():
                if not storage.exists(name):
                    raise CommandError(f"Invoice file is missing: {name}")
                continue
            if not storage.exists(name):
                with source.open("rb") as handle:
                    saved = storage.save(name, File(handle))
                if saved != name:
                    raise CommandError("Private storage changed a legacy filename; investigate before continuing.")
            with storage.open(name, "rb") as handle:
                if handle.read() != source.read_bytes():
                    raise CommandError("Private invoice differs from the public copy; nothing removed.")
            source.unlink()
        self.stdout.write(f"Verified {len(names)} private invoice paths.")
