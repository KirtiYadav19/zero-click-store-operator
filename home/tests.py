from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from home.models import ShopkeeperProfile, Shop, Product, Order, OrderItem
from home import services, gemini_service

class ShopkeeperAndInventoryTestCase(TestCase):
    def setUp(self):
        # Create test users and shops
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
        """Verify shop products belong exclusively to their respective shops."""
        shop1_products = Product.objects.filter(shop=self.shop1)
        shop2_products = Product.objects.filter(shop=self.shop2)

        self.assertIn(self.product1, shop1_products)
        self.assertNotIn(self.product2, shop1_products)
        self.assertIn(self.product2, shop2_products)
        self.assertNotIn(self.product1, shop2_products)

    def test_product_price_and_stock(self):
        """Test product price and stock attributes."""
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
        """Test session-based cart addition, calculation, and removal."""
        session = self.client.session
        success1, _ = services.add_to_cart(session, self.shop.id, self.p1.id, 2)
        success2, _ = services.add_to_cart(session, self.shop.id, self.p2.id, 5)

        self.assertTrue(success1)
        self.assertTrue(success2)

        cart_data = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_data['items']), 2)

        # 2 * 28 + 5 * 10 = 56 + 50 = 106.00
        self.assertEqual(cart_data['total'], Decimal('106.00'))

        services.remove_from_cart(session, self.shop.id, self.p1.id)
        cart_after_remove = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_after_remove['items']), 1)

        services.clear_cart(session, self.shop.id)
        cart_empty = services.get_cart(session, self.shop.id)
        self.assertEqual(len(cart_empty['items']), 0)

    def test_atomic_order_creation_and_stock_deduction(self):
        """Test transaction-safe order placement and inventory deduction."""
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
        self.assertEqual(order.total_amount, Decimal('104.00')) # 3*28 + 2*10 = 84 + 20 = 104

        # Refresh products from db to check stock deduction
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.stock, 7)  # 10 - 3
        self.assertEqual(self.p2.stock, 28) # 30 - 2

    def test_order_creation_insufficient_stock_fails(self):
        """Test that adding quantity exceeding stock returns failure error."""
        session = self.client.session
        success, msg = services.add_to_cart(session, self.shop.id, self.p1.id, 15) # Only 10 in stock
        self.assertFalse(success)
        self.assertIn("Only 10 items available", msg)

        # Stock should remain untouched (10)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 10)


class MultilingualProductUnderstandingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='shopkeeper_demo', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9876543210')
        self.shop = Shop.objects.create(shopkeeper=self.profile, name='Rahul General Store')

        self.maggi = Product.objects.create(
            shop=self.shop,
            name='Maggi 2-Minute Noodles',
            price=Decimal('15.00'),
            unit='packet',
            stock=8,
            is_active=True
        )
        self.atta = Product.objects.create(
            shop=self.shop,
            name='Aashirvaad Atta 5kg',
            price=Decimal('240.00'),
            unit='packet',
            stock=12,
            is_active=True
        )
        self.milk = Product.objects.create(
            shop=self.shop,
            name='Amul Milk 1L',
            price=Decimal('65.00'),
            unit='litre',
            stock=10,
            is_active=True
        )
        self.client = Client()

    def test_extract_product_name_utility(self):
        """Test product keyword extraction from full sentences."""
        extracted1 = gemini_service._extract_product_name("क्या हमारे पास मैगी है स्टॉक में?")
        self.assertIn("मैगी", extracted1)

        extracted2 = gemini_service._extract_product_name("Do you have Maggi?")
        self.assertIn("maggi", extracted2.lower())

        extracted3 = gemini_service._extract_product_name("Maggi stock mein hai?")
        self.assertIn("maggi", extracted3.lower())

    def test_hindi_stock_query(self):
        """Test Hindi full-sentence stock availability query without cart mutation."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "क्या हमारे पास मैगी है स्टॉक में?")
        
        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertIn("8", res["reply"]) # Reports exact DB stock 8
        self.assertEqual(len(res["cart"]["items"]), 0) # Cart untouched

    def test_hinglish_stock_query(self):
        """Test Hinglish stock availability query."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Maggi stock mein hai?")

        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertIn("8", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_english_stock_query(self):
        """Test English stock query."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Do you have Maggi?")

        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertIn("8", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_hindi_price_query(self):
        """Test Hindi price query without cart mutation."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "मैगी कितने की है?")

        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertIn("15", res["reply"]) # ₹15
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_add_to_cart_intent(self):
        """Test explicit add to cart intent in Hindi/Hinglish."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "एक Maggi दे दो")

        self.assertIn("Added 1 × Maggi 2-Minute Noodles", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 1)
        self.assertEqual(res["cart"]["items"][0]["name"], "Maggi 2-Minute Noodles")
