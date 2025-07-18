# Generated by Django 5.2.1 on 2025-06-11 06:40

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='LogEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('level', models.CharField(max_length=20)),
                ('message', models.TextField()),
                ('email_uid', models.CharField(blank=True, help_text='Unique ID of the processed email', max_length=255, null=True)),
                ('email_subject', models.CharField(blank=True, help_text='Subject line of the email', max_length=500, null=True)),
                ('email_sender', models.CharField(blank=True, help_text="Sender's email address or name", max_length=255, null=True)),
                ('email_received_time', models.DateTimeField(blank=True, help_text='When the email was received', null=True)),
                ('attachment_count', models.IntegerField(blank=True, help_text='Number of attachments processed from the email', null=True)),
                ('last_attachment_name', models.CharField(blank=True, help_text='Name of the last processed attachment', max_length=255, null=True)),
                ('last_attachment_size_kb', models.FloatField(blank=True, help_text='Size of the last processed attachment in KB', null=True)),
                ('process_status', models.CharField(blank=True, help_text='Status of the email processing (e.g., SUCCESS, FAILED)', max_length=50, null=True)),
            ],
            options={
                'verbose_name_plural': 'Log Entries',
                'ordering': ['-timestamp'],
            },
        ),
        migrations.CreateModel(
            name='WindmillReading',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(help_text='The timestamp of the reading (rounded to the nearest second).')),
                ('turbine_id', models.CharField(help_text="Unique identifier for the windmill turbine (e.g., 'K167', 'K902').", max_length=50)),
                ('outdoor_temp_avg', models.FloatField(blank=True, help_text='Average outdoor temperature in Celsius. Null if not available.', null=True)),
                ('wind_speed_avg', models.FloatField(blank=True, help_text='Average wind speed in meters per second. Null if not available.', null=True)),
                ('nacelle_pos_avg', models.FloatField(blank=True, help_text='Average nacelle position in degrees. Null if not available.', null=True)),
                ('active_power_avg', models.FloatField(blank=True, help_text='Average active power generated in kilowatts (kW). Null if not available.', null=True)),
                ('grid_freq_avg', models.FloatField(blank=True, help_text='Average grid frequency in Hertz (Hz). Null if not available.', null=True)),
            ],
            options={
                'verbose_name': 'Windmill Reading',
                'verbose_name_plural': 'Windmill Readings',
                'ordering': ['-timestamp', 'turbine_id'],
                'unique_together': {('timestamp', 'turbine_id')},
            },
        ),
    ]
