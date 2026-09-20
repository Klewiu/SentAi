from rest_framework import serializers
from .models import Page

class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        fields = ("id", "name", "url", "verification_status", "verified_at", "verified_by")
        read_only_fields = ("id", "verification_status", "verified_at", "verified_by")
