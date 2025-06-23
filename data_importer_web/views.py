import imaplib
import email
import os
import logging
import pandas as pd
import numpy as np
from datetime import datetime
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
from .models import LogEntry, ExcelDataEntry # Ensure ExcelDataEntry is imported and defined in models.py
import string # Import string module for character sets
import math # Import math for isnan check

# Configure logging at a basic level
# Get the root logger
root_logger = logging.getLogger()
# Set the level for the root logger to INFO. This will affect all handlers unless overridden.
root_logger.setLevel(logging.INFO)

# Define permanent save directory for attachments
PERMANENT_SAVE_DIR = os.path.join(settings.BASE_DIR, 'downloads') # Safer path handling

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

    def _format_timestamp_for_output(self, timestamp_value):
        """
        Formates a timestamp value (can be datetime, pd.Timestamp, string, int/float epoch)
        into a "YYYY-MM-DD HH:MM:SS" string.
        """
        if pd.isna(timestamp_value) or timestamp_value is None: # Handle NaN and None explicitly
            return None
        
        if isinstance(timestamp_value, datetime):
            return timestamp_value.strftime("%Y-%m-%d %H:%M:%S")
        
        if isinstance(timestamp_value, pd.Timestamp):
            return timestamp_value.to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(timestamp_value, str):
            try:
                # Try to parse ISO format first (e.g., '2025-05-01T00:10:00.000')
                dt_object = datetime.fromisoformat(timestamp_value.replace('Z', '+00:00'))
                return dt_object.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    # Fallback to standard YYYY-MM-DD HH:MM:SS format
                    dt_object = datetime.strptime(timestamp_value, "%Y-%m-%d %H:%M:%S")
                    return dt_object.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    root_logger.debug(f"Could not parse string datetime '{timestamp_value}'.")
                    return None # Indicate parsing failure
        
        elif isinstance(timestamp_value, (int, float)):
            try:
                if timestamp_value > 1e12: # Heuristic for milliseconds epoch
                    dt_object = datetime.fromtimestamp(timestamp_value / 1000)
                else: # Assume seconds epoch
                    dt_object = datetime.fromtimestamp(timestamp_value)
                return dt_object.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                root_logger.debug(f"Could not parse numeric datetime '{timestamp_value}'.")
                return None
        else:
            root_logger.debug(f"Unsupported datetime type: {type(timestamp_value)}")
            return None

    def _process_excel_file(self, file_path, email_uid=None):
        """
        Processes an Excel file, restructures data according to the provided logic,
        and inserts it into the fixed ExcelDataEntry Django model.
        """
        root_logger.info(f"Processing Excel: '{os.path.basename(file_path)}'")

        try:
            # Step 1: Read the entire Excel file without a header, so all rows (including headers) are data.
            raw_df = pd.read_excel(file_path, header=None)

            if raw_df.empty:
                root_logger.warning(f"Excel file '{os.path.basename(file_path)}' is empty. Skipping.")
                return 0

            # The first row contains the multi-line headers
            raw_header_row = raw_df.iloc[0]
            # The rest of the DataFrame contains the actual data
            data_rows_df = raw_df.iloc[1:].copy()

            if data_rows_df.empty:
                root_logger.warning(f"Data rows in Excel file '{os.path.basename(file_path)}' are empty. Skipping insertion.")
                return 0

            # This dictionary will map the Excel column index to a tuple (locno, model_field_name)
            # or a special marker for the datetime column.
            excel_col_idx_to_model_info = {}

            # Define the mapping from cleaned Excel parameter descriptions to your model's field names
            # This is CRUCIAL for the fixed schema approach. Add all expected parameters here.
            # These keys MUST match the output of the cleaned_parameter_description logic below.
            excel_param_to_model_field_map = {
                "Outdoor Temp Average": "outdoor_temp",
                "Wind Speed Average": "wind_speed",
                "Nacelle Pos Average": "nacelle_pos",
                "Active Power Average": "active_power",
                "Grid Frequency Average": "frequency", # Corrected to match your DB column
                # Add all other relevant Excel parameter descriptions and their corresponding model field names
                # Example: "Another Parameter": "another_parameter_model_field"
            }

            source_filename = os.path.basename(file_path)

            # Process the raw header row to build our column mapping
            for col_idx, full_excel_header_str in raw_header_row.items():
                if pd.isna(full_excel_header_str):
                    root_logger.debug(f"Skipping empty header at column index {col_idx}.")
                    continue

                header_parts = str(full_excel_header_str).strip().split('\n')

                # Identify the 'Local Time' column (based on the first two lines of the header)
                if len(header_parts) >= 2 and header_parts[0].strip().lower() == 'local' and header_parts[1].strip().lower() == 'time':
                    excel_col_idx_to_model_info[col_idx] = {'type': 'datetime_column'}
                    root_logger.debug(f"Identified datetime column at index {col_idx}.")
                elif len(header_parts) >= 3:
                    # For data columns, extract locno and parameter description
                    locno_raw = header_parts[1].strip()
                    parameter_description_raw = header_parts[2].strip()

                    # --- START NON-REGEX CLEANING LOGIC FOR parameter_description_raw ---
                    cleaned_parameter_description = ""
                    average_suffix = "Average"
                    if average_suffix in parameter_description_raw:
                        idx = parameter_description_raw.find(average_suffix)
                        cleaned_parameter_description = parameter_description_raw[:idx + len(average_suffix)].strip()
                    else:
                        temp_cleaned_chars = []
                        valid_chars = string.ascii_letters + string.digits + ' ' # Only keep alphanumeric and space
                        for char in parameter_description_raw:
                            if char in valid_chars:
                                temp_cleaned_chars.append(char)
                        
                        temp_string = "".join(temp_cleaned_chars)
                        cleaned_parameter_description = " ".join(temp_string.split()).strip() # Normalize whitespace
                    # --- END NON-REGEX CLEANING LOGIC ---
                    
                    # --- START NON-REGEX LOCNO EXTRACTION/VALIDATION ---
                    locno = None
                    # Example: Assuming locno is like "K123"
                    if locno_raw.startswith('K') and len(locno_raw) > 1 and locno_raw[1:].isdigit():
                        locno = locno_raw
                    # --- END NON-REGEX LOCNO EXTRACTION/VALIDATION ---

                    model_field_name = excel_param_to_model_field_map.get(cleaned_parameter_description)

                    if locno and model_field_name:
                        excel_col_idx_to_model_info[col_idx] = {
                            'type': 'data_column',
                            'locno': locno,
                            'model_field': model_field_name
                        }
                        root_logger.debug(f"Mapped Col Index {col_idx} (Excel Header: '{full_excel_header_str}') to locno='{locno}', model_field='{model_field_name}'.")
                    else:
                        root_logger.debug(f"Skipping Excel header '{full_excel_header_str}' (Col Index: {col_idx}): Unrecognized locno ('{locno_raw}') or parameter ('{cleaned_parameter_description}').")
                else:
                    root_logger.debug(f"Skipping Excel header '{full_excel_header_str}' (Col Index: {col_idx}): Header not in expected format (needs at least 3 parts for data columns).")


            # Dictionary to aggregate data for each (datetime, locno) combination before creating model instances.
            # Key: (datetime_object, locno_str)
            # Value: A dictionary of model field_name: value pairs for that combination.
            aggregated_records = {}

            # Iterate through each row of the data DataFrame (actual data, excluding the header row)
            for index, row in data_rows_df.iterrows():
                current_row_datetime = None

                # First pass: find and parse the datetime column for the current row
                datetime_col_idx = None
                for col_idx, info in excel_col_idx_to_model_info.items():
                    if info['type'] == 'datetime_column':
                        datetime_col_idx = col_idx
                        break
                
                if datetime_col_idx is not None and datetime_col_idx in row:
                    raw_time_value = row[datetime_col_idx]
                    formatted_time_str = self._format_timestamp_for_output(raw_time_value)

                    if formatted_time_str:
                        # Convert back to datetime object for Django's DateTimeField
                        try:
                            current_row_datetime = datetime.strptime(formatted_time_str, "%Y-%m-%d %H:%M:%S")
                            # Make datetime timezone-aware if USE_TZ is True in settings
                            if getattr(settings, 'USE_TZ', False):
                                # Ensure current_row_datetime is naive before making it aware
                                if current_row_datetime.tzinfo is not None:
                                    # Convert to UTC naive before making aware to desired timezone
                                    current_row_datetime = current_row_datetime.astimezone(pytz.utc).replace(tzinfo=None)
                                # Make aware to Django's default timezone or current timezone
                                current_row_datetime = timezone.make_aware(current_row_datetime, timezone.get_current_timezone())
                        except ValueError:
                            current_row_datetime = None
                            root_logger.warning(f"Row {index+1}: Formatted datetime '{formatted_time_str}' could not be converted to datetime object.")
                    else:
                        root_logger.warning(f"Row {index+1}: Could not format datetime from '{raw_time_value}'. Skipping row data for this timestamp.")
                
                if current_row_datetime is None:
                    root_logger.debug(f"Skipping row {index+1} due to unparsable datetime.")
                    continue # Skip this entire row if datetime couldn't be parsed

                # Second pass: populate data for each locno and parameter from this row
                for col_idx, cell_value in row.items():
                    col_info = excel_col_idx_to_model_info.get(col_idx)

                    if col_info and col_info['type'] == 'data_column':
                        locno = col_info['locno']
                        model_field = col_info['model_field']

                        # Initialize entry for this (datetime, locno) if not present
                        record_key = (current_row_datetime, locno)
                        if record_key not in aggregated_records:
                            aggregated_records[record_key] = {
                                'datetime': current_row_datetime,
                                'locno': locno,
                                # 'source_filename': source_filename,
                                # 'source_email_uid': email_uid,
                            }
                            # Initialize all potential data fields to None
                            # This ensures all fields in the model are always present in the dict
                            for field_name_map in excel_param_to_model_field_map.values():
                                if field_name_map not in aggregated_records[record_key]:
                                    aggregated_records[record_key][field_name_map] = None

                        # Assign the value, handling NaN and converting numpy types
                        if pd.isna(cell_value):
                            aggregated_records[record_key][model_field] = None
                        else:
                            # Convert numpy types to native Python types for Django ORM
                            if isinstance(cell_value, (np.integer, np.int64)):
                                aggregated_records[record_key][model_field] = int(cell_value)
                            elif isinstance(cell_value, (np.floating, np.float64)):
                                aggregated_records[record_key][model_field] = float(cell_value)
                            else:
                                aggregated_records[record_key][model_field] = cell_value

            # Convert aggregated data into a list of ExcelDataEntry objects
            excel_data_entries = []
            for record_data in aggregated_records.values():
                excel_data_entries.append(ExcelDataEntry(**record_data))

            total_rows_inserted = 0
            if excel_data_entries:
                root_logger.info(f"Attempting to insert {len(excel_data_entries)} restructured records into ExcelDataEntry.")
                with transaction.atomic(): # Ensures all or nothing for the batch
                    try:
                        # Use bulk_create for efficiency. ignore_conflicts=True will skip existing unique records.
                        # This means if (datetime, locno, source_filename) is a duplicate, it won't be inserted.
                        # Django 2.2+ supports ignore_conflicts.
                        ExcelDataEntry.objects.bulk_create(excel_data_entries, ignore_conflicts=True)
                        total_rows_inserted = len(excel_data_entries) # bulk_create returns None on success, assume all if no error
                        root_logger.info(f"Successfully inserted {total_rows_inserted} rows into ExcelDataEntry.")
                    except IntegrityError as e:
                        root_logger.error(f"Integrity Error during bulk_create (e.g., duplicate entry, check unique_together constraint in model): {e}")
                        # Re-raise to ensure transaction rollback if not handled by ignore_conflicts
                        raise 
                    except Exception as e:
                        root_logger.error(f"General error during bulk_create: {e}")
                        # Re-raise to ensure transaction rollback
                        raise 

            root_logger.info(f"Finished processing and inserting data from '{os.path.basename(file_path)}'.")
            return total_rows_inserted

        except pd.errors.EmptyDataError:
            root_logger.error(f"Error: Excel file '{os.path.basename(file_path)}' is empty or corrupted.")
            return 0
        except Exception as e:
            root_logger.exception(f"Unexpected error during Excel processing for '{os.path.basename(file_path)}': {e}")
            raise # Re-raise other unexpected errors to be caught higher up

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
            # Use 'RFC822.HEADER' for full header or 'BODY.PEEK[HEADER]' for just headers without marking as read
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

                # Fetch the full message only if it's a new email
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
                            root_logger.debug(f"Skipping '{filename}': Filter keyword '{self.attachment_keyword}' not found in filename.")
                            continue

                        # Handle duplicate filenames by appending a counter
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
                            root_logger.info(f"Downloaded: '{os.path.basename(filepath)}' (Size: {file_size_kb:.2f} KB)") # Log actual saved name

                            last_attachment_name = os.path.basename(filepath)
                            last_attachment_size_kb = file_size_kb

                            # Process the downloaded Excel file, passing the latest_email_uid
                            rows_inserted = self._process_excel_file(filepath, email_uid=latest_uid_str)
                            if rows_inserted > 0:
                                root_logger.info(f"Successfully processed '{os.path.basename(filepath)}' and inserted {rows_inserted} data rows.")
                            else:
                                root_logger.info(f"No data rows inserted from '{os.path.basename(filepath)}'.")

                        except Exception as e:
                            root_logger.error(f"Error processing attachment '{filename}': {e}", exc_info=True) # exc_info to log full traceback

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
            self.latest_email_status_in_run = "IMAP_ERROR"
        except Exception as e:
            root_logger.exception(f"An unexpected error occurred: {e}")
            self.latest_email_status_in_run = "UNEXPECTED_ERROR"
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

    # Log an initial message to the stream
    root_logger.info(f"[{timezone.now().strftime('%Y-%m-%d %H:%M:%S')}] Web import triggered.")

    try:
        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'start_import':
                status_message = "Import process initiated..."
                try:
                    processor = EmailProcessorLogic() 
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
                    elif processor.latest_email_status_in_run == "IMAP_ERROR":
                        status_message = "Import failed: IMAP connection error."
                    elif processor.latest_email_status_in_run == "UNEXPECTED_ERROR":
                        status_message = "Import failed: An unexpected error occurred during email processing."
                    else:
                        status_message = "Import process completed with unknown status."

                except Exception as e:
                    status_message = f"Error during import: {e}"
                    root_logger.error(status_message) 

    except Exception as e:
        root_logger.error(f"An unhandled error occurred in the view: {e}", exc_info=True)
        log_output_string += f"\nERROR: An unhandled error occurred in the view function: {e}\n"
    finally:
        # Get the captured log output before removing handler
        log_output_string = log_stream.getvalue()
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
                # Ensure they are timezone-aware, typically UTC if stored as such
                start_of_day = timezone.make_aware(start_of_day, pytz.utc) 
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