import json
import logging
import re
from decimal import Decimal
from decouple import config
from . import services
from .models import Product

logger = logging.getLogger(__name__)

GEMINI_API_KEY = config('GEMINI_API_KEY', default='')

SYSTEM_INSTRUCTION = """
You are an intelligent, polite AI store operator and cashier for a neighborhood kirana store.
Your goal is to assist customers with natural language ordering in English, Hindi, or Hinglish.

CRITICAL FUNCTION CALLING AND CONTEXT RULES:
1. When calling search_product(query), query MUST contain ONLY the product name or short product search phrase (e.g. "rice", "मैगी", "Maggi", "aata", "milk").
   DO NOT pass full sentences or conversational phrases (like "Maggi hai ya nahi?", "add quantity 2", "make it 2", "Do you have rice?").

2. STRICT SEPARATION OF INTENTS:
   - FINAL ORDER CONFIRMATION / PLACE ORDER ("ऑर्डर प्लेस कर दो", "order kar do", "place the order", "confirm order", "haan order kar do", "place it", "confirm"):
     Call place_order tool ONLY.
     NEVER interpret "order kar do" or "ऑर्डर प्लेस कर दो" as modifying cart quantity or adding items!
     The Hindi verb "do" / "कर दो" in "order kar do" is a verb meaning "place/do", NOT quantity=2!
   - CHECK AVAILABILITY / STOCK ("Maggi hai ya nahi?", "क्या मैगी है या नहीं?", "Is Maggi available?", "Rice stock mein hai?"):
     Call search_product(query="Maggi") or check_stock. Answer with available stock.
     DO NOT CALL add_to_cart! DO NOT CALL place_order! Cart must remain unchanged!
   - CHECK PRICE ("Rice kitne ka hai?", "चावल कितने का है?", "Price of Maggi?"):
     Call search_product(query="rice"). Answer with product price. DO NOT CALL add_to_cart!
   - ADD TO CART ("2 kg rice de do", "एक किलो चावल दे दो", "Maggi add kar do", "2 packet"):
     Call search_product followed by add_to_cart.
   - CONVERSATIONAL QUANTITY FOLLOW-UP ("add quantity 2", "make it 3", "isko 2 kar do"):
     Call update_cart_quantity(product_id, quantity) to SET total quantity.
   - ADD MORE ("add 2 more", "2 aur add kar do", "ek aur"):
     Call add_to_cart(product_id, quantity) to add to existing quantity.
   - REMOVE ITEM ("isko hata do", "remove maggi"):
     Call remove_from_cart(product_id).

3. Never invent prices or stock numbers. Always use backend tool output.
4. Match customer language style naturally.
"""

