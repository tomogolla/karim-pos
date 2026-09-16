from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pos.models import Product, Staff

class Command(BaseCommand):
    help = 'Add sample staff and products, without fabricated orders or payments.'
    def handle(self, *args, **options):
        if not settings.DEMO_MODE: raise CommandError('Enable DEMO_MODE to load demo data.')
        for name, role in [('Jemimah Syokau','Stylist'),('Kelvin Ndegwa','Stylist'),('Joyce Mwangi','Stylist'),('Valentina Achieng','Stylist'),('Paige Wambui','Nail technician'),('Ian Kimani','Nail technician')]:
            Staff.objects.get_or_create(name=name, defaults={'role':role})
        items = [('Classic Haircut & Blowdry','Hair','service','1500.00'),('Gel Nails & Manicure','Nails','service','2000.00'),('Spa Facial Refresh','Spa & Body','service','3500.00'),('Pedicure Deluxe','Nails','service','2500.00'),('Hair Styling · Express','Hair','service','1000.00'),('Deep Cleanse Facial','Spa & Body','service','4000.00'),('Hydrating Argan Serum','Retail','retail','1800.00'),('Nourishing Hair Mask','Retail','retail','2200.00')]
        for i,(title,category,kind,price) in enumerate(items,1):
            Product.objects.get_or_create(variant_id=f'demo-{i}', defaults={'title':title,'category':category,'kind':kind,'price':price,'sku':f'KS-{i:03}','demo':True,'tracked':kind=='retail','inventory':20 if kind=='retail' else None})
        self.stdout.write(self.style.SUCCESS('Demo catalog and staff are ready. Open the app and choose Explore the local demo.'))
