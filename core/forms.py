from django import forms
from django.contrib.auth import get_user_model

from .models import DepotTest, Farmer

User = get_user_model()


class FarmerForm(forms.ModelForm):
    pin = forms.CharField(min_length=4, max_length=8, widget=forms.PasswordInput,
                          help_text="4–8 digit PIN the farmer uses to log in on the phone.")

    class Meta:
        model = Farmer
        fields = ["member_number", "first_name", "last_name", "gender", "phone", "village",
                  "cooperative", "cluster", "joined_on"]
        widgets = {"joined_on": forms.DateInput(attrs={"type": "date"})}

    def clean_member_number(self):
        v = self.cleaned_data["member_number"].strip().upper()
        if User.objects.filter(username__iexact=v).exists():
            raise forms.ValidationError("A login with this member number already exists.")
        return v

    def clean_pin(self):
        v = self.cleaned_data["pin"]
        if not v.isdigit():
            raise forms.ValidationError("PIN must be numbers only.")
        return v

    def clean(self):
        data = super().clean()
        coop, cluster = data.get("cooperative"), data.get("cluster")
        if coop and cluster and cluster.cooperative_id != coop.id:
            self.add_error("cluster", "This cluster is not part of the selected cooperative.")
        return data

    def save(self, commit=True):
        farmer = super().save(commit=False)
        user = User.objects.create_user(username=farmer.member_number,
                                        password=self.cleaned_data["pin"],
                                        first_name=farmer.first_name, last_name=farmer.last_name)
        farmer.user = user
        farmer.save()
        return farmer


class DepotTestForm(forms.ModelForm):
    class Meta:
        model = DepotTest
        fields = ["moisture_pct", "beans_cut", "purple", "slaty", "mouldy", "insect", "germinated", "remarks"]
        labels = {"moisture_pct": "Moisture %", "insect": "Insect damaged"}

    def clean_moisture_pct(self):
        v = self.cleaned_data["moisture_pct"]
        if v < 0 or v > 40:
            raise forms.ValidationError("Enter a moisture reading between 0 and 40%.")
        return v

    def clean(self):
        d = super().clean()
        cut = d.get("beans_cut") or 0
        defects = sum(d.get(k) or 0 for k in ("purple", "slaty", "mouldy", "insect", "germinated"))
        if cut <= 0:
            self.add_error("beans_cut", "Enter how many beans were cut.")
        elif defects > cut:
            self.add_error(None, "Defective beans cannot be more than the beans cut.")
        return d
