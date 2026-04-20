from django.urls import path
# from .views import promotion_view, setup_view
from . import views

urlpatterns = [
    path('', views.promotion_view, name='promotion'),
    path("stream/", views.promotion_stream, name="promotion_stream"),
    path('stop/', views.promotion_stop_view, name='promotion_stop'),
    path('help/readme/', views.readme_help_view, name='readme_help'),
    path('setup/', views.setup_view, name='setup'),
    path('setup/ollama-models/', views.ollama_models_view, name='ollama_models'),
]
