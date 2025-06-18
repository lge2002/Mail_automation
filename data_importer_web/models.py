from django.db import models
from django.utils import timezone


class LogEntry(models.Model):
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    level = models.CharField(max_length=20) # INFO, WARNING, ERROR, DEBUG, CRITICAL
    message = models.TextField()
    
    # REMOVED FIELDS: logger_name, pathname, lineno, funcname
    # logger_name = models.CharField(max_length=100, blank=True, null=True)
    # pathname = models.CharField(max_length=255, blank=True, null=True) 
    # lineno = models.IntegerField(blank=True, null=True)                           
    # funcname = models.CharField(max_length=100, blank=True, null=True) 

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
        # Assuming your custom LogEntry maps to the 'data_importer_web_logentry' table,
        # it's good practice to explicitly set db_table if it's not the default Django name.
        # However, if your logging setup dynamically creates this table, this might not be needed.
        # For safety and clarity, if this IS the model for the table you showed earlier, add:
        # db_table = 'data_importer_web_logentry' 
        # (Uncomment the line above if data_importer_web_logentry is your table name.)


    def __str__(self):
        # Enhance string representation to include relevant email info if available
        if self.email_subject and self.email_sender:
            return f"[{self.level}] {self.timestamp}: Email '{self.email_subject}' from '{self.email_sender}' - {self.message[:50]}..."
        return f"[{self.level}] {self.timestamp}: {self.message[:100]}..." # Truncate message for display