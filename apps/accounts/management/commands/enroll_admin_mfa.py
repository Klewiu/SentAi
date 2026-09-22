import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Interactively enroll an administrator's authenticator on the trusted server console."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, username, **options):
        user = get_user_model().objects.filter(username=username, is_active=True, is_staff=True).first()
        if not user or not user.check_password(getpass.getpass("Administrator password: ")):
            raise CommandError("Invalid administrator credentials.")
        if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
            raise CommandError("An authenticator is already enrolled. Use the verified admin to manage devices.")
        device = TOTPDevice(user=user, name="Authenticator", confirmed=False)
        self.stdout.write("Add this authenticator URI to your password manager/authenticator privately:")
        self.stdout.write(device.config_url)
        device.save()
        if not device.verify_token(getpass.getpass("Authenticator code: ")):
            device.delete()
            raise CommandError("Code did not verify. Nothing enrolled.")
        device.confirmed = True
        device.save()
        self.stdout.write("Authenticator confirmed. Sign in through the two-factor login page.")
