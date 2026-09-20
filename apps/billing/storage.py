from django.core.files.storage import storages
from django.core.files.storage import FileSystemStorage
from django.core.exceptions import ValidationError


def private_invoice_storage():
    return storages["private"]


class PrivateFileSystemStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError("Invoices are available only through authenticated downloads.")


def validate_invoice_pdf(value):
    if value.size > 10 * 1024 * 1024:
        raise ValidationError("Invoices must be smaller than 10 MB.")
    position = value.tell()
    try:
        value.seek(0)
        if not value.read(5).startswith(b"%PDF-"):
            raise ValidationError("Upload a PDF document.")
    finally:
        value.seek(position)
