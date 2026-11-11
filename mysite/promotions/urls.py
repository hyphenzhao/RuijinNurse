from django.urls import path
# from .views import promotion_view, setup_view
from . import views
from .qa_timing import qa_timing_report
from .timing_analytics import timing_analytics_data, timing_analytics_export

urlpatterns = [
    path('', views.promotion_view, name='promotion'),
    path("stream/", views.promotion_stream, name="promotion_stream"),
    path('stop/', views.promotion_stop_view, name='promotion_stop'),
    path('timing/report/', qa_timing_report, name='qa_timing_report'),
    path('help/readme/', views.readme_help_view, name='readme_help'),
    path('setup/', views.setup_view, name='setup'),
    path('setup/ollama-models/', views.ollama_models_view, name='ollama_models'),
    path('setup/ai-extract/', views.ai_extract_view, name='ai_extract'),
    path('setup/ai-batch-import/', views.ai_batch_import_view, name='ai_batch_import'),
    path('setup/ai-batch-stream/', views.ai_batch_stream_view, name='ai_batch_stream'),
    # ── 二期升级：患者管理 / 数据分析 / AI 驱动配置 ──
    path('patient/select/', views.patient_select_view, name='patient_select'),
    path('patients/', views.patients_view, name='patients'),
    path('analytics/', views.analytics_view, name='analytics'),
    path('analytics/data/', views.analytics_data_view, name='analytics_data'),
    path('analytics/timing/', timing_analytics_data, name='qa_timing_analytics'),
    path('analytics/timing/export/', timing_analytics_export, name='qa_timing_export'),
    path('analytics/extract/', views.analytics_extract_view, name='analytics_extract'),
    path('analytics/insight/', views.analytics_insight_view, name='analytics_insight'),
    path('analytics/ai/fetch-models/', views.ai_fetch_models_view, name='ai_fetch_models'),
    path('analytics/ai/save-config/', views.ai_save_config_view, name='ai_save_config'),
]
