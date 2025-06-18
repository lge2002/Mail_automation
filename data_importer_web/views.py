import imaplib
import email
import os
import re
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import shutil
import tempfile
import io
import hashlib
from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.core.management.base import CommandError
from django.db import transaction, IntegrityError, connection
from django.utils import timezone
from django.conf import settings
import pytz
from .models import LogEntry

# Configure logging at a basic level
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Keep INFO for general output, DEBUG for more detail
# If you want truly basic output, you might remove handlers and just use print() for quick debugging,
# but keeping logging is good practice.

# Define permanent save directory for attachments
PERMANENT_SAVE_DIR = r'C:/Users/tamilarasans/Desktop/mail_web/downloads'

# File to track processed email UIDs
PROCESSED_UIDS_FILE = 'processed_uids.txt'

class EmailProcessorLogic:
    def __init__(self, overwrite_existing=False):
        # Initialize email configuration from Django settings
        self.email_host = getattr(settings, 'EMAIL_IMAP_HOST')
        self.email_port = getattr(settings, 'EMAIL_IMAP_PORT')
        self.email_user = getattr(settings, 'EMAIL_IMAP_USER')
        self.email_password = getattr(settings, 'EMAIL_IMAP_PASSWORD')
        self.email_label = getattr(settings, 'EMAIL_IMAP_LABEL', 'INBOX')
        self.attachment_keyword = getattr(settings, 'EMAIL_ATTACHMENT_KEYWORD', None)

        if self.attachment_keyword:
            self.attachment_keyword = self.attachment_keyword.lower()

        self.overwrite_existing = overwrite_existing

        # Setup attachment save directory
        self.save_directory = PERMANENT_SAVE_DIR
        os.makedirs(self.save_directory, exist_ok=True)
        root_logger.info(f"Attachments will be saved to: '{os.path.abspath(self.save_directory)}'")

        if self.attachment_keyword:
            root_logger.info(f"Attachment filter keyword: '{self.attachment_keyword}'.")
        else:
            root_logger.info("No attachment filter applied.")

        # Initialize processed UIDs tracking
        self.processed_uids_path = os.path.join(settings.BASE_DIR, PROCESSED_UIDS_FILE)
        self.processed_uids = self._load_processed_uids()

        # Initialize status variables for the latest email processed
        self.latest_email_uid = None
        self.latest_email_subject = None
        self.latest_email_sender = None
        self.latest_email_date = None
        self.latest_attachment_name = None
        self.latest_attachment_size_kb = None
        self.total_attachments_processed = 0
        self.latest_email_status_in_run = "NOT_FOUND" # Simplified status name

    def _load_processed_uids(self):
        """Loads UIDs of previously processed emails from a file."""
        processed_uids = set()
        if os.path.exists(self.processed_uids_path):
            try:
                with open(self.processed_uids_path, 'r') as f:
                    processed_uids = set(f.read().splitlines())
                root_logger.info(f"Loaded {len(processed_uids)} UIDs from '{self.processed_uids_path}'.")
            except IOError as e:
                root_logger.error(f"Error loading UIDs file: {e}")
        return processed_uids

    def _save_processed_uid(self, uid_str):
        """Saves a processed email UID to the file."""
        try:
            with open(self.processed_uids_path, 'a') as f:
                f.write(uid_str + '\n')
            self.processed_uids.add(uid_str)
            root_logger.debug(f"Saved UID {uid_str}.")
        except IOError as e:
            root_logger.error(f"Error saving UID {uid_str}: {e}")

    def connect_to_gmail(self):
        """Establishes IMAP SSL connection to the email server."""
        try:
            mail = imaplib.IMAP4_SSL(self.email_host, self.email_port)
            mail.login(self.email_user, self.email_password)
            mail.select(self.email_label)
            root_logger.info(f"Connected to '{self.email_host}' label '{self.email_label}'.")
            return mail
        except imaplib.IMAP4.error as e:
            root_logger.error(f"IMAP connection error: {e}. Check credentials.")
            return None
        except Exception as e:
            root_logger.error(f"Unexpected error connecting to email: {e}")
            return None

    def get_latest_email_uid(self, mail_connection):
        """Fetches the UID of the latest email in the selected mailbox."""
        try:
            result, data = mail_connection.uid('search', None, 'ALL')
            if result == 'OK' and data[0]:
                uid_list_str = data[0].decode().split()
                total_emails_in_mailbox = len(uid_list_str)
                root_logger.info(f"Found {total_emails_in_mailbox} emails in mailbox.")
                if uid_list_str:
                    return uid_list_str[-1] # Return the last UID (latest email)
            root_logger.warning(f"No emails found in label '{self.email_label}'.")
            return None
        except Exception as e:
            root_logger.error(f"Error fetching latest email UID: {e}")
            return None

    def _sanitize_column_name(self, col_name):
        """Sanitizes column names for SQL compatibility."""
        sanitized = re.sub(r'[^\w]+', '_', str(col_name).strip())
        if re.match(r'^\d', sanitized):
            sanitized = '_' + sanitized
        sanitized = sanitized[:50] # Truncate to avoid overly long names
        reserved_keywords = {'ORDER', 'GROUP', 'SELECT', 'FROM', 'WHERE', 'LIMIT', 'COUNT', 'TABLE', 'COLUMN', 'PRIMARY', 'KEY', 'AUTO_INCREMENT'}
        if sanitized.upper() in reserved_keywords:
            sanitized = '_' + sanitized
        return sanitized.lower()

    def _infer_mysql_type(self, value):
        """Infers a suitable MySQL data type based on a sample value."""
        if pd.isna(value):
            return 'VARCHAR(255)' # Default for NaN/None
        if isinstance(value, (int, np.integer)):
            if -2147483648 <= value <= 2147483647:
                return 'INT'
            else:
                return 'BIGINT'
        elif isinstance(value, (float, np.floating)):
            return 'DOUBLE'
        elif isinstance(value, (datetime, pd.Timestamp)):
            return 'DATETIME'
        elif isinstance(value, bool):
            return 'BOOLEAN'
        else:
            try:
                pd.to_datetime(value)
                return 'DATETIME'
            except (ValueError, TypeError):
                return 'VARCHAR(255)' # Default for strings

    def _process_excel_file(self, file_path):
        """Processes an Excel file to create a dynamic MySQL table and insert data."""
        root_logger.info(f"Processing Excel: '{os.path.basename(file_path)}'")

        try:
            # Read header and sample row to infer column names and types
            header_and_sample_data_df = pd.read_excel(file_path, header=None, nrows=2)

            if header_and_sample_data_df.empty or len(header_and_sample_data_df) < 2:
                root_logger.warning(f"Excel file '{os.path.basename(file_path)}' is empty or too short. Skipping.")
                return 0

            excel_column_names = header_and_sample_data_df.iloc[0].tolist()
            sample_row_for_types = header_and_sample_data_df.iloc[1]
            column_definitions = {}

            # Sanitize column names and infer types
            for i, col_name_raw in enumerate(excel_column_names):
                sanitized_col_name = self._sanitize_column_name(col_name_raw)
                original_sanitized_col_name = sanitized_col_name
                count = 1
                while sanitized_col_name in column_definitions: # Handle duplicate sanitized names
                    sanitized_col_name = f"{original_sanitized_col_name}_{count}"
                    count += 1

                inferred_type = self._infer_mysql_type(sample_row_for_types.iloc[i])
                column_definitions[sanitized_col_name] = inferred_type
                root_logger.debug(f"Inferred: '{col_name_raw}' -> '{sanitized_col_name}' ({inferred_type})")

            # Generate dynamic table name
            base_filename = os.path.splitext(os.path.basename(file_path))[0]
            sanitized_base_filename = self._sanitize_column_name(base_filename)
            timestamp_suffix = datetime.now().strftime("%Y%m%d%H%M%S")
            table_name = f"excel_data_{sanitized_base_filename}_{timestamp_suffix}"
            root_logger.info(f"Generated table name: '{table_name}'.")

            # Create table SQL
            columns_definition_string = ',\n'.join([f"`{col}` {col_type}" for col, col_type in column_definitions.items()])
            create_table_sql = f"""
CREATE TABLE `{table_name}` (
id INT AUTO_INCREMENT PRIMARY KEY,
{columns_definition_string}
);
"""
            root_logger.info(f"Creating table '{table_name}'.")
            with connection.cursor() as cursor:
                try:
                    cursor.execute(create_table_sql)
                    root_logger.info(f"Table '{table_name}' created successfully.")
                except Exception as e:
                    root_logger.error(f"Error creating table '{table_name}': {e}")
                    raise CommandError(f"Failed to create table '{table_name}': {e}")

            total_rows_inserted = 0
            # Read all data, skipping the header rows
            data_df_raw = pd.read_excel(file_path, header=None, skiprows=2)

            if data_df_raw.empty:
                root_logger.warning(f"Data DataFrame is empty. Skipping insertion.")
                return 0

            if len(data_df_raw.columns) != len(column_definitions):
                root_logger.error(f"Column count mismatch. Expected {len(column_definitions)}, got {len(data_df_raw.columns)}. Skipping insertion.")
                return 0

            # Rename columns based on sanitized names
            new_column_names = list(column_definitions.keys())
            rename_mapping = {i: col_name for i, col_name in enumerate(new_column_names)}
            data_df = data_df_raw.rename(columns=rename_mapping)
            records_to_insert = data_df.to_dict(orient='records')

            if not records_to_insert:
                root_logger.warning(f"No records to insert after processing. Skipping.")
                return 0

            # Prepare INSERT SQL
            columns_for_insert = ", ".join([f"`{col}`" for col in column_definitions.keys()])
            placeholders = ", ".join(["%s"] * len(column_definitions))
            insert_sql = f"INSERT INTO `{table_name}` ({columns_for_insert}) VALUES ({placeholders})"

            with connection.cursor() as cursor:
                rows_inserted_count = 0
                for record in records_to_insert:
                    values = []
                    for col_name in column_definitions.keys():
                        val = record.get(col_name) # Handle potential missing columns in records
                        if isinstance(val, (np.integer, np.int64)):
                            values.append(int(val))
                        elif isinstance(val, (np.floating, np.float64)):
                            values.append(float(val))
                        elif isinstance(val, pd.Timestamp):
                            values.append(val.to_pydatetime())
                        elif pd.isna(val):
                            values.append(None)
                        else:
                            values.append(val)

                    try:
                        cursor.execute(insert_sql, values)
                        rows_inserted_count += 1
                    except Exception as e:
                        root_logger.error(f"Error inserting row into '{table_name}': {e} - Data: {record}")

            root_logger.info(f"Inserted {rows_inserted_count} rows into '{table_name}'.")
            total_rows_inserted = rows_inserted_count
            root_logger.info(f"Finished inserting data from '{os.path.basename(file_path)}'.")
            return total_rows_inserted

        except pd.errors.EmptyDataError:
            root_logger.error(f"Error: Excel file '{os.path.basename(file_path)}' is empty or corrupted.")
            return 0
        except CommandError:
            raise # Re-raise CommandError as it's a specific Django error
        except Exception as e:
            root_logger.error(f"Unexpected error during Excel processing for '{os.path.basename(file_path)}': {e}")
            raise # Re-raise other unexpected errors

    def download_attachments(self):
        """Connects to email, fetches latest email, and processes attachments."""
        mail = None

        # Reset status variables for a new run
        self.latest_email_uid = None
        self.latest_email_subject = None
        self.latest_email_sender = None
        self.latest_email_date = None
        self.latest_attachment_name = None
        self.latest_attachment_size_kb = None
        self.total_attachments_processed = 0
        self.latest_email_status_in_run = "NOT_FOUND"

        root_logger.info("\n--- Email Import Process Initiated ---")

        try:
            mail = self.connect_to_gmail()
            if not mail:
                root_logger.error("Failed to connect to email server.")
                return

            latest_uid_str = self.get_latest_email_uid(mail)

            if not latest_uid_str:
                root_logger.info("No emails found in mailbox.")
                self.latest_email_status_in_run = "NO_NEW_EMAIL" # Simplified status
                return

            self.latest_email_uid = latest_uid_str

            # Fetch header for basic info before deciding to process
            result, msg_header_data = mail.uid('fetch', latest_uid_str.encode('utf-8'), '(BODY.PEEK[HEADER])')

            if result == 'OK' and msg_header_data and msg_header_data[0]:
                temp_msg_for_latest_info = email.message_from_bytes(msg_header_data[0][1])
                self.latest_email_subject = temp_msg_for_latest_info.get('subject', 'No Subject')
                self.latest_email_sender = temp_msg_for_latest_info.get('from', 'Unknown Sender')
                raw_date = temp_msg_for_latest_info.get('date', 'No Date')
                try:
                    self.latest_email_date = parsedate_to_datetime(raw_date)
                except (TypeError, ValueError):
                    self.latest_email_date = None
            else:
                self.latest_email_subject = 'N/A'
                self.latest_email_sender = 'N/A'
                self.latest_email_date = None

            root_logger.info(f"Latest email: Subject: '{self.latest_email_subject}', Sender: '{self.latest_email_sender}'")

            if latest_uid_str in self.processed_uids:
                root_logger.info(f"Skipped: Email UID {latest_uid_str} already processed.")
                self.latest_email_status_in_run = "ALREADY_PROCESSED"
                return
            else:
                root_logger.info(f"Processing new email UID {latest_uid_str}.")
                self.latest_email_status_in_run = "NEW_EMAIL_PROCESSED" # Simplified status

                result, msg_full_data = mail.uid('fetch', latest_uid_str.encode('utf-8'), '(RFC822)')
                if result != 'OK' or not msg_full_data or not msg_full_data[0]:
                    root_logger.warning(f"Failed to fetch full message for UID {latest_uid_str}.")
                    self.latest_email_status_in_run = "FETCH_FAILED"
                    return
                else:
                    full_message_bytes = msg_full_data[0][1]
                    msg = email.message_from_bytes(full_message_bytes)

                    attachments_found_count = 0
                    last_attachment_name = None
                    last_attachment_size_kb = None

                    for part in msg.walk():
                        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                            continue

                        filename_raw = part.get_filename()
                        if not filename_raw:
                            continue

                        try:
                            filename = email.header.decode_header(filename_raw)[0][0]
                            if isinstance(filename, bytes):
                                filename = filename.decode('utf-8')
                        except Exception:
                            filename = filename_raw

                        if not filename.lower().endswith(('.xlsx', '.xls')):
                            root_logger.debug(f"Skipping '{filename}': Not an Excel file.")
                            continue

                        if self.attachment_keyword and self.attachment_keyword not in filename.lower():
                            root_logger.debug(f"Skipping '{filename}': Filter keyword not found.")
                            continue

                        # Handle duplicate filenames
                        filepath = os.path.join(self.save_directory, filename)
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        original_filepath = filepath
                        while os.path.exists(filepath):
                            filepath = os.path.join(self.save_directory, f"{base}_{counter}{ext}")
                            counter += 1

                        if filepath != original_filepath:
                            root_logger.warning(f"File '{original_filepath}' exists. Saving as '{filepath}'.")

                        try:
                            with open(filepath, 'wb') as f:
                                f.write(part.get_payload(decode=True))

                            attachments_found_count += 1
                            file_size_bytes = os.path.getsize(filepath)
                            file_size_kb = file_size_bytes / 1024
                            root_logger.info(f"Downloaded: '{filename}' (Size: {file_size_kb:.2f} KB)")

                            last_attachment_name = filename
                            last_attachment_size_kb = file_size_kb

                            # Process the downloaded Excel file
                            self._process_excel_file(filepath)

                        except Exception as e:
                            root_logger.error(f"Error processing attachment '{filename}': {e}")

                    self.total_attachments_processed = attachments_found_count
                    self.latest_attachment_name = last_attachment_name
                    self.latest_attachment_size_kb = last_attachment_size_kb

                    root_logger.info(f"Total attachments processed: {attachments_found_count}")

                    # Mark the email as processed only if attachments were found and processed
                    # Or if you always want to mark it as processed if it was a new email, regardless of attachments.
                    # Current logic marks it as processed if it's a new email and we successfully fetched it.
                    self._save_processed_uid(latest_uid_str)

                    root_logger.info(f"--- Finished Processing Email UID {latest_uid_str} ---")

        except imaplib.IMAP4.error as e:
            root_logger.error(f"IMAP Error: {e}.")
        except Exception as e:
            root_logger.exception(f"An unexpected error occurred: {e}")
        finally:
            if mail:
                mail.logout()
                root_logger.info("Logged out from email server. Process finished.")