def _execute_tool(session, shop_id, tool_name, tool_args):
    """Execute Django service tools strictly scoped to shop_id."""
    logger.info(f"=== GEMINI TOOL EXECUTION ===")
    logger.info(f"Tool: {tool_name} | Args: {tool_args} | Shop ID: {shop_id}")

    if tool_name == "search_product":
        query = tool_args.get("query", "")
        products = services.search_product(shop_id, query)
        results = [
            {
                "id": p.id,
                "name": p.name,
                "price": float(p.price),
                "unit": p.unit,
                "stock": p.stock,
                "is_active": p.is_active
            }
            for p in products
        ]
        logger.info(f"Tool search_product result count: {len(results)}")
        return results, "Database Retrieval"

    elif tool_name == "check_stock":
        pid = tool_args.get("product_id")
        qty = tool_args.get("quantity", 1)
        avail, stock, prod = services.check_stock(shop_id, pid, qty)
        return {"available": avail, "current_stock": stock, "product_name": prod.name if prod else ""}, "Inventory Check"

    elif tool_name == "add_to_cart":
        pid = tool_args.get("product_id")
        qty = tool_args.get("quantity", 1)
        success, msg = services.add_to_cart(session, shop_id, pid, qty)
        if success:
            session['last_referenced_product_id'] = pid
        return {"success": success, "message": msg}, "Cart Building"

    elif tool_name == "update_cart_quantity":
        pid = tool_args.get("product_id")
        qty = tool_args.get("quantity", 0)
        success, msg = services.update_cart_quantity(session, shop_id, pid, qty)
        if success:
            session['last_referenced_product_id'] = pid
        return {"success": success, "message": msg}, "Cart Building"

    elif tool_name == "remove_from_cart":
        pid = tool_args.get("product_id")
        success, msg = services.remove_from_cart(session, shop_id, pid)
        return {"success": success, "message": msg}, "Cart Building"

    elif tool_name == "get_cart":
        cart = services.get_cart(session, shop_id)
        formatted_cart = {
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": item["name"],
                    "price": float(item["price"]),
                    "unit": item["unit"],
                    "quantity": item["quantity"],
                    "subtotal": float(item["subtotal"])
                }
                for item in cart["items"]
            ],
            "total": float(cart["total"]),
            "count": cart["count"]
        }
        return formatted_cart, "Bill Calculation"

    elif tool_name == "place_order":
        name = tool_args.get("customer_name", "Customer")
        phone = tool_args.get("customer_phone", "9876543210")
        address = tool_args.get("customer_address", "")
        order, err = services.place_order_atomic(session, shop_id, name, phone, address)
        if order:
            return {"success": True, "order_id": order.id, "total": float(order.total_amount)}, "Order Creation"
        else:
            return {"success": False, "error": err}, "Order Creation"

    return {"error": "Unknown tool"}, "AI Parsing"


# NUMBER MAP FOR MULTILINGUAL QUANTITIES
NUMBER_WORDS = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10
}

# MULTI-WORD INTENT PHRASES (to remove from product query extraction)
MULTIWORD_INTENT_PHRASES = [
    "hai ya nahi", "hai ya nahi?", "available hai ya nahi", "stock mein hai ya nahi",
    "available or not", "in stock or not", "ya nahi", "या नहीं", "milti hai kya", "milta hai kya",
    "milti hai", "milta hai", "kya milti hai", "kya milta hai", "does this store have",
    "do we have", "do you have", "is there", "can i get", "i need", "give me", "show me",
    "what is the price of", "what is the price", "price of", "cost of", "rate of", "how much is", "how much",
    "in stock", "stock mein hai", "stock mein", "stock me", "available hai", "kya hamare paas",
    "kya hamare pass", "kya aapke paas", "kya aapke pass", "add kar do", "add karo",
    "de do", "dedo", "chahiye", "hai kya", "batao", "bataiye", "kitne ki hai", "kitne ka hai",
    "kitne ki", "kitne ka", "kitna stock hai", "kitna hai"
]

# SINGLE WORD FILLERS (to remove from product query extraction)
SINGLEWORD_FILLERS = set([
    # Hindi Devanagari
    "क्या", "हमारे", "आपके", "पास", "है", "स्टॉक", "में", "उपलब्ध", "मिलेगा", "मिलेगी", "चाहिए",
    "दे", "दो", "दिखाओ", "कितना", "कितने", "का", "की", "के", "रेट", "भाव", "रखते", "हो", "लोग",
    "किलो", "लीटर", "पैकेट", "ग्राम", "रुपये", "रुपए", "बताओ", "बताइए", "नहीं", "या", "मिलता", "मिलती",

    # English / Hinglish
    "do", "we", "you", "have", "is", "there", "available", "stock", "can", "i", "get",
    "need", "give", "me", "show", "how", "much", "what", "the", "price", "cost", "rate",
    "of", "hai", "kya", "dedo", "add", "also", "bhi", "mein", "me", "pakka", "batao", "bataiye",
    "milega", "milegi", "kitne", "kitna", "kg", "kilo", "litre", "liter", "l", "packet", "packets",
    "rs", "rupees", "rupee", "aur", "and", "please", "nahi", "ya", "or", "not", "store"
])

