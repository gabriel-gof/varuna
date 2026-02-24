from django.http import JsonResponse
from django.urls import path, include
from django.views.decorators.http import require_GET
from .api import urls as api_urls
from .api.auth_views import login_view, logout_view, me_view, change_password_view


@require_GET
def healthz(_request):
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('auth/login/', login_view, name='auth-login'),
    path('auth/logout/', logout_view, name='auth-logout'),
    path('auth/me/', me_view, name='auth-me'),
    path('auth/change-password/', change_password_view, name='auth-change-password'),
    path('', include(api_urls)),
]
