from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from home.models import ShopkeeperProfile, Shop, Product, Customer, Order

class Command(BaseCommand):
    help = 'Seeds or resets development demo data for Zero-Click Store Operator hackathon demo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Reset inventory stock levels and clear previous demo orders for Rahul General Store.',
        )

    def handle(self, *args, **options):
        reset_mode = options.get('reset', False)

        with transaction.atomic():
            # 1. Demo Shopkeeper User
            user, u_created = User.objects.get_or_create(
                username='demo_shopkeeper',
                defaults={
                    'email': 'demo@zeroclick.local',
                    'is_staff': True
                }
            )
            user.email = 'demo@zeroclick.local'
            user.set_password('Demo@12345')
            user.save()

            # 2. Demo Shopkeeper Profile
            profile, _ = ShopkeeperProfile.objects.get_or_create(
                user=user,
                defaults={'phone': '9999999999'}
            )
            if profile.phone != '9999999999':
                profile.phone = '9999999999'
                profile.save()

            # 3. Demo Shop
            shop, _ = Shop.objects.get_or_create(
                shopkeeper=profile,
                name='Rahul General Store',
                defaults={
                    'address': 'Main Market',
                    'phone': '9999999999',
                    'is_active': True
                }
            )
            shop.address = 'Main Market'
            shop.phone = '9999999999'
            shop.is_active = True
            shop.save()

            # 4. Clear orders if reset mode
            if reset_mode:
                Order.objects.filter(shop=shop).delete()

            # 5. Demo Products
            products_data = [
                {'name': 'Aashirvaad Atta', 'price': Decimal('120.00'), 'unit': 'kg', 'stock': 10, 'is_active': True},
                {'name': 'Amul Milk', 'price': Decimal('65.00'), 'unit': 'litre', 'stock': 5, 'is_active': True},
                {'name': 'Parle-G Biscuits', 'price': Decimal('20.00'), 'unit': 'packet', 'stock': 20, 'is_active': True},
                {'name': 'Fortune Sunflower Oil', 'price': Decimal('150.00'), 'unit': 'litre', 'stock': 5, 'is_active': True},
                {'name': 'Tata Salt', 'price': Decimal('25.00'), 'unit': 'packet', 'stock': 15, 'is_active': True},
                {'name': 'Maggi 2-Minute Noodles', 'price': Decimal('15.00'), 'unit': 'packet', 'stock': 10, 'is_active': True},
                {'name': 'Coca Cola', 'price': Decimal('40.00'), 'unit': 'bottle', 'stock': 10, 'is_active': True},
                {'name': 'Surf Excel', 'price': Decimal('120.00'), 'unit': 'packet', 'stock': 5, 'is_active': True},
            ]

            processed_products = []
            for pdata in products_data:
                product, p_created = Product.objects.get_or_create(
                    shop=shop,
                    name=pdata['name'],
                    defaults=pdata
                )
                if not p_created or reset_mode:
                    product.price = pdata['price']
                    product.unit = pdata['unit']
                    product.stock = pdata['stock']
                    product.is_active = pdata['is_active']
                    product.save()

                processed_products.append(product)

            # 6. Demo Customer
            customer, _ = Customer.objects.get_or_create(
                phone='9876543210',
                defaults={
                    'name': 'Demo Customer',
                    'address': 'Main Market'
                }
            )

        # Output Summary (ASCII safe for all terminals & Windows encodings)
        self.stdout.write(self.style.SUCCESS("=" * 40))
        self.stdout.write(self.style.SUCCESS("ZERO-CLICK STORE OPERATOR"))
        self.stdout.write(self.style.SUCCESS("DEMO DATA SETUP"))
        self.stdout.write(self.style.SUCCESS("=" * 40 + "\n"))

        self.stdout.write("Database:")
        self.stdout.write("PostgreSQL\n")

        self.stdout.write("Shopkeeper:")
        self.stdout.write("demo_shopkeeper [OK]\n")

        self.stdout.write("Shop:")
        self.stdout.write("Rahul General Store [OK]\n")

        self.stdout.write("Products:")
        self.stdout.write(f"{len(processed_products)} products created/updated [OK]\n")

        self.stdout.write("Demo stock:")
        for p in processed_products:
            self.stdout.write(f" - {p.name}: {p.stock} {p.unit}")

        self.stdout.write("\n" + "=" * 40)
        self.stdout.write(self.style.SUCCESS("DEMO DATA READY"))
        self.stdout.write("=" * 40 + "\n")

        self.stdout.write("Login:")
        self.stdout.write("Username: demo_shopkeeper")
        self.stdout.write("Password: Demo@12345\n")

        self.stdout.write("Customer:")
        self.stdout.write("Use customer ordering flow\n")

        self.stdout.write("=" * 40)
