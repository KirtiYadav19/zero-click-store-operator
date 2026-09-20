from django.urls import path
from . import views

urlpatterns = [
    # Customer routes
    path('', views.customer_home, name='customer_home'),
    path('shop/<int:shop_id>/order/', views.customer_shop_order, name='customer_shop_order'),
    path('shop/<int:shop_id>/ai-chat/', views.customer_ai_chat, name='customer_ai_chat'),
    path('shop/<int:shop_id>/voice-input/', views.customer_voice_input, name='customer_voice_input'),
    path('shop/<int:shop_id>/cart/add/', views.customer_cart_add, name='customer_cart_add'),
    path('shop/<int:shop_id>/cart/update/', views.customer_cart_update, name='customer_cart_update'),
    path('shop/<int:shop_id>/cart/clear/', views.customer_cart_clear, name='customer_cart_clear'),
    path('shop/<int:shop_id>/checkout/', views.customer_checkout, name='customer_checkout'),
    path('order/<int:order_id>/success/', views.customer_order_success, name='customer_order_success'),

    # Shopkeeper routes
    path('shopkeeper/register/', views.shopkeeper_register, name='shopkeeper_register'),
    path('shopkeeper/login/', views.shopkeeper_login, name='shopkeeper_login'),
    path('shopkeeper/logout/', views.shopkeeper_logout, name='shopkeeper_logout'),
    path('shopkeeper/create-shop/', views.shopkeeper_create_shop, name='shopkeeper_create_shop'),
    path('shopkeeper/dashboard/', views.shopkeeper_dashboard, name='shopkeeper_dashboard'),
    path('shopkeeper/orders/', views.shopkeeper_orders, name='shopkeeper_orders'),
    path('shopkeeper/products/', views.shopkeeper_products, name='shopkeeper_products'),
    path('shopkeeper/products/add/', views.shopkeeper_product_add, name='shopkeeper_product_add'),
    path('shopkeeper/products/<int:pk>/edit/', views.shopkeeper_product_edit, name='shopkeeper_product_edit'),
]