def import_emails_view(request):
    """Django view to trigger and display email import status."""
    log_output_string = ""
    status_message = "Ready to import emails."
    
    # Create a StringIO object to capture log output for the web display
    log_stream = io.StringIO()
    stream_handler = logging.StreamHandler(log_stream)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream_handler.setFormatter(formatter)
    
    # Add the stream handler to the root logger
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(logging.INFO) # Ensure INFO level for this view's logging

    try:
        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'start_import':
                status_message = "Import process initiated..."
                try:
                    processor = EmailProcessorLogic() # Removed overwrite_existing=True if not explicitly needed
                    processor.download_attachments()

                    # Simplified status message based on processor's final status
                    if processor.latest_email_status_in_run == "NEW_EMAIL_PROCESSED":
                        status_message = "Import completed: New email processed."
                    elif processor.latest_email_status_in_run == "ALREADY_PROCESSED":
                        status_message = "Import completed: Latest email already processed."
                    elif processor.latest_email_status_in_run == "NO_NEW_EMAIL":
                        status_message = "Import completed: No new emails found."
                    elif processor.latest_email_status_in_run == "FETCH_FAILED":
                        status_message = "Import failed: Could not fetch email data."
                    else:
                        status_message = "Import process completed with unknown status."

                except Exception as e:
                    status_message = f"Error during import: {e}"
                    root_logger.error(status_message) # No need for extra dict in basic view logging

        # Get the captured log output
        log_output_string = log_stream.getvalue()

    except Exception as e:
        root_logger.error(f"An unhandled error occurred in the view: {e}")
        log_output_string += f"\nERROR: An unhandled error occurred: {e}\n"
    finally:
        # Crucial: Remove the handler to prevent duplicate logs on subsequent requests
        root_logger.removeHandler(stream_handler)
        log_stream.close()

    context = {
        'message': status_message,
        'log_output': log_output_string,
    }

    return render(request, 'data_importer_web/import_status.html', context)

def historical_logs_view(request):
    """Django view to display historical log entries, with date filtering."""
    logs = LogEntry.objects.all()
    selected_date = request.GET.get('log_date')

    if selected_date:
        try:
            parsed_date = datetime.strptime(selected_date, '%Y-%m-%d').date()

            start_of_day = datetime.combine(parsed_date, datetime.min.time())
            end_of_day = datetime.combine(parsed_date, datetime.max.time())

            # Make them timezone-aware if USE_TZ is True in settings
            if getattr(settings, 'USE_TZ', False):
                start_of_day = timezone.make_aware(start_of_day, pytz.utc) # Assuming storage in UTC
                end_of_day = timezone.make_aware(end_of_day, pytz.utc)

            logs = logs.filter(timestamp__gte=start_of_day, timestamp__lte=end_of_day)

        except ValueError:
            selected_date = None # Clear invalid date for template
            # No specific error message needed for "basic" level, just ignore bad input

    logs = logs.order_by('-timestamp') # Always order by newest first

    context = {
        'logs': logs,
        'selected_date': selected_date, # Pass back to pre-fill date input
    }
    return render(request, 'data_importer_web/historical_logs.html', context)