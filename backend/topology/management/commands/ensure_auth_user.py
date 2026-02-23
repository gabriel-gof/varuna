import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from topology.models import UserProfile


def _is_true(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


class Command(BaseCommand):
    help = 'Create or update an authentication user for API login.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Username for login user.')
        parser.add_argument('--password', type=str, help='Plain password (use env vars in production).')
        parser.add_argument('--email', type=str, default=None, help='User email (optional).')
        parser.add_argument(
            '--role',
            type=str,
            choices=[choice[0] for choice in UserProfile.ROLE_CHOICES],
            help='User role in topology.UserProfile.',
        )
        parser.add_argument(
            '--superuser',
            action='store_true',
            help='Promote user to Django superuser/staff.',
        )
        parser.add_argument(
            '--force-password',
            action='store_true',
            help='Update password when user already exists.',
        )

    def handle(self, *args, **options):
        username = (options.get('username') or os.getenv('VARUNA_AUTH_USERNAME', '')).strip()
        password = options.get('password')
        if password is None:
            password = os.getenv('VARUNA_AUTH_PASSWORD')
        email = options.get('email')
        if email is None:
            email = os.getenv('VARUNA_AUTH_EMAIL', '')
        email = (email or '').strip()

        role = options.get('role') or os.getenv('VARUNA_AUTH_ROLE', UserProfile.ROLE_ADMIN)
        role = (role or UserProfile.ROLE_ADMIN).strip()
        valid_roles = {choice[0] for choice in UserProfile.ROLE_CHOICES}
        if role not in valid_roles:
            raise CommandError(f'Invalid role "{role}". Expected one of: {", ".join(sorted(valid_roles))}.')

        make_superuser = bool(options.get('superuser')) or _is_true(os.getenv('VARUNA_AUTH_SUPERUSER'))
        force_password = bool(options.get('force_password')) or _is_true(os.getenv('VARUNA_AUTH_FORCE_PASSWORD'))

        if not username:
            raise CommandError('Username is required (--username or VARUNA_AUTH_USERNAME).')

        user = User.objects.filter(username=username).first()

        if user is None:
            if not password:
                raise CommandError(
                    'Password is required when creating a new user '
                    '(--password or VARUNA_AUTH_PASSWORD).'
                )
            user = User.objects.create_user(username=username, email=email or '', password=password)
            created = True
            self.stdout.write(self.style.SUCCESS(f'Created auth user "{username}".'))
        else:
            created = False
            self.stdout.write(f'Auth user "{username}" already exists.')

            fields_to_update = []
            if email and user.email != email:
                user.email = email
                fields_to_update.append('email')

            if password and (force_password or not user.has_usable_password()):
                user.set_password(password)
                fields_to_update.append('password')
            elif not user.has_usable_password() and not password:
                raise CommandError(
                    'Existing user has no usable password. Provide --password or VARUNA_AUTH_PASSWORD.'
                )

            if fields_to_update:
                user.save(update_fields=fields_to_update)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Updated auth user "{username}" fields: {", ".join(fields_to_update)}.'
                    )
                )

        if make_superuser:
            elevate_fields = []
            if not user.is_staff:
                user.is_staff = True
                elevate_fields.append('is_staff')
            if not user.is_superuser:
                user.is_superuser = True
                elevate_fields.append('is_superuser')
            if not user.is_active:
                user.is_active = True
                elevate_fields.append('is_active')
            if elevate_fields:
                user.save(update_fields=elevate_fields)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Promoted "{username}" with Django permissions: {", ".join(elevate_fields)}.'
                    )
                )

        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'role': role},
        )
        if profile_created:
            self.stdout.write(self.style.SUCCESS(f'Created UserProfile with role "{role}".'))
        elif profile.role != role:
            profile.role = role
            profile.save(update_fields=['role'])
            self.stdout.write(self.style.SUCCESS(f'Updated UserProfile role to "{role}".'))

        action = 'created' if created else 'verified'
        self.stdout.write(self.style.SUCCESS(f'Authentication user "{username}" {action} successfully.'))
