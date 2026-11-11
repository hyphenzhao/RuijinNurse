from django.contrib import admin

from .models import (
    Agent, KnowledgeDocument, KnowledgeGraph,
    ReferenceCard, KnowledgeEntry, LiteratureEntry,
    Patient, SurgeryPlan, QARecord, AIConfig,
)


class SurgeryPlanInline(admin.TabularInline):
    model = SurgeryPlan
    extra = 0


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'agent_type', 'is_active', 'is_default')
    list_filter = ('agent_type', 'is_active', 'is_default')
    search_fields = ('name', 'slug')


@admin.register(KnowledgeGraph)
class KnowledgeGraphAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    search_fields = ('name',)


@admin.register(ReferenceCard)
class ReferenceCardAdmin(admin.ModelAdmin):
    list_display = ('title', 'knowledge_graph', 'category', 'media_type', 'is_active', 'sort_order')
    list_filter = ('category', 'media_type', 'is_active')


@admin.register(KnowledgeEntry)
class KnowledgeEntryAdmin(admin.ModelAdmin):
    list_display = ('title', 'knowledge_graph', 'source', 'is_active', 'created_at')
    search_fields = ('title', 'content', 'tags')


@admin.register(LiteratureEntry)
class LiteratureEntryAdmin(admin.ModelAdmin):
    list_display = ('title', 'authors', 'year', 'journal', 'evidence_level', 'is_active')
    list_filter = ('evidence_level', 'is_active')
    search_fields = ('title', 'authors', 'doi')


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'created_at')


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('bed_no', 'name', 'gender', 'disease_type', 'admission_time',
                    'is_active', 'created_at')
    list_filter = ('disease_type', 'gender', 'is_active')
    search_fields = ('bed_no', 'name', 'notes')
    inlines = [SurgeryPlanInline]


@admin.register(QARecord)
class QARecordAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'source', 'patient', 'model_key', 'extraction_status',
                    'created_at')
    list_filter = ('source', 'extraction_status', 'created_at')
    search_fields = ('question', 'answer', 'session_scope')
    readonly_fields = ('created_at',)


@admin.register(AIConfig)
class AIConfigAdmin(admin.ModelAdmin):
    list_display = ('provider_type', 'model', 'is_active', 'updated_at')
    list_filter = ('provider_type', 'is_active')
