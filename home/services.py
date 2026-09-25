import math
import logging
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.db import transaction
from .models import Shop, Product, Customer, Order, OrderItem

log = logging.getLogger(__name__)

def get_active_shops():
    """Retrieve all active shops."""
    return Shop.objects.filter(is_active=True).order_by('name')

def get_shop_or_404(shop_id):
    """Retrieve an active shop or raise 404."""
    return get_object_or_404(Shop, id=shop_id, is_active=True)

# ---------------------------------------------------------------------------
# Haversine Geographic Distance Calculation
# ---------------------------------------------------------------------------
def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate geographic distance in meters between two lat/lon points using Haversine formula.
    Returns distance in meters (float).
    """
    try:
        R = 6371000.0  # Earth radius in meters
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        delta_phi = math.radians(float(lat2) - float(lat1))
        delta_lambda = math.radians(float(lon2) - float(lon1))

        a = (math.sin(delta_phi / 2.0) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return R * c
    except (ValueError, TypeError, ZeroDivisionError):
        return None

def format_distance(distance_meters):
    """Format distance in meters: < 1000m -> '650 m away', >= 1000m -> '1.2 km away'."""
    if distance_meters is None:
        return None
    if distance_meters < 1000:
        return f"{int(round(distance_meters))} m away"
    else:
        km = distance_meters / 1000.0
        return f"{km:.1f} km away"

# ---------------------------------------------------------------------------
# Shop Priority Scoring
# ---------------------------------------------------------------------------

def _compute_inventory_score(shop):
    """
    Sum of active product stock for a shop.
    More stock → higher inventory score.
    """
    from django.db.models import Sum
    result = shop.products.filter(is_active=True).aggregate(total=Sum('stock'))
    return result['total'] or 0


def _compute_purchase_score(shop, customer_phone):
    """
    Total amount (₹) spent by this customer (identified by phone) at this shop.
    Returns 0 when customer_phone is None or no purchase history exists.
    """
    if not customer_phone:
        return 0
    from decimal import Decimal
    try:
        total = (
            Order.objects
            .filter(shop=shop, customer__phone=customer_phone, status__in=['confirmed', 'completed'])
            .aggregate(total=__import__('django.db.models', fromlist=['Sum']).Sum('total_amount'))
        )['total']
        return float(total or 0)
    except Exception:
        return 0


def _compute_priority_score(distance_meters, purchase_amount, inventory_total,
                            max_distance, max_purchase, max_inventory):
    """
    Composite priority score in [0, 100].

    Weights:
      - Distance   : 40 %  (closer → higher score)
      - Purchase   : 35 %  (more spent → higher score)
      - Inventory  : 25 %  (more stock → higher score)

    Each factor is normalised to [0, 1] before weighting.
    """
    # Distance factor: 1 when distance is 0, 0 when distance equals max
    if max_distance and max_distance > 0 and distance_meters is not None:
        dist_factor = 1.0 - (distance_meters / max_distance)
        dist_factor = max(0.0, min(1.0, dist_factor))
    elif distance_meters is None:
        dist_factor = 0.0          # no location data → lowest distance rank
    else:
        dist_factor = 1.0          # only one shop with location

    # Purchase factor
    if max_purchase and max_purchase > 0:
        purchase_factor = purchase_amount / max_purchase
    else:
        purchase_factor = 0.0

    # Inventory factor
    if max_inventory and max_inventory > 0:
        inventory_factor = inventory_total / max_inventory
    else:
        inventory_factor = 0.0

    score = (dist_factor * 40.0) + (purchase_factor * 35.0) + (inventory_factor * 25.0)
    return round(score, 1)


def _priority_label(score):
    """Return a human-readable priority tier label."""
    if score >= 65:
        return 'Top Pick'
    if score >= 40:
        return 'Recommended'
    if score >= 20:
        return 'Nearby'
    return 'Available'


def get_nearby_active_shops(customer_lat=None, customer_lng=None, customer_phone=None):
    """
    Retrieve active shops ranked by a composite priority score:
      • Distance   (40 %) — closer is better
      • Purchase history (35 %) — more spent by this customer at this shop is better
      • Inventory  (25 %) — more total stock is better

    Shops without coordinates are listed after location-enabled shops.
    Returns list of dicts:
      {
        'shop': shop_obj,
        'distance_meters': float|None,
        'distance_text': str|None,
        'priority_score': float,        # 0-100
        'priority_label': str,
        'inventory_total': int,
        'purchase_total': float,
      }
    """
    from django.db.models import Sum

    shops = list(Shop.objects.filter(is_active=True).order_by('name'))

    # --- Pre-compute inventory totals for all shops in one query ---
    inv_map = {}
    inv_qs = (
        Product.objects
        .filter(shop__in=shops, is_active=True)
        .values('shop_id')
        .annotate(total=Sum('stock'))
    )
    for row in inv_qs:
        inv_map[row['shop_id']] = row['total'] or 0

    # --- Pre-compute purchase totals for this customer ---
    purchase_map = {}
    if customer_phone:
        try:
            purchase_qs = (
                Order.objects
                .filter(
                    shop__in=shops,
                    customer__phone=customer_phone,
                    status__in=['confirmed', 'completed'],
                )
                .values('shop_id')
                .annotate(total=Sum('total_amount'))
            )
            for row in purchase_qs:
                purchase_map[row['shop_id']] = float(row['total'] or 0)
        except Exception:
            pass

    # --- Compute distances ---
    clat, clng = None, None
    if customer_lat is not None and customer_lng is not None:
        try:
            clat = float(customer_lat)
            clng = float(customer_lng)
        except (ValueError, TypeError):
            clat, clng = None, None

    result = []
    for shop in shops:
        if clat is not None and clng is not None and shop.latitude is not None and shop.longitude is not None:
            dist = calculate_haversine_distance(clat, clng, float(shop.latitude), float(shop.longitude))
            dist_text = format_distance(dist)
        else:
            dist = None
            dist_text = None

        result.append({
            'shop': shop,
            'distance_meters': dist,
            'distance_text': dist_text,
            'inventory_total': inv_map.get(shop.id, 0),
            'purchase_total': purchase_map.get(shop.id, 0.0),
            'priority_score': 0.0,
            'priority_label': 'Available',
        })

    # --- Normalise across the full result set ---
    distances_with_val = [r['distance_meters'] for r in result if r['distance_meters'] is not None]
    max_distance  = max(distances_with_val) if distances_with_val else 0
    max_purchase  = max((r['purchase_total'] for r in result), default=0)
    max_inventory = max((r['inventory_total'] for r in result), default=0)

    for r in result:
        score = _compute_priority_score(
            r['distance_meters'],
            r['purchase_total'],
            r['inventory_total'],
            max_distance,
            max_purchase,
            max_inventory,
        )
        r['priority_score'] = score
        r['priority_label'] = _priority_label(score)

    # --- Sort: highest priority first; ties broken by distance then name ---
    result.sort(key=lambda x: (
        -x['priority_score'],
        x['distance_meters'] if x['distance_meters'] is not None else float('inf'),
        x['shop'].name,
    ))
    return result


# ---------------------------------------------------------------------------
# Multilingual Hindi/Hinglish → English product-name transliteration map.
# Keys are lowercase Devanagari / Hinglish spellings.
# Values are the English terms that typically appear in DB product names.
# ---------------------------------------------------------------------------
_TRANSLITERATION_MAP = {
    # Coca Cola / Coke
    "कोका कोला": "coca cola",
    "कोका-कोला": "coca cola",
    "कोकाकोला": "coca cola",
    "कोका": "coca cola",
    "कोक": "coca cola",
    "coke": "coca cola",
    "coca-cola": "coca cola",
    "coca": "coca cola",
    "cola": "coca cola",

    # Maggi / Noodles
    "मैगी": "maggi",
    "maggie": "maggi",
    "noodles": "maggi",
    "नूडल्स": "maggi",
    "maggi": "maggi",

    # Atta / flour
    "आटा": "atta",
    "आटे": "atta",
    "aata": "atta",
    "chakki": "atta",
    "flour": "atta",
    "atta": "atta",

    # Milk / Doodh
    "दूध": "milk",
    "doodh": "milk",
    "dudh": "milk",
    "milk": "milk",

    # Salt / Namak
    "नमक": "salt",
    "namak": "salt",
    "salt": "salt",

    # Biscuits / Parle-G
    "बिस्किट": "biscuit",
    "बिस्कुट": "biscuit",
    "biskut": "biscuit",
    "biscuit": "biscuit",
    "biscuits": "biscuit",
    "पारले": "parle-g",
    "पारले जी": "parle-g",
    "पारले-जी": "parle-g",
    "parle": "parle-g",
    "parle-g": "parle-g",
    "parleg": "parle-g",

    # Oil / Tel
    "तेल": "oil",
    "tel": "oil",
    "sunflower": "oil",
    "fortune": "oil",
    "oil": "oil",

    # Sugar / Chini
    "चीनी": "sugar",
    "chini": "sugar",
    "sugar": "sugar",

    # Rice / Chawal
    "चावल": "rice",
    "chawal": "rice",
    "chawl": "rice",
    "rice": "rice",

    # Dal / Lentils
    "दाल": "dal",
    "dal": "dal",

    # Tea / Chai
    "चाय": "tea",
    "chai": "tea",
    "tea": "tea",

    # Detergent / Surf Excel
    "सर्फ": "surf excel",
    "सर्फ एक्सेल": "surf excel",
    "surf": "surf excel",
    "surf excel": "surf excel",
    "detergent": "surf excel",
}


def _expand_query_variants(query: str) -> list[str]:
    """
    Return an ordered list of search terms to try for a given query string.
    Normalizes whitespace, trims punctuation, handles Devanagari transliteration.
    """
    import re
    # Strip common punctuation while preserving word characters in Unicode
    cleaned = re.sub(r'[?.,!\-:;"\'\(\)]', ' ', query).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    variants = []
    if cleaned:
        variants.append(cleaned)

    ql = cleaned.lower()
    if ql and ql not in variants:
        variants.append(ql)

    # Whole query transliteration lookup
    mapped = _TRANSLITERATION_MAP.get(ql)
    if mapped and mapped not in variants:
        variants.append(mapped)

    # Hyphen/space normalized lookup (e.g. "coca-cola" vs "coca cola")
    normalized_spaces = ql.replace('-', ' ')
    if normalized_spaces in _TRANSLITERATION_MAP:
        m_space = _TRANSLITERATION_MAP[normalized_spaces]
        if m_space not in variants:
            variants.append(m_space)

    # Token-level transliteration lookup & combination
    tokens = ql.split()
    mapped_tokens = []
    for token in tokens:
        if token in _TRANSLITERATION_MAP:
            m_token = _TRANSLITERATION_MAP[token]
            mapped_tokens.append(m_token)
            if m_token not in variants:
                variants.append(m_token)
        else:
            mapped_tokens.append(token)

    combined_mapped = " ".join(mapped_tokens)
    if combined_mapped and combined_mapped not in variants:
        variants.append(combined_mapped)

    return variants


def search_product(shop_id, query):
    """
    Search active products in a specific shop with prioritized matching:
    1. Exact match (case-insensitive name__iexact)
    2. Starts with (case-insensitive name__istartswith)
    3. Contains (case-insensitive name__icontains)

    Shop isolation and is_active filter are strictly preserved.
    """
    import logging
    log = logging.getLogger(__name__)

    variants = _expand_query_variants(query)
    log.debug("[search_product] shop=%s raw_query=%r variants=%r", shop_id, query, variants)

    if not variants:
        return []

    seen_ids: set[int] = set()
    exact_matches = []
    startswith_matches = []
    contains_matches = []

    for variant in variants:
        # 1. Exact case-insensitive matches
        exact_qs = Product.objects.filter(
            shop_id=shop_id,
            is_active=True,
            name__iexact=variant
        )
        for p in exact_qs:
            if p.id not in seen_ids:
                seen_ids.add(p.id)
                exact_matches.append(p)

        # 2. Starts-with case-insensitive matches
        starts_qs = Product.objects.filter(
            shop_id=shop_id,
            is_active=True,
            name__istartswith=variant
        ).order_by('name')
        for p in starts_qs:
            if p.id not in seen_ids:
                seen_ids.add(p.id)
                startswith_matches.append(p)

        # 3. Contains case-insensitive matches
        contains_qs = Product.objects.filter(
            shop_id=shop_id,
            is_active=True,
            name__icontains=variant
        ).order_by('name')
        for p in contains_qs:
            if p.id not in seen_ids:
                seen_ids.add(p.id)
                contains_matches.append(p)

    # Return ranked results: exact matches first, then prefix matches, then substring matches
    results = exact_matches + startswith_matches + contains_matches
    log.debug("[search_product] found %d product(s): %s",
              len(results), [p.name for p in results])
    return results

def check_stock(shop_id, product_id, quantity):
    """Check if requested quantity is available in stock for a shop's product."""
    try:
        product = Product.objects.get(id=product_id, shop_id=shop_id, is_active=True)
        return product.stock >= quantity, product.stock, product
    except Product.DoesNotExist:
        return False, 0, None

