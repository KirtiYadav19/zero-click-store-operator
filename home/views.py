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


from decouple import config

@login_required(login_url='shopkeeper_login')
def shopkeeper_create_shop(request):
    profile, _ = ShopkeeperProfile.objects.get_or_create(user=request.user)

    if profile.shops.exists():
        messages.info(request, "You already have a shop registered.")
        return redirect('shopkeeper_dashboard')

    google_maps_api_key = config('GOOGLE_MAPS_API_KEY', default='')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        lat_str = request.POST.get('latitude', '').strip()
        lng_str = request.POST.get('longitude', '').strip()

        if not name:
            messages.error(request, "Shop name is required.")
            return render(request, 'shopkeeper/create_shop.html', {'google_maps_api_key': google_maps_api_key})

        lat = None
        lng = None
        if lat_str and lng_str:
            try:
                lat_val = float(lat_str)
                lng_val = float(lng_str)
                if -90 <= lat_val <= 90 and -180 <= lng_val <= 180:
                    lat = lat_val
                    lng = lng_val
                else:
                    messages.error(request, "Latitude must be between -90 and 90, Longitude between -180 and 180.")
                    return render(request, 'shopkeeper/create_shop.html', {'google_maps_api_key': google_maps_api_key})
            except ValueError:
                messages.error(request, "Coordinates must be valid numbers.")
                return render(request, 'shopkeeper/create_shop.html', {'google_maps_api_key': google_maps_api_key})

        Shop.objects.create(
            shopkeeper=profile,
            name=name,
            address=address,
            phone=phone,
            latitude=lat,
            longitude=lng
        )
        messages.success(request, "Shop created successfully!")
        return redirect('shopkeeper_dashboard')

    return render(request, 'shopkeeper/create_shop.html', {'google_maps_api_key': google_maps_api_key})


@login_required(login_url='shopkeeper_login')
def shopkeeper_edit_shop(request):
    shop = _get_user_shop(request.user)
    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    google_maps_api_key = config('GOOGLE_MAPS_API_KEY', default='')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        lat_str = request.POST.get('latitude', '').strip()
        lng_str = request.POST.get('longitude', '').strip()

        if not name:
            messages.error(request, "Shop name is required.")
            return render(request, 'shopkeeper/edit_shop.html', {'shop': shop, 'google_maps_api_key': google_maps_api_key})

        lat = None
        lng = None
        if lat_str and lng_str:
            try:
                lat_val = float(lat_str)
                lng_val = float(lng_str)
                if -90 <= lat_val <= 90 and -180 <= lng_val <= 180:
                    lat = lat_val
                    lng = lng_val
                else:
                    messages.error(request, "Latitude must be between -90 and 90, Longitude between -180 and 180.")
                    return render(request, 'shopkeeper/edit_shop.html', {'shop': shop, 'google_maps_api_key': google_maps_api_key})
            except ValueError:
                messages.error(request, "Coordinates must be valid numbers.")
                return render(request, 'shopkeeper/edit_shop.html', {'shop': shop, 'google_maps_api_key': google_maps_api_key})

        shop.name = name
        shop.address = address
        shop.phone = phone
        shop.latitude = lat
        shop.longitude = lng
        shop.save()

        messages.success(request, "Shop details and location updated successfully!")
        return redirect('shopkeeper_dashboard')

    context = {
        'shop': shop,
        'google_maps_api_key': google_maps_api_key,
    }
    return render(request, 'shopkeeper/edit_shop.html', context)


