from django.contrib import admin
from .models import Staff, Product, Bill, Line, Payment, SyncJob, Audit, Customer, CustomerCreation
admin.site.register(Staff)
@admin.register(Product, Bill, Line, Payment, SyncJob, Audit, Customer, CustomerCreation)
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