def _get_session_cart_key(shop_id):
    return f'cart_shop_{shop_id}'

def get_cart(session, shop_id):
    """
    Retrieve cart details from session for a specific shop.
    Returns dict: { 'items': [ {product, quantity, subtotal} ], 'total': Decimal }
    """
    cart_key = _get_session_cart_key(shop_id)
    raw_cart = session.get(cart_key, {}) # {str(product_id): quantity}
    
    items = []
    total = Decimal('0.00')

    if not raw_cart:
        return {'items': items, 'total': total, 'count': 0}

    product_ids = [int(pid) for pid in raw_cart.keys()]
    products = Product.objects.filter(id__in=product_ids, shop_id=shop_id, is_active=True)
    product_dict = {p.id: p for p in products}

    for pid_str, qty in list(raw_cart.items()):
        pid = int(pid_str)
        if pid in product_dict:
            product = product_dict[pid]
            subtotal = product.price * Decimal(qty)
            total += subtotal
            items.append({
                'product_id': product.id,
                'name': product.name,
                'price': product.price,
                'unit': product.unit,
                'quantity': qty,
                'stock': product.stock,
                'subtotal': subtotal,
            })
        else:
            # Product no longer exists/active, remove from session cart
            del raw_cart[pid_str]
            session[cart_key] = raw_cart

    count = sum(item['quantity'] for item in items)
    return {'items': items, 'total': total, 'count': count}

