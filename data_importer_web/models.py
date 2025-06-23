from django.db import models
from django.utils import timezone


class LogEntry(models.Model):
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    level = models.CharField(max_length=20) # INFO, WARNING, ERROR, DEBUG, CRITICAL
    message = models.TextField()

    # ADDED FIELDS FOR EMAIL LOGGING CONTEXT (these remain)
    email_uid = models.CharField(max_length=255, null=True, blank=True, help_text="Unique ID of the processed email")
    email_subject = models.CharField(max_length=500, null=True, blank=True, help_text="Subject line of the email")
    email_sender = models.CharField(max_length=255, null=True, blank=True, help_text="Sender's email address or name")
    email_received_time = models.DateTimeField(null=True, blank=True, help_text="When the email was received")
    attachment_count = models.IntegerField(null=True, blank=True, help_text="Number of attachments processed from the email")
    last_attachment_name = models.CharField(max_length=255, null=True, blank=True, help_text="Name of the last processed attachment")
    last_attachment_size_kb = models.FloatField(null=True, blank=True, help_text="Size of the last processed attachment in KB")
    process_status = models.CharField(max_length=50, null=True, blank=True, help_text="Status of the email processing (e.g., SUCCESS, FAILED)")

    class Meta:
        verbose_name_plural = "Log Entries" # Nicer name in Django Admin
        ordering = ['-timestamp'] # Order by newest first by default

    def __str__(self):
        # Enhance string representation to include relevant email info if available
        if self.email_subject and self.email_sender:
            return f"[{self.level}] {self.timestamp}: Email '{self.email_subject}' from '{self.email_sender}' - {self.message[:50]}..."
        return f"[{self.level}] {self.timestamp}: {self.message[:100]}..." # Truncate message for display

# --- NEW MODEL BASED ON YOUR SQL TABLE SCHEMA ---
class ExcelDataEntry(models.Model):
    # Django automatically creates an 'id' field as primary key unless specified otherwise.
    # So, we don't need to define 'id' explicitly unless you have a custom ID.

    # Corresponds to `locno` varchar(50) DEFAULT NULL
    locno = models.CharField(max_length=50, null=True, blank=True)

    # Corresponds to `datetime` datetime DEFAULT NULL
    # Using db_index=True for potentially faster queries on datetime.
    datetime = models.DateTimeField(null=True, blank=True, db_index=True)

    # These fields are VARCHAR(10) in your SQL, so they should be CharField in Django.
    # This means numerical data will be stored as strings in the database.
    outdoor_temp = models.CharField(max_length=10, null=True, blank=True) # Corresponds to `Outdoor_Temp` varchar(10) DEFAULT NULL
    wind_speed = models.CharField(max_length=10, null=True, blank=True)   # Corresponds to `Wind_Speed` varchar(10) DEFAULT NULL
    nacelle_pos = models.CharField(max_length=10, null=True, blank=True)  # Corresponds to `Nacelle_Pos` varchar(10) DEFAULT NULL
    active_power = models.CharField(max_length=10, null=True, blank=True) # Corresponds to `Active_Power` varchar(10) DEFAULT NULL
    frequency = models.CharField(max_length=10, null=True, blank=True)    # Corresponds to `frequency` varchar(10) DEFAULT NULL

    # --- Optional: Add these fields for better traceability and debugging ---
    # # These were in our previous ExcelDataEntry proposal, good for tracking source.
    # source_filename = models.CharField(max_length=255, blank=True, null=True)
    # source_email_uid = models.CharField(max_length=255, blank=True, null=True)
    # imported_at = models.DateTimeField(auto_now_add=True) # Automatically sets creation timestamp

    class Meta:
        # Default table name will be 'your_app_name_exceldataentry'.
        # If you want a very specific fixed name like 'suz_excel_data' (NOT 'suz_20250101' as that's dynamic), you can add:
        # db_table = 'suz_excel_data' # <-- Uncomment and set if you want a custom, fixed table name.

        # Add a unique constraint to prevent duplicate entries for the same data point.
        # This is useful with bulk_create(ignore_conflicts=True).
        unique_together = ('datetime', 'locno')

        verbose_name = "Excel Data Entry"
        verbose_name_plural = "Excel Data Entries"
        ordering = ['-datetime', 'locno'] # Default ordering for queries

    def __str__(self):
        # A human-readable representation of an object, useful in admin.
        return f"[{self.locno}] Data for {self.datetime.strftime('%Y-%m-%d %H:%M:%S')} from {self.source_filename or 'N/A'}"