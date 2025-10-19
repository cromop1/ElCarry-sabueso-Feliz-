from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    # Admin de Django
    path('admin/', admin.site.urls),

    # App principal
    path('', include('Core.urls')),

    # Redirigir la raíz a login si quieres (opcional)
    path('', RedirectView.as_view(pattern_name='login', permanent=False)),
]