def add_to_cart(session, shop_id, product_id, quantity=1):
    """Add a quantity of product to session cart after validation."""
    if quantity <= 0:
        return False, "Quantity must be greater than zero."

    is_avail, available_stock, product = check_stock(shop_id, product_id, quantity)
    if not product:
        return False, "Product not found or inactive in this shop."

    cart_key = _get_session_cart_key(shop_id)
    raw_cart = session.get(cart_key, {})
    current_qty = raw_cart.get(str(product_id), 0)
    new_qty = current_qty + quantity

    if new_qty > product.stock:
        return False, f"Only {product.stock} items available in stock. Cannot add {quantity} more."

    raw_cart[str(product_id)] = new_qty
    session[cart_key] = raw_cart
    session.modified = True
    return True, f"Added {quantity} x {product.name} to cart."

def update_cart_quantity(session, shop_id, product_id, quantity):
    """Set exact quantity for a product in session cart."""
    cart_key = _get_session_cart_key(shop_id)
    raw_cart = session.get(cart_key, {})
    pid_str = str(product_id)

    if quantity <= 0:
        if pid_str in raw_cart:
            del raw_cart[pid_str]
            session[cart_key] = raw_cart
            session.modified = True
        return True, "Item removed from cart."

    is_avail, available_stock, product = check_stock(shop_id, product_id, quantity)
    if not product:
        return False, "Product not found or inactive."

    if quantity > product.stock:
        return False, f"Cannot set quantity to {quantity}. Only {product.stock} in stock."

    raw_cart[pid_str] = quantity
    session[cart_key] = raw_cart
    session.modified = True
    return True, "Cart updated."

