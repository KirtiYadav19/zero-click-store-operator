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


class FinalConversationalAITestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='demo_shopkeeper_test', password='Password123!')
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
        self.oil = Product.objects.create(
            shop=self.shop,
            name='Fortune Sunflower Oil',
            price=Decimal('150.00'),
            unit='litre',
            stock=8,
            is_active=True
        )
        self.client = Client()

    def test_case_insensitive_search(self):
        """TEST 1: Case-insensitive product search for 'Rice'."""
        # Query: 'rice', 'RICE', 'Rice?'
        res1 = services.search_product(self.shop.id, "rice")
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0].name, "Rice")

        res2 = services.search_product(self.shop.id, "RICE")
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0].name, "Rice")

        res3 = services.search_product(self.shop.id, "Rice?")
        self.assertEqual(len(res3), 1)
        self.assertEqual(res3[0].name, "Rice")

    def test_hindi_availability_query(self):
        """TEST 2: Hindi availability question."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "क्या हमारे पास चावल है?")
        self.assertIn("Rice", res["reply"])
        self.assertIn("available", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0) # No cart mutation

    def test_hinglish_stock_query(self):
        """TEST 3: Hinglish stock check."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Rice stock mein hai?")
        self.assertIn("Rice", res["reply"])
        self.assertIn("25", res["reply"]) # Reports 25 kg stock
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_price_query(self):
        """TEST 4: Price question without cart mutation."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "Rice kitne ka hai?")
        self.assertIn("60", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_add_product(self):
        """TEST 5: Add 2 kg rice."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 kg rice de do")
        self.assertIn("Added 2 × Rice", res["reply"])
        self.assertEqual(res["cart"]["items"][0]["quantity"], 2)

    def test_followup_set_quantity(self):
        """TEST 6 & 7: Conversational follow-up 'add quantity 2' and 'isko 3 kar do'."""
        session = self.client.session
        # Initial: Rice x 1
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")
        
        # Follow-up: 'add quantity 2' -> SET TOTAL QUANTITY to 2
        res1 = gemini_service._rule_based_nlp_handler(session, self.shop.id, "add quantity 2")
        self.assertEqual(res1["cart"]["items"][0]["quantity"], 2)
        self.assertNotIn("unavailable", res1["reply"])

        # Follow-up: 'isko 3 kar do' -> SET TOTAL QUANTITY to 3
        res2 = gemini_service._rule_based_nlp_handler(session, self.shop.id, "isko 3 kar do")
        self.assertEqual(res2["cart"]["items"][0]["quantity"], 3)

    def test_followup_add_more(self):
        """TEST 8: Conversational follow-up '2 aur add kar do' (Add More)."""
        session = self.client.session
        # Initial: Rice x 1
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")

        # '2 aur add kar do' -> 1 + 2 = 3
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 aur add kar do")
        self.assertEqual(res["cart"]["items"][0]["quantity"], 3)

    def test_followup_remove(self):
        """TEST 9: 'isko hata do' removes referenced item."""
        session = self.client.session
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")
        self.assertEqual(len(services.get_cart(session, self.shop.id)["items"]), 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "isko hata do")
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_maggi_name_resolution(self):
        """TEST 10: 'Maggi' resolves to 'Maggi 2-Minute Noodles'."""
        matches = services.search_product(self.shop.id, "Maggi")
        self.assertEqual(matches[0].name, "Maggi 2-Minute Noodles")

    def test_maggi_hindi_query(self):
        """TEST 11: 'मैगी है क्या?' checks Maggi availability."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "मैगी है क्या?")
        self.assertIn("Maggi 2-Minute Noodles", res["reply"])
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_multiple_products_order(self):
        """TEST 12: '2 kg rice aur 1 litre milk de do' adds both items."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 kg rice aur 1 litre milk de do")
        self.assertEqual(len(res["cart"]["items"]), 2)
        # Total: 2*60 + 1*65 = 120 + 65 = 185
        self.assertEqual(res["cart"]["total"], Decimal('185.00'))

    def test_hindi_and_hinglish_quantities(self):
        """TEST 13 & 14: Hindi and Hinglish worded quantities."""
        session1 = self.client.session
        res1 = gemini_service._rule_based_nlp_handler(session1, self.shop.id, "दो किलो चावल चाहिए")
        self.assertEqual(res1["cart"]["items"][0]["quantity"], 2)

        session2 = self.client.session
        res2 = gemini_service._rule_based_nlp_handler(session2, self.shop.id, "do kilo rice chahiye")
        self.assertEqual(res2["cart"]["items"][0]["quantity"], 2)

    def test_final_confirmation_and_atomic_order(self):
        """TEST 15: Full conversation from 'bas itna hi' to 'haan order kar do'."""
        session = self.client.session
        gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 kg rice de do")
        
        # 'bas itna hi' -> Bill summary, NO order yet
        res_summary = gemini_service._rule_based_nlp_handler(session, self.shop.id, "bas itna hi")
        self.assertIn("order summary", res_summary["reply"].lower())
        self.assertNotIn("placed successfully", res_summary["reply"].lower())

        # 'haan order kar do' -> Atomic order creation + stock deduction
        res_order = gemini_service._rule_based_nlp_handler(session, self.shop.id, "haan order kar do")
        self.assertIn("placed successfully", res_order["reply"].lower())
        
        # Stock deducted in PostgreSQL: 25 - 1 = 24
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, 24)
