from decimal import Decimal
from django.db import transaction
from django.shortcuts import get_object_or_404
from .models import Shop, Product, Customer, Order, OrderItem

def get_active_shops():
    """Retrieve all active shops."""
    return Shop.objects.filter(is_active=True).order_by('name')

def get_shop_or_404(shop_id):
    """Retrieve an active shop or raise 404."""
    return get_object_or_404(Shop, id=shop_id, is_active=True)

# ---------------------------------------------------------------------------
# Small Hindi/Hinglish → common English product-name transliteration map.
# Keys are lowercase Devanagari / Hinglish spellings.
# Values are the English terms that typically appear in DB product names.
# Extend here as needed — no external API required.
# ---------------------------------------------------------------------------
_TRANSLITERATION_MAP = {
    # Maggi
    "मैगी": "maggi",
    "maggie": "maggi",
    # Atta / flour
    "आटा": "atta",
    "आटे": "atta",
    "aata": "atta",
    # Milk / Doodh
    "दूध": "milk",
    "doodh": "milk",
    "dudh": "milk",
    # Salt
    "नमक": "salt",
    "namak": "salt",
    # Biscuits
    "बिस्किट": "biscuit",
    "biskut": "biscuit",
    "biscuit": "biscuit",
    # Oil
    "तेल": "oil",
    "tel": "oil",
    # Sugar
    "चीनी": "sugar",
    "chini": "sugar",
    # Rice
    "चावल": "rice",
    "chawal": "rice",
    # Dal / lentils
    "दाल": "dal",
    # Tea
    "चाय": "tea",
    "chai": "tea",
}


def _expand_query_variants(query: str) -> list[str]:
    """
    Return a list of search terms to try for a given query string.
    Includes the original, lowercased, and any transliteration expansions.
    """
    q = query.strip()
    variants = []

    # 1. Original query (preserves Devanagari for icontains)
    if q:
        variants.append(q)

    # 2. Lowercase version (helps for English mixed-case)
    ql = q.lower()
    if ql not in variants:
        variants.append(ql)

    # 3. Transliteration lookup (Devanagari / Hinglish → English)
    mapped = _TRANSLITERATION_MAP.get(ql)
    if mapped and mapped not in variants:
        variants.append(mapped)

    return variants


def search_product(shop_id, query):
    """
    Search active products in a specific shop.

    Tries multiple query variants (original + transliterations) so that
    Hindi terms like 'मैगी' correctly match 'Maggi 2-Minute Noodles',
    and Hinglish terms like 'aata' match 'Aashirvaad Atta'.

    Shop isolation and is_active filter are always preserved.
    """
    import logging
    log = logging.getLogger(__name__)

    variants = _expand_query_variants(query)
    log.debug("[search_product] shop=%s raw_query=%r variants=%r", shop_id, query, variants)

    seen_ids: set[int] = set()
    results = []

    for variant in variants:
        qs = Product.objects.filter(
            shop_id=shop_id,
            is_active=True,
            name__icontains=variant
        ).order_by('name')
        for p in qs:
            if p.id not in seen_ids:
                seen_ids.add(p.id)
                results.append(p)

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

        # 5. Clear session cart
        clear_cart(session, shop_id)

        return order, None
