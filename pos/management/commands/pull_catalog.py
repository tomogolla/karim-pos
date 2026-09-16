from django.core.management.base import BaseCommand, CommandError
from pos.shopify import pull_catalog
class Command(BaseCommand):
    help = 'Import all Shopify product variants into the local catalog cache.'
    def handle(self,*args,**options):
        try: self.stdout.write(f'{pull_catalog()} variants imported.')
        except Exception as exc: raise CommandError(str(exc)) from exc
