from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from home.models import ShopkeeperProfile, Shop, Product, Order

class Command(BaseCommand):
    help = 'Seeds or resets development demo data for Zero-Click Store Operator hackathon demo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Reset inventory stock levels and clear previous demo orders.',
        )

    def handle(self, *args, **options):
        reset_mode = options.get('reset', False)
        self.stdout.write(f"Seeding demo data (Reset mode: {reset_mode})...")

        # 1. Demo Shopkeeper: Rahul
        u1, created1 = User.objects.get_or_create(
            username='rahul_shopkeeper',
            defaults={'email': 'rahul@store.com', 'is_staff': True}
        )
        if created1:
            u1.set_password('pass123')
            u1.save()

        prof1, _ = ShopkeeperProfile.objects.get_or_create(user=u1, defaults={'phone': '9876543210'})
        shop1, _ = Shop.objects.get_or_create(
            shopkeeper=prof1,
            name='Rahul General Store',
            defaults={'address': 'Shop #12, Main Market, Sector 4', 'phone': '9876543210', 'is_active': True}
        )

        p1_list = [
            {'name': 'Aashirvaad Atta', 'price': 120.00, 'unit': 'kg', 'stock': 10, 'is_active': True},
            {'name': 'Amul Milk', 'price': 65.00, 'unit': 'litre', 'stock': 5, 'is_active': True},
            {'name': 'Parle-G Biscuits', 'price': 20.00, 'unit': 'packet', 'stock': 20, 'is_active': True},
            {'name': 'Fortune Oil', 'price': 150.00, 'unit': 'litre', 'stock': 5, 'is_active': True},
            {'name': 'Tata Salt', 'price': 25.00, 'unit': 'packet', 'stock': 15, 'is_active': True},
        ]

        if reset_mode:
            Order.objects.filter(shop=shop1).delete()

        for item in p1_list:
            p, created = Product.objects.get_or_create(shop=shop1, name=item['name'], defaults=item)
            if not created and reset_mode:
                p.price = item['price']
                p.stock = item['stock']
                p.unit = item['unit']
                p.is_active = item['is_active']
                p.save()

        # 2. Demo Shopkeeper 2: Sharma
        u2, created2 = User.objects.get_or_create(
            username='sharma_shopkeeper',
            defaults={'email': 'sharma@kirana.com', 'is_staff': True}
        )
        if created2:
            u2.set_password('pass123')
            u2.save()

        prof2, _ = ShopkeeperProfile.objects.get_or_create(user=u2, defaults={'phone': '9123456789'})
        shop2, _ = Shop.objects.get_or_create(
            shopkeeper=prof2,
            name='Sharma Kirana Store',
            defaults={'address': 'Station Road, Market Square', 'phone': '9123456789', 'is_active': True}
        )

        p2_list = [
            {'name': 'Fortune Chakki Fresh Atta', 'price': 118.00, 'unit': 'kg', 'stock': 15, 'is_active': True},
            {'name': 'Mother Dairy Milk', 'price': 64.00, 'unit': 'litre', 'stock': 20, 'is_active': True},
        ]

        if reset_mode:
            Order.objects.filter(shop=shop2).delete()

        for item in p2_list:
            p, created = Product.objects.get_or_create(shop=shop2, name=item['name'], defaults=item)
            if not created and reset_mode:
                p.price = item['price']
                p.stock = item['stock']
                p.unit = item['unit']
                p.is_active = item['is_active']
                p.save()

        self.stdout.write(self.style.SUCCESS("Successfully seeded/reset demo stores and catalog products!"))
