import logging
from django.db import transaction
from datetime import datetime
from django.utils import timezone
import pytz # Ensure pytz is imported
from django.conf import settings # Import settings to check USE_TZ

class DatabaseHandler(logging.Handler):
    def emit(self, record):
        try:
            from .models import LogEntry # Import your LogEntry model

            # Convert the float timestamp (record.created) to a datetime object
            local_naive_datetime = datetime.fromtimestamp(record.created)

            # Make the datetime object timezone-aware based on Django settings.
            if getattr(settings, 'USE_TZ', False): # Check if USE_TZ is True in settings
                # If USE_TZ is True, make the datetime aware and convert to UTC
                log_timestamp_aware = timezone.make_aware(local_naive_datetime, pytz.utc)
            else:
                # If USE_TZ is False, keep it naive (assuming local time)
                log_timestamp_aware = local_naive_datetime

            # Extract the new custom fields from the log record's attributes.
            # Use getattr with a default of None to prevent errors if a field isn't present
            # in a particular log record's 'extra' data.
            email_uid = getattr(record, 'email_uid', None)
            email_subject = getattr(record, 'email_subject', None)
            email_sender = getattr(record, 'email_sender', None)
            email_received_time = getattr(record, 'email_received_time', None)
            attachment_count = getattr(record, 'attachment_count', None)
            last_attachment_name = getattr(record, 'last_attachment_name', None)
            last_attachment_size_kb = getattr(record, 'last_attachment_size_kb', None)
            process_status = getattr(record, 'process_status', None)


            with transaction.atomic():
                LogEntry.objects.create(
                    timestamp=log_timestamp_aware,
                    level=record.levelname,
                    message=self.format(record),
                    # REMOVED: logger_name=record.name,
                    # REMOVED: pathname=record.pathname,
                    # REMOVED: lineno=record.lineno,
                    # REMOVED: funcname=record.funcName,
                    
                    # --- Populate new fields if they exist in the log record ---
                    email_uid=email_uid,
                    email_subject=email_subject,
                    email_sender=email_sender,
                    email_received_time=email_received_time,
                    attachment_count=attachment_count,
                    last_attachment_name=last_attachment_name,
                    last_attachment_size_kb=last_attachment_size_kb,
                    process_status=process_status,
                )
        except Exception as e:
            # IMPORTANT: Do NOT use Django models here as they might be the source of the error
            # This print statement is a fallback if saving to DB fails
            print(f"CRITICAL ERROR: Failed to save log to database: {e}")
            print(f"Original Log (not saved): [{record.levelname}] {record.getMessage()}")