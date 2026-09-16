from django.core.management.base import BaseCommand
from pos.models import SyncJob
from pos.shopify import send_job
class Command(BaseCommand):
    help = 'Send pending orders once. Schedule externally after configuring Shopify. Uncertain outcomes are never automatically retried.'
    def handle(self,*args,**options):
        for pk in SyncJob.objects.filter(state='pending').values_list('pk',flat=True):
            try: self.stdout.write(f'Job {pk}: {"synced" if send_job(pk) else "inspect status"}')
            except ValueError as exc: self.stdout.write(str(exc))
