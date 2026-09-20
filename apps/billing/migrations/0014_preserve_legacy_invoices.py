from django.db import migrations


def preserve_invoices(apps, schema_editor):
    Invoice = apps.get_model("billing", "BillingInvoice")
    Payment = apps.get_model("billing", "BillingPayment")
    Profile = apps.get_model("billing", "BillingProfile")
    alias = schema_editor.connection.alias
    for payment in Payment.objects.using(alias).exclude(invoice_document="").filter(invoice_issued_at__isnull=False).iterator(chunk_size=100):
        if not Invoice.objects.using(alias).filter(payment_id=payment.pk).exists():
            Invoice.objects.using(alias).create(user_id=payment.user_id, payment_id=payment.pk, subscription_id=payment.subscription_id, invoice_number=payment.invoice_number, issued_at=payment.invoice_issued_at, document=payment.invoice_document.name, sent=payment.invoice_sent, sent_at=payment.invoice_sent_at)
    for invoice in Invoice.objects.using(alias).filter(billing_snapshot={}).iterator(chunk_size=100):
        profile = Profile.objects.using(alias).filter(user_id=invoice.user_id).first()
        if profile:
            snapshot = {field: getattr(profile, field) for field in ("company_name", "tax_id", "street", "postal_code", "city", "country", "invoice_email")}
            snapshot["snapshot_source"] = "current_profile_at_migration"
            Invoice.objects.using(alias).filter(pk=invoice.pk).update(billing_snapshot=snapshot)


class Migration(migrations.Migration):
    dependencies = [("billing", "0013_billinginvoice_billing_snapshot_and_more")]
    operations = [migrations.RunPython(preserve_invoices, migrations.RunPython.noop)]
