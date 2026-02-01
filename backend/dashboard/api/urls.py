from rest_framework.routers import DefaultRouter
from .views import OLTViewSet, OLTSlotViewSet, OLTPONViewSet, ONUViewSet, VendorProfileViewSet

router = DefaultRouter()
router.register(r'olts', OLTViewSet, basename='olt')
router.register(r'slots', OLTSlotViewSet, basename='slot')
router.register(r'pons', OLTPONViewSet, basename='pon')
router.register(r'onu', ONUViewSet, basename='onu')
router.register(r'vendor-profiles', VendorProfileViewSet, basename='vendor-profile')

urlpatterns = router.urls