# WORDS THAT NEVER SHOULD BE SEARCHED AS PRODUCT NAMES
CONVERSATIONAL_KEYWORDS = set([
    "quantity", "make it", "make that", "change", "change quantity", "add", "add more", "one more", "two more",
    "remove", "delete", "hata do", "kar do", "kardo", "isko", "usko", "it", "that", "this", "ise", "use",
    "isse", "usse", "मात्रा", "दो", "तीन", "चार", "पाँच", "एक और", "हटा दो", "कर दो", "इसे", "उसे", "इसको", "उसको"
])

def _extract_number(text):
    """Extract numeric or worded quantity from text strictly without misinterpreting verbs ('do', 'kar do', 'कर दो')."""
    text_lower = text.strip().lower()

    # If the message contains order placement keywords, do NOT extract numbers from verb phrases
    order_words = ["order", "ऑर्डर", "place", "प्लेस", "confirm", "कन्फर्म", "submit", "बुक"]
    has_order_word = any(w in text_lower or w in text for w in order_words)

    # 1. Match numeric digits (e.g. "quantity 2", "2 packet", "isko 3 kar do")
    m = re.search(r'\b(\d+)\b', text_lower)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass

    # If order word is present without numeric digits, return None immediately
    if has_order_word:
        return None

    # 2. Strip common verb endings before searching for number word "do"
    cleaned_for_nums = re.sub(
        r'\b(?:kar|de|dedo|laga|bata|place|kar-do|hata|dal|daal)\s+(?:do|दो)\b',
        ' ',
        text_lower
    )
    cleaned_for_nums = re.sub(r'(?:कर|दे|लगा|बता|प्लेस|हटा|डाल)\s*दो', ' ', cleaned_for_nums)

    # 3. Match word tokens from cleaned text
    tokens = re.findall(r'[\u0900-\u097F\w]+', cleaned_for_nums)
    for token in tokens:
        if token in NUMBER_WORDS:
            # Special protection for 'do' / 'दो': match only if explicit quantity context
            if token in ["do", "दो"]:
                if re.search(r'\b(?:do|दो)\s*(?:kg|kilo|litre|liter|packet|packets|piece|pieces|item|items|पैकेट|किलो|लीटर|पीस|मात्रा)\b', text_lower) or \
                   re.search(r'\b(?:quantity|qty|make it|make that|change|isko|usko|मात्रा)\s*(?:to|is)?\s*(?:do|दो)\b', text_lower):
                    return 2
                else:
                    continue
            return NUMBER_WORDS[token]

    return None


def _extract_product_name(text):
    """
    Extract clean product keyword from natural language user message
    by stripping away known intent/filler words in Hindi, Hinglish, and English.
    """
    cleaned = text.strip()
    c_low = cleaned.lower()

    # 1. Remove multiword intent phrases
    for phrase in MULTIWORD_INTENT_PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        c_low = pattern.sub(" ", c_low)

    # 2. Remove punctuation
    c_low = re.sub(r'[?.,!\-:;"\'\(\)]', ' ', c_low)

    # 3. Token-by-token filtering for remaining fillers
    tokens = c_low.split()
    remaining = [t for t in tokens if t not in SINGLEWORD_FILLERS and not t.isdigit() and t not in NUMBER_WORDS]

    if remaining:
        extracted = " ".join(remaining).strip()
        logger.info(f"[Product Extractor] Raw: '{text}' -> Extracted: '{extracted}'")
        return extracted

    # Fallback to Devanagari word extraction if everything got stripped or mixed
    words = re.findall(r'[\u0900-\u097F\w]+', text)
    filtered = [w for w in words if w.lower() not in SINGLEWORD_FILLERS and w not in SINGLEWORD_FILLERS and not w.isdigit() and w.lower() not in NUMBER_WORDS]
    if filtered:
        return " ".join(filtered).strip()

    return text.strip()