@login_required(login_url='shopkeeper_login')
def shopkeeper_dashboard(request):
    shop = _get_user_shop(request.user)

    if not shop:
        messages.info(request, "Please create a shop first.")
        return redirect('shopkeeper_create_shop')

    product_count = shop.products.count()
    order_count = shop.orders.count()
    recent_orders = shop.orders.all()[:5]
    google_maps_api_key = config('GOOGLE_MAPS_API_KEY', default='')

    context = {
        'shop': shop,
        'product_count': product_count,
        'order_count': order_count,
        'recent_orders': recent_orders,
        'google_maps_api_key': google_maps_api_key,
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
    """Customer home page listing active shops sorted by distance if location provided."""
    lat = request.GET.get('lat') or request.session.get('customer_lat')
    lng = request.GET.get('lng') or request.session.get('customer_lng')

    if request.GET.get('lat') and request.GET.get('lng'):
        request.session['customer_lat'] = request.GET.get('lat')
        request.session['customer_lng'] = request.GET.get('lng')
        request.session.modified = True

    shops_data = services.get_nearby_active_shops(lat, lng)
    google_maps_api_key = config('GOOGLE_MAPS_API_KEY', default='')

    context = {
        'shops_data': shops_data,
        'customer_lat': lat,
        'customer_lng': lng,
        'google_maps_api_key': google_maps_api_key,
    }
    return render(request, 'customer/home.html', context)


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

    # Store order authorization in session
    request.session['last_order_id'] = order.id
    placed_orders = request.session.get('placed_order_ids', [])
    if order.id not in placed_orders:
        placed_orders.append(order.id)
    request.session['placed_order_ids'] = placed_orders
    request.session.modified = True

    context = {
        'order': order,
        'items': items,
    }
    return render(request, 'customer/order_success.html', context)


def order_receipt_pdf(request, order_id):
    """
    Generate and stream downloadable PDF receipt for a confirmed order.
    Enforces strict authorization: session must match order_id or request.user must be the shopkeeper.
    """
    try:
        order = Order.objects.select_related('shop', 'customer').filter(id=order_id).first()
        if not order:
            return HttpResponse("Order not found.", status=404)

        # Security & Authorization check
        is_authorized = False

        # 1. Shopkeeper of this order's shop
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if profile and profile.shops.filter(id=order.shop.id).exists():
                is_authorized = True

        # 2. Customer session authorization
        if not is_authorized:
            last_order_id = request.session.get('last_order_id')
            placed_order_ids = request.session.get('placed_order_ids', [])
            if last_order_id == order.id or order.id in placed_order_ids:
                is_authorized = True

        if not is_authorized:
            return HttpResponseForbidden("Receipt could not be accessed. You are not authorized to view this receipt.")

        # Build PDF using ReportLab
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        styles = getSampleStyleSheet()

        # Custom styles
        shop_title_style = ParagraphStyle(
            'ShopTitle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=18,
            alignment=1, # Center
            spaceAfter=4,
            textColor=colors.HexColor('#1A2530')
        )

        shop_sub_style = ParagraphStyle(
            'ShopSub',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            alignment=1, # Center
            textColor=colors.HexColor('#555555'),
            spaceAfter=2
        )

        heading2_style = ParagraphStyle(
            'ReceiptHeading',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=14,
            alignment=1, # Center
            textColor=colors.HexColor('#27AE60'),
            spaceBefore=10,
            spaceAfter=10
        )

        normal_style = ParagraphStyle(
            'ReceiptNormal',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            textColor=colors.HexColor('#333333'),
            leading=14
        )

        bold_style = ParagraphStyle(
            'ReceiptBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            textColor=colors.HexColor('#1A2530'),
            leading=14
        )

        story = []

        # 1. Shop Header
        shop = order.shop
        shop_name = shop.name.upper() if shop else "STORE RECEIPT"
        story.append(Paragraph(shop_name, shop_title_style))
        if shop and shop.address:
            story.append(Paragraph(shop.address, shop_sub_style))
        if shop and shop.phone:
            story.append(Paragraph(f"Phone: {shop.phone}", shop_sub_style))

        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#CCCCCC'), spaceAfter=10))

        # 2. Receipt Subtitle & Order Details
        story.append(Paragraph("ORDER RECEIPT", heading2_style))

        order_date_str = order.created_at.strftime('%d %b %Y, %I:%M %p')
        info_data = [
            [
                Paragraph(f"<b>Order ID:</b> #{order.id}", normal_style),
                Paragraph(f"<b>Date:</b> {order_date_str}", normal_style)
            ]
        ]

        customer = order.customer
        cust_name = customer.name if customer and customer.name else "Customer"
        cust_phone = customer.phone if customer and customer.phone else "N/A"
        cust_addr = customer.address if customer and customer.address else ""

        info_data.append([
            Paragraph(f"<b>Customer:</b> {cust_name}", normal_style),
            Paragraph(f"<b>Phone:</b> {cust_phone}", normal_style)
        ])
        if cust_addr:
            info_data.append([
                Paragraph(f"<b>Address:</b> {cust_addr}", normal_style),
                Paragraph("", normal_style)
            ])

        info_table = Table(info_data, colWidths=[270, 270])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('TOPPADDING', (0,0), (-1,-1), 0),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 12))

        # 3. Items Table
        items = order.items.all().select_related('product')

        table_data = [
            [
                Paragraph("<b>ITEM</b>", bold_style),
                Paragraph("<b>QTY</b>", bold_style),
                Paragraph("<b>PRICE</b>", bold_style),
                Paragraph("<b>SUBTOTAL</b>", bold_style)
            ]
        ]

        for item in items:
            prod_name = item.product.name if item.product else "Product"
            table_data.append([
                Paragraph(prod_name, normal_style),
                Paragraph(str(item.quantity), normal_style),
                Paragraph(f"₹{item.unit_price:.2f}", normal_style),
                Paragraph(f"₹{item.subtotal:.2f}", normal_style)
            ])

        items_table = Table(table_data, colWidths=[240, 60, 120, 120])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8F9FA')),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E9ECEF')),
            ('ALIGN', (1,0), (1,-1), 'CENTER'),
            ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 12))

        # 4. Total Amount
        total_data = [
            [
                Paragraph("<b>TOTAL AMOUNT</b>", ParagraphStyle('TLabel', parent=bold_style, fontSize=12)),
                Paragraph(f"<b>₹{order.total_amount:.2f}</b>", ParagraphStyle('TVal', parent=bold_style, fontSize=12, alignment=2, textColor=colors.HexColor('#1A2530')))
            ]
        ]
        total_table = Table(total_data, colWidths=[360, 180])
        total_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#E8F8F5')),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#27AE60')),
        ]))
        story.append(total_table)
        story.append(Spacer(1, 20))

        # 5. Footer / Status
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#CCCCCC'), spaceAfter=15))
        status_style = ParagraphStyle(
            'StatusText',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=12,
            alignment=1,
            textColor=colors.HexColor('#27AE60')
        )
        thanks_style = ParagraphStyle(
            'ThanksText',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            alignment=1,
            textColor=colors.HexColor('#555555'),
            spaceBefore=4
        )
        story.append(Paragraph("✓ ORDER CONFIRMED", status_style))
        story.append(Paragraph("Thank you for shopping with us!", thanks_style))

        # Build document
        doc.build(story)
        pdf_data = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_data, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="order_{order.id}_receipt.pdf"'
        return response

    except Exception as e:
        logger.exception("Error generating receipt PDF for order_id=%s: %s", order_id, str(e))
        return HttpResponse("Receipt could not be generated. Please try again.", status=500)



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
