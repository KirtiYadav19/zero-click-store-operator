import json
import io
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from .models import ShopkeeperProfile, Shop, Product, Order, OrderItem
from .forms import ProductForm
from . import services
from . import gemini_service
from . import sarvam_service

logger = logging.getLogger(__name__)

def _get_user_shop(user):
    profile = getattr(user, 'profile', None)
    if not profile:
        return None
    return profile.shops.first()

# --- SHOPKEEPER VIEWS ---

def shopkeeper_register(request):
    if request.user.is_authenticated:
        shop = _get_user_shop(request.user)
        if shop:
            return redirect('shopkeeper_dashboard')
        return redirect('shopkeeper_create_shop')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')
        form_data = {'username': username, 'email': email, 'phone': phone}

        if not username or not password or not confirm_password or not phone:
            messages.error(request, "All required fields (Username, Phone, Password, Confirm Password) must be filled.")
            return render(request, 'shopkeeper/register.html', {'form_data': form_data})

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'shopkeeper/register.html', {'form_data': form_data})

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"Username '{username}' is already taken. If you already have an account, please login below.")
            return render(request, 'shopkeeper/register.html', {'form_data': form_data})

        try:
            user = User.objects.create_user(username=username, email=email, password=password)
            ShopkeeperProfile.objects.create(user=user, phone=phone)
            login(request, user)
            messages.success(request, "Registration successful! Please create your shop.")
            return redirect('shopkeeper_create_shop')
        except Exception as e:
            messages.error(request, f"Registration failed: {str(e)}")
            return render(request, 'shopkeeper/register.html', {'form_data': form_data})

    return render(request, 'shopkeeper/register.html')


def shopkeeper_login(request):
    if request.user.is_authenticated:
        shop = _get_user_shop(request.user)
        if shop:
            return redirect('shopkeeper_dashboard')
        return redirect('shopkeeper_create_shop')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            shop = _get_user_shop(user)
            if shop:
                return redirect('shopkeeper_dashboard')
            else:
                return redirect('shopkeeper_create_shop')
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, 'shopkeeper/login.html')


