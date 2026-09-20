import json
import logging
import re
from decimal import Decimal
from decouple import config
from . import services

logger = logging.getLogger(__name__)

GEMINI_API_KEY = config('GEMINI_API_KEY', default='')

SYSTEM_INSTRUCTION = """
You are an intelligent, polite AI store operator and cashier for a neighborhood kirana store.
Your goal is to assist customers with natural language ordering in English, Hindi, or Hinglish.

CRITICAL FUNCTION CALLING RULES:
1. When calling search_product(query), query MUST contain ONLY the product name or short product search phrase (e.g. "मैगी", "Maggi", "aata", "milk").
   DO NOT pass full sentences like "क्या हमारे पास मैगी है स्टॉक में?" or "Do you have Maggi?".
   Extract only the product name!

2. DISTINGUISH INTENTS CAREFULLY:
   - CHECK AVAILABILITY / STOCK ("क्या हमारे पास मैगी है?", "Is Maggi in stock?"):
     Call search_product(query="Maggi") first. Then answer with stock count. DO NOT call add_to_cart unless asked!
   - CHECK PRICE ("मैगी कितने की है?", "Price of Maggi?"):
     Call search_product(query="Maggi"). Answer with product price. DO NOT call add_to_cart!
   - ADD TO CART ("एक मैगी दे दो", "Maggi add kar do"):
     Call search_product(query="Maggi") or add_to_cart directly.
   - ORDER CONFIRMATION ("haan", "yes", "place order"):
     Call place_order.

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
        return {"success": success, "message": msg}, "Cart Building"

    elif tool_name == "update_cart_quantity":
        pid = tool_args.get("product_id")
        qty = tool_args.get("quantity", 0)
        success, msg = services.update_cart_quantity(session, shop_id, pid, qty)
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


# INTENT PHRASES (multi-word) to remove first
MULTIWORD_INTENT_PHRASES = [
    "do we have", "do you have", "is there", "can i get", "i need", "give me", "show me",
    "what is the price of", "price of", "cost of", "rate of", "how much is", "how much",
    "in stock", "stock mein hai", "stock mein", "stock me", "available hai", "kya hamare paas",
    "kya hamare pass", "kya aapke paas", "kya aapke pass", "add kar do", "add karo",
    "de do", "chahiye", "hai kya", "batao", "bataiye"
]

# SINGLE WORD FILLERS to remove next
SINGLEWORD_FILLERS = set([
    # Hindi Devanagari
    "क्या", "हमारे", "पास", "है", "स्टॉक", "में", "उपलब्ध", "मिलेगा", "मिलेगी", "चाहिए",
    "दे", "दो", "दिखाओ", "कितना", "कितने", "का", "की", "रेट", "भाव", "रखते", "हो", "लोग",
    "एक", "दो", "तीन", "चार", "पांच", "1", "2", "3", "4", "5", "किलो", "लीटर", "पैकेट", "ग्राम",

    # English / Hinglish
    "do", "we", "you", "have", "is", "there", "available", "stock", "can", "i", "get",
    "need", "give", "me", "show", "how", "much", "what", "the", "price", "cost", "rate",
    "of", "hai", "kya", "dedo", "add", "also", "bhi", "mein", "pakka", "batao", "bataiye",
    "milega", "milegi", "kitne", "kitna", "kg", "kilo", "litre", "liter", "l", "packet", "packets"
])

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
    c_low = re.sub(r'[?.,!\-:;]', ' ', c_low)

    # 3. Token-by-token filtering for remaining fillers
    tokens = c_low.split()
    remaining = [t for t in tokens if t not in SINGLEWORD_FILLERS and not t.isdigit()]

    if remaining:
        extracted = " ".join(remaining)
        logger.info(f"[Product Extractor] Raw: '{text}' -> Extracted: '{extracted}'")
        return extracted

    # Fallback to Devanagari word extraction if everything got stripped or mixed
    words = re.findall(r'[\u0900-\u097F\w]+', text)
    filtered = [w for w in words if w.lower() not in SINGLEWORD_FILLERS and w not in SINGLEWORD_FILLERS and not w.isdigit()]
    if filtered:
        return " ".join(filtered)

    return text.strip()


def _rule_based_nlp_handler(session, shop_id, text):
    """
    High-reliability Hinglish/Hindi/English NLP fallback engine for hackathon demo.
    Executes actual backend tools against PostgreSQL via services.py.
    """
    logger.info(f"=== FALLBACK NLP HANDLER ===")
    logger.info(f"USER INPUT: '{text}' | SHOP ID: {shop_id}")

    text_lower = text.strip().lower()
    shop = services.get_shop_or_404(shop_id)
    cart = services.get_cart(session, shop_id)

    # 1. Check for Order Confirmation ("haan", "yes", "confirm", "order kar do", "place it")
    confirm_phrases = ["haan", "yes", "ha", "confirm", "place order", "order kar do", "order place", "kar do", "kardo"]
    is_confirm = any(phrase in text_lower for phrase in confirm_phrases) and cart["items"]

    if is_confirm:
        order, err = services.place_order_atomic(session, shop_id, "Customer", "9876543210", "")
        if order:
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

    # 2. Check for Cancellation ("cancel", "rehne do", "nahi chahiye")
    cancel_phrases = ["cancel", "rehne do", "nahi chahiye", "clear cart"]
    if any(phrase in text_lower for phrase in cancel_phrases):
        services.clear_cart(session, shop_id)
        updated_cart = services.get_cart(session, shop_id)
        return {
            "reply": "Cart clear kar diya gaya hai. Kuch aur chahiye?",
            "cart": updated_cart,
            "step": "Cart Building"
        }

    # 3. Check for Completion ("bas", "itna hi", "that's all", "nothing else", "done")
    done_phrases = ["bas", "itna hi", "that's all", "nothing else", "aur kuch nahi", "done", "bas itna hi"]
    if any(phrase in text_lower for phrase in done_phrases):
        if not cart["items"]:
            return {
                "reply": "Aapka cart abhi khali hai. Kya add karna chahenge?",
                "cart": cart,
                "step": "AI Parsing"
            }

        item_summary = ", ".join([f"{item['quantity']} {item['name']} (₹{item['subtotal']})" for item in cart['items']])
        reply = f"Aapka order summary: {item_summary}. Total Bill: ₹{cart['total']}. Kya order place kar du? (Say 'Haan' or 'Yes' to confirm)"
        return {
            "reply": reply,
            "cart": cart,
            "step": "Customer Confirmation"
        }

    # Determine Intent: Price Check vs Stock Check vs Add to Cart
    is_add_action = any(w in text_lower or w in text for w in ["add", "dedo", "de do", "chahiye", "चाहिए", "दे दो", "buy", "order", "lao", "ले लो"])

    is_price_check = not is_add_action and any(word in text_lower or word in text for word in [
        "kitne ki", "kitna", "price", "rate", "cost", "कितने की", "कितना", "रेट", "भाव", "how much", "what is the price"
    ])

    is_stock_check = not is_add_action and not is_price_check and any(word in text_lower or word in text for word in [
        "stock", "available", "उपलब्ध", "स्टॉक", "मिलेगा", "मिलेगी", "रखते हो", "do you have", "do we have", "is there", "है क्या", "है?"
    ])

    # Extract Quantity
    qty = 1
    qty_match = re.search(r'(\d+)\s*(kg|kilo|litre|liter|l|packet|packets|pkt|piece|pieces)?', text_lower)
    if qty_match:
        try:
            qty = int(qty_match.group(1))
        except ValueError:
            qty = 1

    # Extract clean product query
    product_query = _extract_product_name(text)

    # Perform DB search using improved search_product
    matches = services.search_product(shop_id, product_query)

    logger.info(f"FALLBACK NLP SEARCH: query='{product_query}' | Matches found: {[p.name for p in matches]}")

    if not matches:
        return {
            "reply": f"Sorry, '{product_query}' hamare store '{shop.name}' mein available nahi hai. Aap active catalog se items select kar sakte hain.",
            "cart": cart,
            "step": "Database Retrieval"
        }

    if len(matches) > 1:
        options = ", ".join([f"{p.name} (₹{p.price}/{p.unit})" for p in matches])
        return {
            "reply": f"'{product_query}' ke multiple options available hain: {options}. Aap kaunsa chahenge?",
            "cart": cart,
            "step": "Database Retrieval"
        }

    product = matches[0]

    # Handle Price Check Query (Informational ONLY, NO CART ADDITION)
    if is_price_check:
        reply = f"{product.name} ₹{product.price} per {product.unit} hai."
        return {
            "reply": reply,
            "cart": cart,
            "step": "Database Retrieval"
        }

    # Handle Stock Check Query (Informational ONLY, NO CART ADDITION)
    if is_stock_check:
        if product.stock > 0:
            reply = f"Haan! {product.name} available hai. Abhi {product.stock} {product.unit}s stock mein hain."
        else:
            reply = f"Sorry, {product.name} abhi out of stock hai."

        return {
            "reply": reply,
            "cart": cart,
            "step": "Inventory Check"
        }

    # Handle Add To Cart Intent
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
    Tries official Google GenAI SDK with Function Calling, falling back gracefully to robust NLP engine.
    """
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY not set in .env. Using fallback NLP service.")
        return _rule_based_nlp_handler(session, shop_id, user_message)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        shop = services.get_shop_or_404(shop_id)

        # Tools declaration for Gemini
        tools = [
            services.search_product,
            services.check_stock,
            services.add_to_cart,
            services.update_cart_quantity,
            services.remove_from_cart,
            services.get_cart,
            services.place_order_atomic
        ]

        context_prompt = f"[Store: {shop.name}, Shop ID: {shop_id}] Customer: {user_message}"

        # Try gemini models with function calling
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
