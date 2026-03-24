from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('promotions', '0002_knowledgedocument'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='ollama_host',
            field=models.CharField(
                default='127.0.0.1',
                help_text='Ollama 服务 IP 或主机名，例如 127.0.0.1',
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name='agent',
            name='ollama_port',
            field=models.PositiveIntegerField(
                default=11434,
                help_text='Ollama 服务端口，例如 11434',
            ),
        ),
    ]