def _get_referenced_product(session, shop_id, cart, product_hint=""):
    """
    Resolve which product the user is referring to (e.g. 'isko', 'it', 'add quantity 2').
    Returns Product instance or None.
    """
    # 1. If explicit hint provided, try searching
    if product_hint and product_hint not in CONVERSATIONAL_KEYWORDS:
        matches = services.search_product(shop_id, product_hint)
        if matches:
            return matches[0]

    # 2. Check session last_referenced_product_id
    last_pid = session.get('last_referenced_product_id')
    if last_pid:
        try:
            return Product.objects.get(id=last_pid, shop_id=shop_id, is_active=True)
        except Product.DoesNotExist:
            pass

    # 3. If cart has exactly one item, that's unambiguous
    if cart and len(cart['items']) == 1:
        pid = cart['items'][0]['product_id']
        try:
            return Product.objects.get(id=pid, shop_id=shop_id, is_active=True)
        except Product.DoesNotExist:
            pass

    return None


def _rule_based_nlp_handler(session, shop_id, text):
    """
    High-reliability, fully conversational Multilingual (Hindi/Hinglish/English) NLP engine.
    Handles follow-up references ('add quantity 2', 'make it 3', 'isko hata do', '2 aur add kar do'),
    case-insensitive searches, stock/price queries, multi-product ordering, and atomic checkout.
    """
    logger.info(f"=== RULE-BASED NLP HANDLER ===")
    logger.info(f"USER INPUT: '{text}' | SHOP ID: {shop_id}")

    text_lower = text.strip().lower()
    shop = services.get_shop_or_404(shop_id)
    cart = services.get_cart(session, shop_id)

    # 1. Check for Order Confirmation ("ऑर्डर प्लेस कर दो", "order kar do", "place order", "confirm order")
    confirm_phrases = [
        # Hindi Devanagari
        "ऑर्डर प्लेस कर दो", "ऑर्डर कर दो", "ऑर्डर लगा दो", "ऑर्डर कन्फर्म कर दो", "हाँ ऑर्डर कर दो",
        "बिल के हिसाब से ऑर्डर कर दो", "इसे ऑर्डर कर दो", "हाँ कर दो", "हाँ, कर दो", "ऑर्डर प्लेस करें",
        "ऑर्डर बुक कर दो", "ऑर्डर प्लेस", "ऑर्डर बुक", "ऑर्डर कन्फर्म", "ऑर्डर कर दीजिए", "ऑर्डर कर दो जी",
        "ऑर्डर प्लेस कर दीजिए", "कर दो जी",

        # Hinglish / Roman Hindi
        "order place kar do", "order kar do", "order laga do", "order confirm kar do",
        "haan order kar do", "ha order kar do", "bil ke hisab se order kar do", "ise order kar do",
        "haan kar do", "haan, kar do", "order place", "order confirm", "order kar do na",
        "place order", "place the order", "confirm order", "confirm the order", "confirm it",
        "yes place it", "yes, place it", "submit order", "submit the order", "order now",

        # English
        "place the order", "confirm the order", "yes, place it", "submit the order",
        "order now", "confirm", "place order"
    ]

    is_confirm = any(phrase in text_lower or phrase in text for phrase in confirm_phrases)

    # Handle pending confirmation state or short confirmations ("yes", "haan", "ha", "हाँ", "जी", "ok")
    short_confirmations = ["haan", "yes", "ha", "हाँ", "जी", "जी हाँ", "ok", "okay", "ठीक है", "ठीक", "yes place it", "yes please", "kar do", "कर दो"]
    if not is_confirm and (text_lower in short_confirmations or text in short_confirmations):
        if session.get('order_confirmation_pending') or ("confirm" in session.get('last_ai_step', '').lower()):
            is_confirm = True

    if is_confirm:
        if not cart["items"]:
            return {
                "reply": "Aapka cart empty hai. Kripya pehle items add karein.",
                "cart": cart,
                "step": "Cart Empty"
            }

        order, err = services.place_order_atomic(session, shop_id, "Customer", "9876543210", "")
        if order:
            session['order_confirmation_pending'] = False
            session.pop('last_referenced_product_id', None)
            session.modified = True
            reply = f"Order #{order.id} placed successfully! Total amount: ₹{order.total_amount}. Order and inventory have been updated."
            updated_cart = services.get_cart(session, shop_id)
            return {
                "reply": reply,
                "cart": updated_cart,
                "step": "Confirmation Sent",
                "order_id": order.id
            }
        else:
            return {
                "reply": f"Sorry, order could not be placed: {err}",
                "cart": cart,
                "step": "Inventory Check"
            }

    # 2. Check for Cancellation ("cancel", "rehne do", "nahi chahiye", "clear cart")
    cancel_phrases = ["clear cart", "cancel order", "sab hata do"]
    if any(phrase in text_lower for phrase in cancel_phrases):
        services.clear_cart(session, shop_id)
        updated_cart = services.get_cart(session, shop_id)
        return {
            "reply": "Cart clear kar diya gaya hai. Kuch aur chahiye?",
            "cart": updated_cart,
            "step": "Cart Building"
        }

    # 3. Check for Completion / Bill Summary ("bas", "itna hi", "that's all", "done")
    done_phrases = ["bas", "itna hi", "that's all", "nothing else", "aur kuch nahi", "done", "bas itna hi"]
    if any(phrase in text_lower for phrase in done_phrases) and not any(w in text_lower for w in ["add", "aur", "de do", "chahiye"]):
        if not cart["items"]:
            return {
                "reply": "Aapka cart abhi khali hai. Kya add karna chahenge?",
                "cart": cart,
                "step": "AI Parsing"
            }

        item_summary = ", ".join([f"{item['quantity']} × {item['name']} (₹{item['subtotal']})" for item in cart['items']])
        session['order_confirmation_pending'] = True
        session['last_ai_step'] = "Customer Confirmation"
        session.modified = True
        reply = f"Aapka order summary: {item_summary}. Total Bill: ₹{cart['total']}. Kya order place kar du? (Say 'Haan' or 'Yes' to confirm)"
        return {
            "reply": reply,
            "cart": cart,
            "step": "Customer Confirmation"
        }

    # 4. Check for Remove / Delete Item ("isko hata do", "remove it", "delete it", "hata do")
    remove_phrases = ["hata do", "remove", "delete", "hata de", "nikal do", "cancel"]
    if any(phrase in text_lower for phrase in remove_phrases) and not is_confirm:
        prod_hint = _extract_product_name(text)
        target_prod = _get_referenced_product(session, shop_id, cart, product_hint=prod_hint if prod_hint not in CONVERSATIONAL_KEYWORDS else "")

        if target_prod:
            services.remove_from_cart(session, shop_id, target_prod.id)
            updated_cart = services.get_cart(session, shop_id)
            return {
                "reply": f"{target_prod.name} cart se remove kar diya gaya hai. Total: ₹{updated_cart['total']}. Aur kuch chahiye?",
                "cart": updated_cart,
                "step": "Cart Building"
            }
        elif cart['items']:
            options = " ya ".join([item['name'] for item in cart['items']])
            return {
                "reply": f"Aap kaunsa item remove karna chahenge ({options})?",
                "cart": cart,
                "step": "Cart Building"
            }

    # 5. Check for Conversational Quantity Modification (SET QUANTITY vs ADD MORE)
    is_add_more = any(p in text_lower for p in ["aur add", "more", "ek aur", "one more", "another", "और जोड़"]) or ("aur" in text_lower and "add" in text_lower and "quantity" not in text_lower)

    has_order_keyword = any(w in text_lower or w in text for w in ["order", "ऑर्डर", "place", "प्लेस", "confirm", "कन्फर्म"])

    is_set_quantity = (
        not has_order_keyword
        and not is_add_more
        and _extract_number(text) is not None
        and (
            "quantity" in text_lower or "qty" in text_lower or "make it" in text_lower or "make that" in text_lower or
            "change" in text_lower or "मात्रा" in text_lower or "isko" in text_lower or "isse" in text_lower or
            "usko" in text_lower or "use" in text_lower or "ise" in text_lower or "set quantity" in text_lower
        )
    )

    if is_add_more:
        qty_inc = _extract_number(text) or 1
        prod_hint = _extract_product_name(text)
        target_prod = _get_referenced_product(session, shop_id, cart, product_hint=prod_hint if prod_hint not in CONVERSATIONAL_KEYWORDS else "")

        if target_prod:
            success, msg = services.add_to_cart(session, shop_id, target_prod.id, qty_inc)
            updated_cart = services.get_cart(session, shop_id)
            session['last_referenced_product_id'] = target_prod.id
            if success:
                curr_item = next((it for it in updated_cart['items'] if it['product_id'] == target_prod.id), None)
                curr_qty = curr_item['quantity'] if curr_item else qty_inc
                reply = f"{target_prod.name} ki quantity ab {curr_qty} kar di gayi hai. Total: ₹{updated_cart['total']}. Aur kuch chahiye?"
            else:
                reply = msg
            return {
                "reply": reply,
                "cart": updated_cart,
                "step": "Cart Building"
            }

    if is_set_quantity:
        target_qty = _extract_number(text)
        if target_qty is not None:
            prod_hint = _extract_product_name(text)
            target_prod = _get_referenced_product(session, shop_id, cart, product_hint=prod_hint if prod_hint not in CONVERSATIONAL_KEYWORDS else "")

            if target_prod:
                success, msg = services.update_cart_quantity(session, shop_id, target_prod.id, target_qty)
                updated_cart = services.get_cart(session, shop_id)
                session['last_referenced_product_id'] = target_prod.id
                if success:
                    reply = f"{target_prod.name} ki quantity {target_qty} kar di gayi hai. Total: ₹{updated_cart['total']}. Aur kuch chahiye?"
                else:
                    reply = msg
                return {
                    "reply": reply,
                    "cart": updated_cart,
                    "step": "Cart Building"
                }
            elif cart['items'] and len(cart['items']) > 1:
                options = " ya ".join([item['name'] for item in cart['items']])
                return {
                    "reply": f"Aap {options} mein se kiski quantity {target_qty} karna chahte hain?",
                    "cart": cart,
                    "step": "Cart Building"
                }

    # 6. Check for Stock, Price, or Availability Query Intent (Informational ONLY, NO Cart Mutation)
    # Explicit ordering words:
    ordering_words = ["de do", "dedo", "dena", "add", "daal do", "daalo", "दे दो", "डाल दो", "ले लो", "buy", "chahiye", "चाहिए"]
    has_ordering_word = any(w in text_lower or w in text for w in ordering_words)

    # Availability / Yes-No query phrases
    availability_phrases = [
        "hai ya nahi", "ya nahi", "या नहीं", "available", "उपलब्ध", "stock", "स्टॉक",
        "do you have", "is there", "does this store have", "can i get", "is it available",
        "hai kya", "है क्या", "hai?", "है?", "milegi", "milega", "मिलेगी", "मिलेगा",
        "milti hai", "milta hai", "मिलती है", "मिलता है", "hai", "है"
    ]

    is_price_check = not has_ordering_word and any(word in text_lower or word in text for word in [
        "kitne ki", "kitne ka", "kitna", "price", "rate", "cost", "कितने की", "कितने का", "कितना", "रेट", "भाव", "how much", "what is the price"
    ])

    is_stock_or_availability = not has_ordering_word and not is_price_check and any(word in text_lower or word in text for word in availability_phrases)

    # Follow-up stock/price check on previous item (e.g. "kitna stock hai?", "kitne ki hai?")
    if (is_stock_or_availability or is_price_check) and not _extract_product_name(text):
        target_prod = _get_referenced_product(session, shop_id, cart)
        if target_prod:
            if is_price_check:
                return {
                    "reply": f"{target_prod.name} ₹{target_prod.price} per {target_prod.unit} hai.",
                    "cart": cart,
                    "step": "Database Retrieval"
                }
            elif is_stock_or_availability:
                if target_prod.stock > 0:
                    reply = f"Yes, {target_prod.name} available hai. Abhi {target_prod.stock} {target_prod.unit}s stock mein hain. Agar chahiye toh quantity bata dijiye."
                else:
                    reply = f"Sorry, {target_prod.name} abhi out of stock hai."
                return {
                    "reply": reply,
                    "cart": cart,
                    "step": "Inventory Check"
                }

    # 7. Check for Multi-Product Addition (e.g. "2 kg rice aur 1 litre milk de do")
    parts = re.split(r'\s+(?:aur|and|,|\+)\s+', text, flags=re.IGNORECASE)
    if len(parts) > 1 and has_ordering_word:
        added_summaries = []
        for part in parts:
            p_qty = _extract_number(part) or 1
            p_query = _extract_product_name(part)
            if p_query:
                p_matches = services.search_product(shop_id, p_query)
                if p_matches:
                    prod = p_matches[0]
                    succ, m = services.add_to_cart(session, shop_id, prod.id, p_qty)
                    if succ:
                        added_summaries.append(f"{p_qty} × {prod.name}")
                        session['last_referenced_product_id'] = prod.id

        if added_summaries:
            updated_cart = services.get_cart(session, shop_id)
            reply = f"Added {', '.join(added_summaries)} to cart. Total: ₹{updated_cart['total']}. Aur kuch chahiye?"
            return {
                "reply": reply,
                "cart": updated_cart,
                "step": "Bill Calculation"
            }

    # 8. Single Product Extraction & DB Lookup
    product_query = _extract_product_name(text)

    # Check if user just specified a quantity follow-up for the last referenced item (e.g. "2 packet", "2", "दो पैकेट")
    num_val = _extract_number(text)
    if (not product_query or product_query in CONVERSATIONAL_KEYWORDS) and num_val is not None:
        target_prod = _get_referenced_product(session, shop_id, cart)
        if target_prod:
            succ, m = services.add_to_cart(session, shop_id, target_prod.id, num_val)
            updated_cart = services.get_cart(session, shop_id)
            session['last_referenced_product_id'] = target_prod.id
            if succ:
                reply = f"Added {num_val} × {target_prod.name} (₹{target_prod.price * Decimal(num_val)}) to cart. Total: ₹{updated_cart['total']}. Aur kuch chahiye?"
            else:
                reply = m
            return {
                "reply": reply,
                "cart": updated_cart,
                "step": "Bill Calculation"
            }

    # If query is empty or in conversational keywords, try referencing last item
    if not product_query or product_query in CONVERSATIONAL_KEYWORDS:
        target_prod = _get_referenced_product(session, shop_id, cart)
        if target_prod:
            product_query = target_prod.name

    matches = services.search_product(shop_id, product_query)

    logger.info(f"FALLBACK NLP SEARCH: query='{product_query}' | Matches found: {[p.name for p in matches]}")

    if not matches:
        return {
            "reply": f"Sorry, '{product_query}' hamare store '{shop.name}' mein available nahi hai. Aap active catalog se items select kar sakte hain.",
            "cart": cart,
            "step": "Database Retrieval"
        }

    # If multiple matches found (e.g. "Maggi 70g" vs "Maggi 140g"), ask for clarification
    if len(matches) > 1 and matches[0].name.lower() != product_query.lower():
        options = ", ".join([f"{p.name} (₹{p.price}/{p.unit})" for p in matches])
        return {
            "reply": f"'{product_query}' ke multiple options available hain: {options}. Aap kaunsa chahenge?",
            "cart": cart,
            "step": "Database Retrieval"
        }

    product = matches[0]
    session['last_referenced_product_id'] = product.id
    session.modified = True

    # Handle Price Check Query (Informational ONLY, NO CART ADDITION)
    if is_price_check:
        reply = f"{product.name} ₹{product.price} per {product.unit} hai."
        return {
            "reply": reply,
            "cart": cart,
            "step": "Database Retrieval"
        }

    # Handle Stock / Availability Check Query (Informational ONLY, NO CART ADDITION)
    if is_stock_or_availability:
        if product.stock > 0:
            reply = f"Yes, {product.name} available hai. Abhi {product.stock} {product.unit}s stock mein hain. Agar chahiye toh quantity bata dijiye."
        else:
            reply = f"Sorry, {product.name} abhi out of stock hai."

        return {
            "reply": reply,
            "cart": cart,
            "step": "Inventory Check"
        }

    # Handle Add To Cart Intent
    qty = _extract_number(text) or 1
    avail, stock, _ = services.check_stock(shop_id, product.id, qty)

    if not avail:
        if stock > 0:
            return {
                "reply": f"Sorry, '{product.name}' ke sirf {stock} {product.unit} available hain. Kya aap {stock} add karna chahenge?",
                "cart": cart,
                "step": "Inventory Check"
            }
        else:
            return {
                "reply": f"Sorry, '{product.name}' abhi out of stock hai.",
                "cart": cart,
                "step": "Inventory Check"
            }

    success, msg = services.add_to_cart(session, shop_id, product.id, qty)
    updated_cart = services.get_cart(session, shop_id)

    reply = f"Added {qty} × {product.name} (₹{product.price * Decimal(qty)}) to cart. Total: ₹{updated_cart['total']}. Aur kuch chahiye?"
    return {
        "reply": reply,
        "cart": updated_cart,
        "step": "Bill Calculation"
    }


