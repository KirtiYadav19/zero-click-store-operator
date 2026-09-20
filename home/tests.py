from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from home.models import ShopkeeperProfile, Shop, Product, Order, OrderItem
from home import services, gemini_service

class ShopkeeperAndInventoryTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='shopkeeper1', password='Password123!')
        self.profile1 = ShopkeeperProfile.objects.create(user=self.user1, phone='9876543210')
        self.shop1 = Shop.objects.create(shopkeeper=self.profile1, name='Rahul Store', address='Main Market')

        self.user2 = User.objects.create_user(username='shopkeeper2', password='Password123!')
        self.profile2 = ShopkeeperProfile.objects.create(user=self.user2, phone='9876543211')
        self.shop2 = Shop.objects.create(shopkeeper=self.profile2, name='Sharma Store', address='Subhash Nagar')

        self.product1 = Product.objects.create(
            shop=self.shop1,
            name='Aashirvaad Atta 5kg',
            price=Decimal('250.00'),
            unit='packet',
            stock=20,
            is_active=True
        )
        self.product2 = Product.objects.create(
            shop=self.shop2,
            name='Amul Milk 1L',
            price=Decimal('66.00'),
            unit='packet',
            stock=50,
            is_active=True
        )

    def test_shop_ownership_and_isolation(self):
        shop1_products = Product.objects.filter(shop=self.shop1)
        shop2_products = Product.objects.filter(shop=self.shop2)

        self.assertIn(self.product1, shop1_products)
        self.assertNotIn(self.product2, shop1_products)
        self.assertIn(self.product2, shop2_products)
        self.assertNotIn(self.product1, shop2_products)

    def test_product_price_and_stock(self):
        self.assertEqual(self.product1.price, Decimal('250.00'))
        self.assertEqual(self.product1.stock, 20)
        self.assertTrue(self.product1.is_active)


class CartAndOrderTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9988776655')
        self.shop = Shop.objects.create(shopkeeper=self.profile, name='Test Kirana', address='Center Market')
        self.p1 = Product.objects.create(shop=self.shop, name='Tata Salt 1kg', price=Decimal('28.00'), unit='packet', stock=10)
        self.p2 = Product.objects.create(shop=self.shop, name='Parle-G 80g', price=Decimal('10.00'), unit='packet', stock=30)
        self.client = Client()

    def test_cart_operations(self):
        session = self.client.session
        success1, _ = services.add_to_cart(session, self.shop.id, self.p1.id, 2)
        success2, _ = services.add_to_cart(session, self.shop.id, self.p2.id, 5)

        self.assertTrue(success1)
        self.assertTrue(success2)

        cart_data = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_data['items']), 2)
        self.assertEqual(cart_data['total'], Decimal('106.00'))

        services.remove_from_cart(session, self.shop.id, self.p1.id)
        cart_after_remove = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_after_remove['items']), 1)

        services.clear_cart(session, self.shop.id)
        cart_empty = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_empty['items']), 0)

    def test_atomic_order_creation_and_stock_deduction(self):
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.p1.id, 3)
        services.add_to_cart(session, self.shop.id, self.p2.id, 2)

        order, err = services.place_order_atomic(
            session=session,
            shop_id=self.shop.id,
            customer_name='Amit Verma',
            customer_phone='9998887776',
            customer_address='Flat 101, Test Residency'
        )

        self.assertIsNone(err)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, 'confirmed')
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.total_amount, Decimal('104.00'))

        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.stock, 7)
        self.assertEqual(self.p2.stock, 28)


class Prompt16AvailabilityVsOrderingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='demo_shopkeeper_p16', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9999999999')
        self.shop = Shop.objects.create(shopkeeper=self.profile, name='Rahul General Store')

        self.rice = Product.objects.create(
            shop=self.shop,
            name='Rice',
            price=Decimal('60.00'),
            unit='kg',
            stock=25,
            is_active=True
        )
        self.maggi = Product.objects.create(
            shop=self.shop,
            name='Maggi 2-Minute Noodles',
            price=Decimal('15.00'),
            unit='packet',
            stock=10,
            is_active=True
        )
        self.milk = Product.objects.create(
            shop=self.shop,
            name='Amul Milk',
            price=Decimal('65.00'),
            unit='litre',
            stock=15,
            is_active=True
        )
        self.client = Client()

    def test_maggi_hai_ya_nahi_does_not_order(self):
        """'Maggi hai ya nahi?' must return availability, NOT add to cart or place order."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Maggi hai ya nahi?")

        # 1. Product found and reported
        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertIn("10", res["reply"])
        self.assertIn("available", res["reply"].lower())

        # 2. Cart MUST be completely empty
        self.assertEqual(len(res["cart"]["items"]), 0)
        self.assertEqual(res["cart"]["total"], Decimal('0.00'))

        # 3. No order created in DB
        self.assertEqual(Order.objects.filter(shop=self.shop).count(), 0)

        # 4. Follow-up: User says '2 packet' -> Now adds 2 to cart!
        res_followup = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 packet")
        self.assertEqual(len(res_followup["cart"]["items"]), 1)
        self.assertEqual(res_followup["cart"]["items"][0]["quantity"], 2)
        self.assertEqual(res_followup["cart"]["total"], Decimal('30.00'))

    def test_hindi_availability_does_not_order(self):
        """'क्या मैगी है या नहीं?' checks availability without cart mutation."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "क्या मैगी है या नहीं?")
        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_cart_preservation_during_availability_check(self):
        """Asking availability of another product must preserve existing cart items."""
        session = self.client.session
        # Add 2 kg rice to cart
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 kg rice de do")
        self.assertEqual(len(services.get_cart(session, self.shop.id)["items"]), 1)

        # Now ask about Maggi availability
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Maggi hai ya nahi?")
        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        
        # Cart must still contain exactly Rice x 2 (no Maggi added)
        cart = res["cart"]
        self.assertEqual(len(cart["items"]), 1)
        self.assertEqual(cart["items"][0]["name"], "Rice")
        self.assertEqual(cart["items"][0]["quantity"], 2)

    def test_case_insensitive_rice_search(self):
        """'rice', 'RICE', 'Rice?' all find Rice."""
        res1 = services.search_product(self.shop.id, "rice")
        self.assertEqual(res1[0].name, "Rice")

        res2 = services.search_product(self.shop.id, "RICE")
        self.assertEqual(res2[0].name, "Rice")

        res3 = services.search_product(self.shop.id, "Rice?")
        self.assertEqual(res3[0].name, "Rice")

    def test_followup_set_quantity(self):
        """'add quantity 2' sets total quantity of referenced item to 2."""
        session = self.client.session
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")
        
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "add quantity 2")
        self.assertEqual(res["cart"]["items"][0]["quantity"], 2)

    def test_add_more_quantity(self):
        """'2 aur add kar do' adds 2 more to existing quantity (1 + 2 = 3)."""
        session = self.client.session
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")
        
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 aur add kar do")
        self.assertEqual(res["cart"]["items"][0]["quantity"], 3)
