from django.urls import path, include
from .api import urls as api_urls
from .api.auth_views import login_view, logout_view, me_view

urlpatterns = [
    path('auth/login/', login_view, name='auth-login'),
    path('auth/logout/', logout_view, name='auth-logout'),
    path('auth/me/', me_view, name='auth-me'),
    path('', include(api_urls)),
]
