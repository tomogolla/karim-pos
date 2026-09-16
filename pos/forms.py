from django import forms
from .models import Bill, Line, Staff
class CustomerForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.shopify_customer_id:
            for field in ['first_name', 'last_name', 'email', 'phone']:
                self.fields[field].disabled = True
                self.fields[field].required = False

    class Meta:
        model = Bill
        fields = ['first_name', 'last_name', 'phone', 'email', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}

class ShopifyCustomerForm(forms.Form):
    first_name = forms.CharField(max_length=100, label='First name')
    last_name = forms.CharField(max_length=100, required=False, label='Last name')
    email = forms.EmailField(required=False)
    phone = forms.RegexField(regex=r'^\+[1-9]\d{6,14}$', required=False,
                            help_text='International format, for example +254712345678.',
                            error_messages={'invalid': 'Use an international phone number starting with + and country code.'})

    def clean(self):
        data = super().clean()
        if not data.get('email') and not data.get('phone'):
            raise forms.ValidationError('Add an email or phone number so this customer can be found again.')
        return data
class LineForm(forms.ModelForm):
    class Meta:
        model = Line
        fields = ['quantity', 'price', 'discount', 'staff']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auto_id = f'id_line_{self.instance.pk}_%s'
        self.fields['staff'].queryset = Staff.objects.filter(active=True)
        self.fields['quantity'].min_value = 1
class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=[('cash','Cash'), ('mpesa','M-Pesa'), ('card','Card')])
    tendered = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, label='Amount received')
    reference = forms.CharField(max_length=120, required=False, label='Payment reference')
    confirmed = forms.BooleanField(label='I have confirmed receipt of this payment.')
    def clean(self):
        data = super().clean()
        if data.get('method') != 'cash' and not data.get('reference', '').strip(): self.add_error('reference', 'A payment reference is required for M-Pesa and card.')
        return data
class StaffForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ['name', 'role', 'active']