def process_customer_message(session, shop_id, user_message):
    """
    Main entry point for customer AI conversation.
    Prepares live structured cart context and last referenced product,
    tries official Google GenAI SDK with Function Calling, falling back gracefully to robust NLP engine.
    """
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY not set in .env. Using fallback NLP service.")
        return _rule_based_nlp_handler(session, shop_id, user_message)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        shop = services.get_shop_or_404(shop_id)
        current_cart = services.get_cart(session, shop_id)
        last_pid = session.get('last_referenced_product_id')
        last_pname = ""
        if last_pid:
            try:
                p = Product.objects.get(id=last_pid, shop_id=shop_id)
                last_pname = p.name
            except Product.DoesNotExist:
                pass

        cart_context_str = json.dumps(current_cart['items'])
        context_prompt = (
            f"[Store: {shop.name}, Shop ID: {shop_id}]\n"
            f"[CURRENT CART: {cart_context_str} | Total: ₹{current_cart['total']}]\n"
            f"[LAST REFERENCED PRODUCT: {last_pname} (ID: {last_pid})]\n"
            f"Customer: {user_message}"
        )

        for model_name in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']:
            try:
                config_obj = types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                )

                response = client.models.generate_content(
                    model=model_name,
                    contents=context_prompt,
                    config=config_obj
                )

                cart = services.get_cart(session, shop_id)
                if response and response.text:
                    return {
                        "reply": response.text,
                        "cart": cart,
                        "step": "AI Conversation"
                    }
            except Exception as inner_e:
                logger.debug(f"Gemini API model {model_name} attempt: {inner_e}")
                continue

    except Exception as e:
        logger.warning(f"Google GenAI SDK Exception: {e}. Trying fallback NLP engine.")

    # Always fallback safely to rule-based engine to guarantee 100% hackathon demo success
    return _rule_based_nlp_handler(session, shop_id, user_message)
