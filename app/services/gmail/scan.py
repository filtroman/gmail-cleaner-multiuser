"""
GMail Scanning Operations
--------------------------
Functions for scanning emails to find unsubscribe links.
"""

import logging
from collections import defaultdict
from email.utils import parsedate_to_datetime
from typing import Optional

from app.core import state
from app.services.auth import get_gmail_service
from app.services.gmail.helpers import (
    build_gmail_query,
    execute_batch_with_retry,
    execute_with_backoff,
    get_unsubscribe_from_headers,
    get_sender_info,
    get_subject,
)

logger = logging.getLogger(__name__)


def scan_emails(limit: int = 500, filters: Optional[dict] = None):
    """Scan emails for unsubscribe links using GMail Batch API with rate-limit protection."""
    if limit <= 0:
        state.reset_scan()
        state.scan_status["error"] = "Limit must be greater than 0"
        state.scan_status["done"] = True
        return

    state.reset_scan()
    state.scan_status["message"] = "Connecting to GMail..."

    service, error = get_gmail_service()
    if error:
        state.scan_status["error"] = error
        state.scan_status["done"] = True
        return

    try:
        state.scan_status["message"] = "Fetching email list..."

        query = build_gmail_query(filters)
        message_ids = []
        page_token = None

        while len(message_ids) < limit:
            list_params = {
                "userId": "me",
                "maxResults": min(500, limit - len(message_ids)),
            }
            if page_token:
                list_params["pageToken"] = page_token
            if query:
                list_params["q"] = query

            result = execute_with_backoff(
                lambda p=list_params: service.users().messages().list(**p).execute(),
                status_dict=state.scan_status,
            )

            messages = result.get("messages", [])
            message_ids.extend([m["id"] for m in messages])

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        if not message_ids:
            state.scan_status["message"] = "No emails found"
            state.scan_status["done"] = True
            return

        total = len(message_ids)
        state.scan_status["message"] = f"Found {total} emails. Scanning..."

        unsubscribe_data: dict[str, dict] = defaultdict(
            lambda: {
                "link": None,
                "count": 0,
                "subjects": [],
                "type": None,
                "sender": "",
                "email": "",
                "first_date": None,
                "last_date": None,
            }
        )

        def process_message(response: dict) -> None:
            headers = response.get("payload", {}).get("headers", [])
            unsub_link, unsub_type = get_unsubscribe_from_headers(headers)

            if unsub_link:
                sender_name, sender_email = get_sender_info(headers)
                subject = get_subject(headers)
                domain = (
                    sender_email.split("@")[-1] if "@" in sender_email else sender_email
                )

                email_date = None
                for header in headers:
                    if header["name"].lower() == "date":
                        email_date = header["value"]
                        break

                unsubscribe_data[domain]["link"] = unsub_link
                unsubscribe_data[domain]["count"] += 1
                unsubscribe_data[domain]["type"] = unsub_type
                unsubscribe_data[domain]["sender"] = sender_name
                unsubscribe_data[domain]["email"] = sender_email
                if len(unsubscribe_data[domain]["subjects"]) < 3:
                    unsubscribe_data[domain]["subjects"].append(subject)

                if email_date:
                    try:
                        msg_datetime = parsedate_to_datetime(email_date)
                        current_first = unsubscribe_data[domain]["first_date"]
                        current_last = unsubscribe_data[domain]["last_date"]

                        if current_first is None:
                            unsubscribe_data[domain]["first_date"] = email_date
                        else:
                            try:
                                first_datetime = parsedate_to_datetime(current_first)
                                if msg_datetime < first_datetime:
                                    unsubscribe_data[domain]["first_date"] = email_date
                            except (ValueError, TypeError):
                                if email_date < current_first:
                                    unsubscribe_data[domain]["first_date"] = email_date

                        if current_last is None:
                            unsubscribe_data[domain]["last_date"] = email_date
                        else:
                            try:
                                last_datetime = parsedate_to_datetime(current_last)
                                if msg_datetime > last_datetime:
                                    unsubscribe_data[domain]["last_date"] = email_date
                            except (ValueError, TypeError):
                                if email_date > current_last:
                                    unsubscribe_data[domain]["last_date"] = email_date
                    except (ValueError, TypeError):
                        pass

        def update_progress(completed: int, total_count: int) -> None:
            state.scan_status["progress"] = int(completed / total_count * 100)
            state.scan_status["message"] = (
                f"Scanned {completed}/{total_count} emails ({len(unsubscribe_data)} found)"
            )

        execute_batch_with_retry(
            service=service,
            items=message_ids,
            build_request=lambda msg_id: service.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=[
                    "From",
                    "Subject",
                    "Date",
                    "List-Unsubscribe",
                    "List-Unsubscribe-Post",
                ],
            ),
            on_success=process_message,
            status_dict=state.scan_status,
            batch_size=50,
            batch_delay=1.0,
            progress_cb=update_progress,
        )

        sorted_results = sorted(
            [
                {
                    "domain": k,
                    "link": v["link"],
                    "count": v["count"],
                    "subjects": v["subjects"],
                    "type": v["type"],
                    "sender": v.get("sender", ""),
                    "email": v.get("email", ""),
                    "first_date": v.get("first_date"),
                    "last_date": v.get("last_date"),
                }
                for k, v in unsubscribe_data.items()
            ],
            key=lambda x: x.get("count", 0) or 0,
            reverse=True,
        )

        state.scan_results = sorted_results
        state.scan_status["message"] = f"Found {len(state.scan_results)} subscriptions"
        state.scan_status["done"] = True

    except Exception as e:
        state.scan_status["error"] = str(e)
        state.scan_status["done"] = True


def get_scan_status() -> dict:
    """Get current scan status."""
    return state.scan_status.copy()


def get_scan_results() -> list:
    """Get scan results."""
    return state.scan_results.copy()