def shopkeeper_logout(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('shopkeeper_login')


@login_required(login_url='shopkeeper_login')
def shopkeeper_create_shop(request):
    profile, _ = ShopkeeperProfile.objects.get_or_create(user=request.user)
    
    if profile.shops.exists():
        messages.info(request, "You already have a shop registered.")
        return redirect('shopkeeper_dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()

        if not name:
            messages.error(request, "Shop name is required.")
            return render(request, 'shopkeeper/create_shop.html')

        Shop.objects.create(
            shopkeeper=profile,
            name=name,
            address=address,
            phone=phone
        )
        messages.success(request, "Shop created successfully!")
        return redirect('shopkeeper_dashboard')

    return render(request, 'shopkeeper/create_shop.html')


@login_required(login_url='shopkeeper_login')
def shopkeeper_dashboard(request):
    shop = _get_user_shop(request.user)

    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    product_count = shop.products.count()
    order_count = shop.orders.count()
    recent_orders = shop.orders.all()[:5]

    context = {
        'shop': shop,
        'product_count': product_count,
        'order_count': order_count,
        'recent_orders': recent_orders,
    }
    return render(request, 'shopkeeper/dashboard.html', context)


@login_required(login_url='shopkeeper_login')
def shopkeeper_orders(request):
    shop = _get_user_shop(request.user)
    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    orders = Order.objects.filter(shop=shop).order_by('-created_at')
    context = {
        'shop': shop,
        'orders': orders,
    }
    return render(request, 'shopkeeper/orders/list.html', context)


@login_required(login_url='shopkeeper_login')
def shopkeeper_products(request):
    shop = _get_user_shop(request.user)
    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    products = Product.objects.filter(shop=shop).order_by('name')
    context = {
        'shop': shop,
        'products': products,
    }
    return render(request, 'shopkeeper/products/list.html', context)


@login_required(login_url='shopkeeper_login')
def shopkeeper_product_add(request):
    shop = _get_user_shop(request.user)
    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.shop = shop
            product.save()
            messages.success(request, f"Product '{product.name}' added successfully.")
            return redirect('shopkeeper_products')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm()

    context = {
        'shop': shop,
        'form': form,
        'title': 'Add New Product',
        'is_edit': False,
    }
    return render(request, 'shopkeeper/products/form.html', context)


@login_required(login_url='shopkeeper_login')
def shopkeeper_product_edit(request, pk):
    shop = _get_user_shop(request.user)
    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    product = get_object_or_404(Product, pk=pk, shop=shop)

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            updated_product = form.save(commit=False)
            updated_product.shop = shop
            updated_product.save()
            messages.success(request, f"Product '{updated_product.name}' updated successfully.")
            return redirect('shopkeeper_products')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm(instance=product)

    context = {
        'shop': shop,
        'product': product,
        'form': form,
        'title': f"Edit {product.name}",
        'is_edit': True,
    }
    return render(request, 'shopkeeper/products/form.html', context)


# --- CUSTOMER VIEWS ---

def customer_home(request):
    """Customer home page listing active shops."""
    shops = services.get_active_shops()
    return render(request, 'customer/home.html', {'shops': shops})


def customer_shop_order(request, shop_id):
    """Customer ordering page for a specific active shop."""
    shop = services.get_shop_or_404(shop_id)
    products = Product.objects.filter(shop=shop, is_active=True).order_by('name')
    cart = services.get_cart(request.session, shop_id)

    context = {
        'shop': shop,
        'products': products,
        'cart': cart,
    }
    return render(request, 'customer/shop_order.html', context)


def customer_cart_add(request, shop_id):
    """POST endpoint to add a product to session cart."""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        try:
            quantity = int(request.POST.get('quantity', 1))
        except ValueError:
            quantity = 1

        success, msg = services.add_to_cart(request.session, shop_id, product_id, quantity)
        if success:
            messages.success(request, msg)
        else:
            messages.error(request, msg)

    return redirect('customer_shop_order', shop_id=shop_id)


def customer_cart_update(request, shop_id):
    """POST endpoint to update or remove item in session cart."""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        try:
            quantity = int(request.POST.get('quantity', 0))
        except ValueError:
            quantity = 0

        success, msg = services.update_cart_quantity(request.session, shop_id, product_id, quantity)
        if success:
            messages.success(request, msg)
        else:
            messages.error(request, msg)

    return redirect('customer_shop_order', shop_id=shop_id)


def customer_cart_clear(request, shop_id):
    """POST endpoint to clear cart for a shop."""
    if request.method == 'POST':
        services.clear_cart(request.session, shop_id)
        messages.info(request, "Cart cleared.")

    return redirect('customer_shop_order', shop_id=shop_id)


def customer_checkout(request, shop_id):
    """Checkout page displaying order review and customer info form."""
    shop = services.get_shop_or_404(shop_id)
    cart = services.get_cart(request.session, shop_id)

    if not cart['items']:
        messages.error(request, "Your cart is empty. Please add items before checking out.")
        return redirect('customer_shop_order', shop_id=shop_id)

    if request.method == 'POST':
        customer_name = request.POST.get('name', '').strip()
        customer_phone = request.POST.get('phone', '').strip()
        customer_address = request.POST.get('address', '').strip()

        try:
            order, err = services.place_order_atomic(
                request.session,
                shop_id,
                customer_name,
                customer_phone,
                customer_address
            )
            if order:
                return redirect('customer_order_success', order_id=order.id)
            else:
                messages.error(request, err or "Failed to place order.")
        except Exception as e:
            messages.error(request, f"Order placement failed: {str(e)}")

    context = {
        'shop': shop,
        'cart': cart,
    }
    return render(request, 'customer/checkout.html', context)


def customer_order_success(request, order_id):
    """Order confirmation success page."""
    order = get_object_or_404(Order, id=order_id)
    items = order.items.all().select_related('product')
    context = {
        'order': order,
        'items': items,
    }
    return render(request, 'customer/order_success.html', context)


def customer_ai_chat(request, shop_id):
    """JSON AJAX Endpoint for Gemini AI Customer Chat."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)

    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
            message = data.get('message', '').strip()
        else:
            message = request.POST.get('message', '').strip()
    except Exception:
        message = request.POST.get('message', '').strip()

    if not message:
        return JsonResponse({'error': 'Empty message'}, status=400)

    # Security: Server validates shop_id against PostgreSQL
    shop = services.get_shop_or_404(shop_id)

    # Process through Gemini AI / NLP engine
    result = gemini_service.process_customer_message(request.session, shop.id, message)
    return JsonResponse(result)


def customer_voice_input(request, shop_id):
    """JSON AJAX Endpoint for Sarvam STT -> Gemini AI -> Sarvam TTS pipeline."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': 'No audio file uploaded'}, status=400)

    shop = services.get_shop_or_404(shop_id)

    # 1. Sarvam Speech-to-Text
    audio_bytes = audio_file.read()
    transcript, stt_err = sarvam_service.speech_to_text(audio_bytes, audio_file.name)

    if not transcript:
        return JsonResponse({
            'error': stt_err or 'No speech detected. Please try again or use text chat.',
            'transcript': ''
        }, status=400)

    # 2. Re-use 100% existing Gemini + Django tool pipeline
    result = gemini_service.process_customer_message(request.session, shop.id, transcript)

    # 3. Sarvam Text-to-Speech audio generation
    audio_uri = sarvam_service.text_to_speech(result.get('reply', ''))

    # Return unified response
    response_data = {
        'transcript': transcript,
        'reply': result.get('reply', ''),
        'audio': audio_uri,
        'cart': result.get('cart'),
        'step': result.get('step', 'Voice Input'),
        'order_id': result.get('order_id')
    }
    return JsonResponse(response_data)
