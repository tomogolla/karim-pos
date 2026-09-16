from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from . import views
urlpatterns = [
    path('api/customers/', views.customer_search_api, name='customer_search_api'),
    path('api/customers/<int:pk>/', views.customer_detail_api, name='customer_detail_api'),
    path('api/customers/<int:pk>/refresh/', views.customer_refresh_api, name='customer_refresh_api'),
    path('bills/<int:pk>/customer/select/', views.select_customer, name='select_customer'),
    path('bills/<int:pk>/customer/clear/', views.clear_customer, name='clear_customer'),
    path('',views.dashboard,name='dashboard'),
    path('login/',LoginView.as_view(template_name='pos/login.html'),name='login'),
    path('logout/',LogoutView.as_view(),name='logout'), path('demo-login/',views.demo_login,name='demo_login'),
    path('bills/new/',views.new_bill,name='new_bill'), path('bills/',views.bills,name='bills'),
    path('bills/<int:pk>/',views.bill,name='bill'), path('bills/<int:pk>/add/',views.add_line,name='add_line'),
    path('lines/<int:pk>/',views.update_line,name='update_line'), path('bills/<int:pk>/checkout/',views.checkout,name='checkout'),
    path('bills/<int:pk>/receipt/',views.receipt,name='receipt'), path('bills/<int:pk>/void/',views.void_bill,name='void_bill'),
    path('orders/',views.orders,name='orders'), path('orders/<int:pk>/retry/',views.retry,name='retry'), path('orders/<int:pk>/payload/',views.payload,name='payload'),
    path('catalog/',views.catalog,name='catalog'), path('catalog/import/',views.import_catalog,name='import_catalog'),
    path('staff/',views.staff,name='staff'), path('reports/',views.reports,name='reports'), path('reports/export/',views.export,name='export'),
    path('settings/',views.settings_page,name='settings'),
]
