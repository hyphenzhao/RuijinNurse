"""URL routing for the RuijinNurse REST API (prefix: /api/v1/)."""
from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path('auth/login/', views.LoginView.as_view(), name='api_login'),
    path('auth/refresh/', views.RefreshView.as_view(), name='api_token_refresh'),
    path('auth/logout/', views.LogoutView.as_view(), name='api_logout'),

    # Chat
    path('chat/stream/', views.ChatStreamView.as_view(), name='api_chat_stream'),
    path('chat/stop/', views.ChatStopView.as_view(), name='api_chat_stop'),

    # Agents
    path('agents/', views.AgentListView.as_view(), name='api_agents'),
    path('agents/<str:slug>/default/', views.AgentSetDefaultView.as_view(), name='api_agent_set_default'),

    # Models
    path('models/', views.ModelListView.as_view(), name='api_models'),

    # Sessions
    path('sessions/', views.SessionListView.as_view(), name='api_sessions'),
    path('sessions/<str:session_id>/', views.SessionDetailView.as_view(), name='api_session_detail'),

    # Health
    path('health/', views.HealthView.as_view(), name='api_health'),

    # Settings
    path('settings/', views.SettingsView.as_view(), name='api_settings'),
]