def remove_from_cart(session, shop_id, product_id):
    """Remove product from session cart."""
    return update_cart_quantity(session, shop_id, product_id, 0)

def clear_cart(session, shop_id):
    """Clear all items in session cart for a shop."""
    cart_key = _get_session_cart_key(shop_id)
    if cart_key in session:
        del session[cart_key]
        session.modified = True
    return True

def place_order_atomic(session, shop_id, customer_name, customer_phone, customer_address=""):
    """
    Execute atomic order creation with PostgreSQL inventory deduction.
    """
    cart_details = get_cart(session, shop_id)
    if not cart_details['items']:
        return None, "Cart is empty."

    if not customer_name or not customer_phone:
        return None, "Customer name and phone number are required."

    with transaction.atomic():
        shop = get_shop_or_404(shop_id)

        # 1. Retrieve or create customer record
        customer, _ = Customer.objects.get_or_create(
            phone=customer_phone,
            defaults={'name': customer_name, 'address': customer_address}
        )
        if customer_name and customer.name != customer_name:
            customer.name = customer_name
            customer.save()

        # 2. Lock product rows and re-check stock in database
        product_ids = [item['product_id'] for item in cart_details['items']]
        locked_products = Product.objects.select_for_update().filter(id__in=product_ids, shop=shop, is_active=True)
        locked_dict = {p.id: p for p in locked_products}

        total_amount = Decimal('0.00')
        order_items_to_create = []

        for item in cart_details['items']:
            pid = item['product_id']
            qty = item['quantity']
            if pid not in locked_dict:
                raise ValueError(f"Product '{item['name']}' is no longer available.")

            product = locked_dict[pid]
            if product.stock < qty:
                raise ValueError(f"Insufficient stock for '{product.name}'. Available: {product.stock}, requested: {qty}.")

            subtotal = product.price * Decimal(qty)
            total_amount += subtotal

            order_items_to_create.append({
                'product': product,
                'quantity': qty,
                'unit_price': product.price,
                'subtotal': subtotal
            })

        # 3. Create Order
        order = Order.objects.create(
            shop=shop,
            customer=customer,
            total_amount=total_amount,
            status='confirmed'
        )

        # 4. Create OrderItems & Deduct Stock
        for item_data in order_items_to_create:
            OrderItem.objects.create(
                order=order,
                product=item_data['product'],
                quantity=item_data['quantity'],
                unit_price=item_data['unit_price'],
                subtotal=item_data['subtotal']
            )
            # Deduct stock
            prod = item_data['product']
            prod.stock -= item_data['quantity']
            prod.save()

        # 5. Clear session cart and track order authorization
        clear_cart(session, shop_id)
        session['last_order_id'] = order.id
        placed_orders = session.get('placed_order_ids', [])
        if order.id not in placed_orders:
            placed_orders.append(order.id)
        session['placed_order_ids'] = placed_orders
        session.modified = True

        return order, None
