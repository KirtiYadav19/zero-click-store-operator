from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from home.models import ShopkeeperProfile, Shop, Product, Customer, Order, OrderItem
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


class Prompt18DownloadReceiptTestCase(TestCase):
    def setUp(self):
        self.shopkeeper_user = User.objects.create_user(username='receipt_shopkeeper', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.shopkeeper_user, phone='9876543210')
        self.shop = Shop.objects.create(
            shopkeeper=self.profile,
            name='Rahul General Store',
            address='Main Market',
            phone='9999999999'
        )
        self.p1 = Product.objects.create(
            shop=self.shop,
            name='Maggi 2-Minute Noodles',
            price=Decimal('15.00'),
            unit='packet',
            stock=20,
            is_active=True
        )
        self.p2 = Product.objects.create(
            shop=self.shop,
            name='Amul Milk 1L',
            price=Decimal('65.00'),
            unit='packet',
            stock=15,
            is_active=True
        )

        self.customer = Customer.objects.create(name='Ankit', phone='9876543210', address='123 Main St')
        self.order = Order.objects.create(shop=self.shop, customer=self.customer, total_amount=Decimal('95.00'), status='confirmed')
        OrderItem.objects.create(order=self.order, product=self.p1, quantity=2, unit_price=Decimal('15.00'), subtotal=Decimal('30.00'))
        OrderItem.objects.create(order=self.order, product=self.p2, quantity=1, unit_price=Decimal('65.00'), subtotal=Decimal('65.00'))

        self.authorized_client = Client()
        session = self.authorized_client.session
        session['last_order_id'] = self.order.id
        session['placed_order_ids'] = [self.order.id]
        session.save()

        self.unauthorized_client = Client()

    def test_authorized_customer_receipt_download(self):
        """Authorized customer session can download PDF receipt."""
        response = self.authorized_client.get(f'/order/{self.order.id}/receipt/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(f'attachment; filename="order_{self.order.id}_receipt.pdf"', response['Content-Disposition'])
        self.assertGreater(len(response.content), 1000)

    def test_unauthorized_customer_receipt_access_denied(self):
        """Unauthorized customer cannot download another customer's receipt (HTTP 403)."""
        response = self.unauthorized_client.get(f'/order/{self.order.id}/receipt/')
        self.assertEqual(response.status_code, 403)

    def test_shopkeeper_can_download_receipt(self):
        """Shopkeeper of the shop can download any receipt for their shop."""
        shopkeeper_client = Client()
        shopkeeper_client.login(username='receipt_shopkeeper', password='Password123!')
        response = shopkeeper_client.get(f'/order/{self.order.id}/receipt/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_nonexistent_order_receipt_404(self):
        """Non-existent order ID returns 404 Not Found."""
        response = self.authorized_client.get('/order/999999/receipt/')
        self.assertEqual(response.status_code, 404)


class CriticalOrderConfirmationVsQuantityTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='critical_bug_shopkeeper', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9999911111')
        self.shop = Shop.objects.create(shopkeeper=self.profile, name='Rahul Kirana Store')
        self.maggi = Product.objects.create(
            shop=self.shop,
            name='Maggi 2-Minute Noodles',
            price=Decimal('15.00'),
            unit='packet',
            stock=10,
            is_active=True
        )
        self.client = Client()

    def test_order_place_kar_do_hindi(self):
        """'ऑर्डर प्लेस कर दो' MUST place order with Qty=1, NOT change quantity to 2."""
        session = self.client.session
        # Step 1 & 2: Add 1 Maggi to cart
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        # Step 3 & 4: User says 'ऑर्डर प्लेस कर दो'
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "ऑर्डर प्लेस कर दो")

        # Step 5 & 6: Order placed, inventory decreased by 1, order item quantity = 1
        self.assertIn("order_id", res)
        order = Order.objects.get(id=res["order_id"])
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertEqual(item.quantity, 1) # MUST BE 1, NOT 2!
        self.assertEqual(item.subtotal, Decimal('15.00'))

        self.maggi.refresh_from_db()
        self.assertEqual(self.maggi.stock, 9) # Stock decreased by exactly 1

    def test_order_kar_do_hinglish(self):
        """'order kar do' MUST place order with Qty=1."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "order kar do")

        self.assertIn("order_id", res)
        order = Order.objects.get(id=res["order_id"])
        self.assertEqual(order.items.first().quantity, 1)

    def test_place_the_order_english(self):
        """'place the order' MUST place order with Qty=1."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "place the order")

        self.assertIn("order_id", res)
        order = Order.objects.get(id=res["order_id"])
        self.assertEqual(order.items.first().quantity, 1)

    def test_haan_order_kar_do(self):
        """'haan order kar do' MUST place order with Qty=1."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "haan order kar do")

        self.assertIn("order_id", res)
        order = Order.objects.get(id=res["order_id"])
        self.assertEqual(order.items.first().quantity, 1)

    def test_explicit_quantity_2_kar_do(self):
        """'quantity 2 kar do' MUST set quantity to 2."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "quantity 2 kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 2)

    def test_explicit_isko_2_kar_do(self):
        """'isko 2 kar do' MUST set quantity to 2."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "isko 2 kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 2)

    def test_2_aur_add_kar_do(self):
        """'2 aur add kar do' MUST increment quantity by 2 (1 + 2 = 3)."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 aur add kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 3)

    def test_aur_maggi_chahiye_does_not_place_order(self):
        """'aur Maggi chahiye' should NOT automatically place an order."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "aur Maggi chahiye")

        self.assertNotIn("order_id", res)
        self.assertEqual(Order.objects.filter(shop=self.shop).count(), 0)


class TwoConversationalBugFixesTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='two_bugs_shopkeeper', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9988112233')
        self.shop = Shop.objects.create(shopkeeper=self.profile, name='Quick Kirana Store')
        self.maggi = Product.objects.create(
            shop=self.shop,
            name='Maggi 2-Minute Noodles',
            price=Decimal('15.00'),
            unit='packet',
            stock=5,
            is_active=True
        )
        self.client = Client()

    def test_bug1_1_hi_kar_do(self):
        """TEST 1: '1 hi kar do' sets quantity to 1 without searching for 'hi kar'."""
        session = self.client.session
        session['last_referenced_product_id'] = self.maggi.id
        session.save()

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "1 hi kar do")

        self.assertNotIn("hamare store", res["reply"].lower()) # Must NOT say "'hi kar' hamare store mein available nahi hai"
        self.assertEqual(res["cart"]["items"][0]["quantity"], 1)
        self.assertEqual(res["cart"]["items"][0]["product_id"], self.maggi.id)

    def test_bug1_ek_kar_do(self):
        """TEST 2: 'ek kar do' sets quantity to 1."""
        session = self.client.session
        session['last_referenced_product_id'] = self.maggi.id
        session.save()

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "ek kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 1)

    def test_bug1_2_kar_do(self):
        """TEST 3: '2 kar do' sets quantity to 2."""
        session = self.client.session
        session['last_referenced_product_id'] = self.maggi.id
        session.save()

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "2 kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 2)

    def test_bug1_order_kar_do(self):
        """TEST 4: 'order kar do' triggers final order confirmation, NOT quantity 2."""
        session = self.client.session
        services.add_to_cart(session, self.shop.id, self.maggi.id, 1)

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "order kar do")

        self.assertIn("order_id", res)
        order = Order.objects.get(id=res["order_id"])
        self.assertEqual(order.items.first().quantity, 1) # Must be 1, NOT 2

    def test_bug2_jitne_maggi_stock_me_hai_sare_order_kar_do(self):
        """TEST 5: Cart empty, DB stock = 5 -> Adds 5 to cart, asks confirmation, does NOT say cart empty."""
        session = self.client.session

        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "jitne maggi stock me hai sare order kar do")

        self.assertNotIn("cart empty", res["reply"].lower())
        self.assertEqual(res["cart"]["items"][0]["quantity"], 5)
        self.assertEqual(res["cart"]["total"], Decimal('75.00'))
        self.assertIn("5 packets", res["reply"].lower())
        self.assertIn("place kar du", res["reply"].lower())

        # Verify no DB Order was created yet
        self.assertEqual(Order.objects.filter(shop=self.shop).count(), 0)

        # Now customer confirms: "haan" -> Order created!
        res_confirm = gemini_service._rule_based_nlp_handler(session, self.shop.id, "haan")
        self.assertIn("order_id", res_confirm)
        self.assertEqual(Order.objects.filter(shop=self.shop).count(), 1)
        self.maggi.refresh_from_db()
        self.assertEqual(self.maggi.stock, 0) # Stock reduced from 5 to 0

    def test_bug2_out_of_stock_all_order(self):
        """TEST 6: Cart empty, DB stock = 0 -> Returns out of stock message, no cart item created."""
        self.maggi.stock = 0
        self.maggi.save()

        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "jitni maggi stock mein hai saari order kar do")

        self.assertIn("out of stock", res["reply"].lower())
        self.assertEqual(len(res["cart"]["items"]), 0)

    def test_bug2_saari_available_maggi_order_kar_do(self):
        """TEST 7: 'saari available Maggi order kar do' adds exact available stock to cart and shows bill."""
        session = self.client.session
        res = gemini_service._rule_based_nlp_handler(session, self.shop.id, "saari available Maggi order kar do")

        self.assertEqual(res["cart"]["items"][0]["quantity"], 5)
        self.assertEqual(res["cart"]["total"], Decimal('75.00'))
        self.assertIn("place kar du", res["reply"].lower())


class LocationAndMultilingualSearchTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='loc_shopkeeper', password='Password123!')
        self.profile = ShopkeeperProfile.objects.create(user=self.user, phone='9990001112')

        # Shop 1: Connaught Place, Delhi (28.6315, 77.2167)
        self.shop_cp = Shop.objects.create(
            shopkeeper=self.profile,
            name='CP Kirana Store',
            address='Connaught Place, Delhi',
            latitude=Decimal('28.631500'),
            longitude=Decimal('77.216700'),
            is_active=True
        )

        # Shop 2: Gurgaon (28.4595, 77.0266) - approx 25-30 km away from CP
        self.shop_ggn = Shop.objects.create(
            shopkeeper=self.profile,
            name='Gurgaon Retail Store',
            address='DLF Cyber City, Gurgaon',
            latitude=Decimal('28.459500'),
            longitude=Decimal('77.026600'),
            is_active=True
        )

        # Shop 3: Location disabled
        self.shop_no_loc = Shop.objects.create(
            shopkeeper=self.profile,
            name='No Location Store',
            address='Somewhere in India',
            is_active=True
        )

        # Products in CP Kirana
        self.coca_cola = Product.objects.create(
            shop=self.shop_cp,
            name='Coca Cola 750ml',
            price=Decimal('40.00'),
            unit='bottle',
            stock=50,
            is_active=True
        )
        self.surf_excel = Product.objects.create(
            shop=self.shop_cp,
            name='Surf Excel Easy Wash 1kg',
            price=Decimal('140.00'),
            unit='packet',
            stock=30,
            is_active=True
        )
        self.parle_g = Product.objects.create(
            shop=self.shop_cp,
            name='Parle-G Gold 100g',
            price=Decimal('10.00'),
            unit='packet',
            stock=100,
            is_active=True
        )

        self.client = Client()

    def test_hindi_coca_cola_product_search(self):
        """'कोका कोला', 'कोक', 'coke' all match 'Coca Cola 750ml'."""
        res1 = services.search_product(self.shop_cp.id, "कोका कोला")
        self.assertTrue(len(res1) > 0)
        self.assertEqual(res1[0].name, "Coca Cola 750ml")

        res2 = services.search_product(self.shop_cp.id, "कोक")
        self.assertTrue(len(res2) > 0)
        self.assertEqual(res2[0].name, "Coca Cola 750ml")

        res3 = services.search_product(self.shop_cp.id, "coke")
        self.assertTrue(len(res3) > 0)
        self.assertEqual(res3[0].name, "Coca Cola 750ml")

    def test_hindi_surf_excel_and_parle_g_search(self):
        """'सर्फ' matches Surf Excel and 'पारले जी' matches Parle-G."""
        res_surf = services.search_product(self.shop_cp.id, "सर्फ")
        self.assertTrue(len(res_surf) > 0)
        self.assertEqual(res_surf[0].name, "Surf Excel Easy Wash 1kg")

        res_parle = services.search_product(self.shop_cp.id, "पारले जी")
        self.assertTrue(len(res_parle) > 0)
        self.assertEqual(res_parle[0].name, "Parle-G Gold 100g")

    def test_haversine_distance_calculation(self):
        """Haversine distance between Connaught Place and Gurgaon should be approx 28 km (28000m)."""
        dist_meters = services.calculate_haversine_distance(28.6315, 77.2167, 28.4595, 77.0266)
        self.assertIsNotNone(dist_meters)
        self.assertGreater(dist_meters, 20000) # > 20 km
        self.assertLess(dist_meters, 35000)    # < 35 km

    def test_distance_formatting(self):
        """Meters formatted cleanly as 'X m away' or 'Y km away'."""
        self.assertEqual(services.format_distance(450), "450 m away")
        self.assertEqual(services.format_distance(1250), "1.2 km away")
        self.assertEqual(services.format_distance(2500), "2.5 km away")
        self.assertIsNone(services.format_distance(None))

    def test_nearby_shops_sorting(self):
        """Customer located near CP (28.6300, 77.2150) should get CP store first."""
        shops_data = services.get_nearby_active_shops(customer_lat=28.6300, customer_lng=77.2150)
        self.assertTrue(len(shops_data) >= 2)
        # First shop must be CP Kirana Store
        self.assertEqual(shops_data[0]['shop'].name, "CP Kirana Store")
        self.assertIsNotNone(shops_data[0]['distance_meters'])
        self.assertLess(shops_data[0]['distance_meters'], 1000) # Less than 1 km

    def test_shop_location_is_enabled(self):
        """is_location_enabled returns True when latitude & longitude are present."""
        self.assertTrue(self.shop_cp.is_location_enabled())
        self.assertFalse(self.shop_no_loc.is_location_enabled())

    def test_shopkeeper_edit_shop_coordinates(self):
        """Shopkeeper can edit shop coordinates via POST."""
        self.client.login(username='loc_shopkeeper', password='Password123!')
        response = self.client.post('/shopkeeper/edit-shop/', {
            'name': 'CP Kirana Store Updated',
            'address': 'Inner Circle, CP',
            'phone': '9990001112',
            'latitude': '28.6320',
            'longitude': '77.2170'
        })
        self.assertEqual(response.status_code, 302) # Redirects to dashboard
        self.shop_cp.refresh_from_db()
        self.assertEqual(self.shop_cp.name, 'CP Kirana Store Updated')
        self.assertEqual(self.shop_cp.latitude, Decimal('28.632000'))
        self.assertEqual(self.shop_cp.longitude, Decimal('77.217000'))




