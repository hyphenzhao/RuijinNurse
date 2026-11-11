from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from promotions.models import QATiming


class Command(BaseCommand):
    help = ('Mark abandoned pending timings interrupted. Use --older-than-minutes 0 '
            'only while all application workers are stopped, before service startup.')

    def add_arguments(self, parser):
        parser.add_argument('--older-than-minutes', type=int, default=120)

    def handle(self, *args, **options):
        age = options['older_than_minutes']
        if age < 0:
            raise CommandError('older-than-minutes must not be negative')
        now = timezone.now()
        count = QATiming.objects.filter(
            status='pending', started_at__lte=now - timedelta(minutes=age),
        ).update(status='interrupted', finished_at=now)
        self.stdout.write(f'Recovered {count} abandoned question timings.')
