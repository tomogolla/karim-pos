from django.urls import path
from . import api, views

urlpatterns = [
    path('session/', api.session),
    path('session/login/', api.login_api),
    path('session/demo/', api.demo_login_api),
    path('session/logout/', api.logout_api),
    path('dashboard/', api.dashboard),
    path('bills/', api.bills),
    path('bills/<int:pk>/', api.bill_detail),
    path('bills/<int:pk>/lines/', api.add_line),
    path('bills/<int:pk>/checkout/', api.checkout),
    path('bills/<int:pk>/receipt/', api.receipt),
    path('bills/<int:pk>/customer/select/', api.select_customer),
    path('bills/<int:pk>/customer/', api.bill_customer),
    path('lines/<int:pk>/', api.line_detail),
    path('catalog/', api.catalog),
    path('staff/', api.staff),
    path('orders/', api.orders),
    path('orders/<int:pk>/retry/', api.retry_order),
    path('orders/<int:pk>/payload/', api.order_payload),
    path('reports/', api.reports),
    path('settings/', api.settings_api),
    path('customers/', views.customer_search_api),
    path('customers/<int:pk>/', views.customer_detail_api),
    path('customers/<int:pk>/refresh/', views.customer_refresh_api),
]
