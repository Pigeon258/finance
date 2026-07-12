from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the system's only owner account"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="owner")

    def handle(self, *args, **options):
        user_model = get_user_model()
        if user_model._default_manager.exists():
            raise CommandError("An owner account already exists; no additional users are allowed.")

        username = options["username"]
        password = getpass("Password: ")
        confirmation = getpass("Password (again): ")
        if password != confirmation:
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error

        user_model._default_manager.create_superuser(username=username, password=password)
        self.stdout.write(self.style.SUCCESS("Owner account created."))
