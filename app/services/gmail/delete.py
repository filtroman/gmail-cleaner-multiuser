"""
Gmail Delete Operations
-----------------------
Functions for deleting emails and scanning senders.
"""

import logging
import re
from collections import defaultdict
from typing import Optional

from app.core import state
from app.services.auth import get_gmail_service
from app.services.gmail.helpers import (
    build_gmail_query,
    execute_batch_with_retry,
    execute_with_backoff,
    get_sender_info,
    get_subject,
)

logger = logging.getLogger(__name__)


def scan_senders_for_delete(limit: int = 1000, filters: Optional[dict] = None):
    """Scan emails and group by sender for bulk delete (with rate-limit protection)."""
    if limit <= 0:
        state.reset_delete_scan()
        state.delete_scan_status["error"] = "Limit must be greater than 0"
        state.delete_scan_status["done"] = True
        return

    state.reset_delete_scan()
    state.delete_scan_status["message"] = "Connecting to Gmail..."

    service, error = get_gmail_service()
    if error:
        state.delete_scan_status["error"] = error
        state.delete_scan_status["done"] = True
        return

    try:
        state.delete_scan_status["message"] = "Fetching emails..."

        query = build_gmail_query(filters)

        results = execute_with_backoff(
            lambda: service.users()
            .messages()
            .list(userId="me", maxResults=min(limit, 500), q=query or None)
            .execute(),
            status_dict=state.delete_scan_status,
        )

        messages = results.get("messages", [])

        while "nextPageToken" in results and len(messages) < limit:
            page_token = results["nextPageToken"]
            results = execute_with_backoff(
                lambda pt=page_token: service.users()
                .messages()
                .list(
                    userId="me",
                    maxResults=min(limit - len(messages), 500),
                    pageToken=pt,
                    q=query or None,
                )
                .execute(),
                status_dict=state.delete_scan_status,
            )
            messages.extend(results.get("messages", []))

        messages = messages[:limit]
        total = len(messages)

        if total == 0:
            state.delete_scan_status["message"] = "No emails found"
            state.delete_scan_status["done"] = True
            return

        state.delete_scan_status["message"] = f"Scanning {total} emails..."

        sender_counts: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "sender": "",
                "email": "",
                "subjects": [],
                "first_date": None,
                "last_date": None,
                "message_ids": [],
                "total_size": 0,
            }
        )

        def process_message(response: dict) -> None:
            headers = response.get("payload", {}).get("headers", [])
            sender_name, sender_email = get_sender_info(headers)
            subject = get_subject(headers)
            msg_id = response.get("id", "")
            size_estimate = response.get("sizeEstimate", 0)

            email_date = None
            for header in headers:
                if header["name"].lower() == "date":
                    email_date = header["value"]
                    break

            if sender_email:
                sender_counts[sender_email]["count"] += 1
                sender_counts[sender_email]["sender"] = sender_name
                sender_counts[sender_email]["email"] = sender_email
                sender_counts[sender_email]["message_ids"].append(msg_id)
                sender_counts[sender_email]["total_size"] += size_estimate
                if len(sender_counts[sender_email]["subjects"]) < 3:
                    sender_counts[sender_email]["subjects"].append(subject)

                if email_date:
                    if sender_counts[sender_email]["first_date"] is None:
                        sender_counts[sender_email]["first_date"] = email_date
                    sender_counts[sender_email]["last_date"] = email_date

        def update_progress(completed: int, total_count: int) -> None:
            state.delete_scan_status["progress"] = int(completed / total_count * 100)
            state.delete_scan_status["message"] = (
                f"Scanned {completed}/{total_count} emails"
            )

        execute_batch_with_retry(
            service=service,
            items=messages,
            build_request=lambda msg_data: service.users()
            .messages()
            .get(
                userId="me",
                id=msg_data["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ),
            on_success=process_message,
            status_dict=state.delete_scan_status,
            batch_size=50,
            batch_delay=1.0,
            progress_cb=update_progress,
        )

        sorted_senders = sorted(
            [{"email": k, **v} for k, v in sender_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        state.delete_scan_results = sorted_senders
        state.delete_scan_status["message"] = f"Found {len(sorted_senders)} senders"
        state.delete_scan_status["done"] = True

    except Exception as e:
        state.delete_scan_status["error"] = str(e)
        state.delete_scan_status["done"] = True


def get_delete_scan_status() -> dict:
    """Get delete scan status."""
    return state.delete_scan_status.copy()


def get_delete_scan_results() -> list:
    """Get delete scan results."""
    return state.delete_scan_results.copy()


def delete_emails_by_sender(sender: str) -> dict:
    """Delete all emails from a specific sender."""
    if not sender or not sender.strip():
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No sender specified",
        }

    sender = sender.strip()
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    domain_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"

    if not (re.match(email_pattern, sender) or re.match(domain_pattern, sender)):
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "Invalid sender format. Must be a valid email address or domain.",
        }

    size_freed = 0
    for r in state.delete_scan_results:
        if r.get("email") == sender:
            size_freed = r.get("total_size", 0)
            break

    service, error = get_gmail_service()
    if error:
        return {"success": False, "deleted": 0, "size_freed": 0, "message": error}

    try:
        query = f"from:{sender}"
        results = execute_with_backoff(
            lambda: service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500)
            .execute()
        )
        messages = results.get("messages", [])

        while "nextPageToken" in results:
            page_token = results["nextPageToken"]
            results = execute_with_backoff(
                lambda pt=page_token: service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=500,
                    pageToken=pt,
                )
                .execute()
            )
            messages.extend(results.get("messages", []))

        if not messages:
            return {
                "success": True,
                "deleted": 0,
                "size_freed": 0,
                "message": "No emails found",
            }

        ids = [msg["id"] for msg in messages]
        batch_size = 100
        deleted = 0

        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            execute_with_backoff(
                lambda b=batch: service.users()
                .messages()
                .batchModify(userId="me", body={"ids": b, "addLabelIds": ["TRASH"]})
                .execute()
            )
            deleted += len(batch)

        state.delete_scan_results = [
            r for r in state.delete_scan_results if r.get("email") != sender
        ]

        return {
            "success": True,
            "deleted": deleted,
            "size_freed": size_freed,
            "message": f"Moved {deleted} emails to trash",
        }

    except Exception as e:
        return {"success": False, "deleted": 0, "size_freed": 0, "message": str(e)}


def delete_emails_bulk(senders: list[str]) -> dict:
    """Delete emails from multiple senders."""
    if not senders:
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No senders specified",
        }

    total_deleted = 0
    total_size_freed = 0
    errors = []

    for sender in senders:
        result = delete_emails_by_sender(sender)
        if result["success"]:
            total_deleted += result["deleted"]
            total_size_freed += result.get("size_freed", 0)
        else:
            errors.append(f"{sender}: {result['message']}")

    if errors:
        return {
            "success": len(errors) < len(senders),
            "deleted": total_deleted,
            "size_freed": total_size_freed,
            "message": f"Deleted {total_deleted} emails. Errors: {'; '.join(errors[:3])}",
        }

    if total_deleted == 0:
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No emails found to delete",
        }
    return {
        "success": True,
        "deleted": total_deleted,
        "size_freed": total_size_freed,
        "message": f"Deleted {total_deleted} emails",
    }


def delete_emails_bulk_background(senders: list[str]) -> None:
    """Delete emails from multiple senders with progress updates (background task)."""
    state.reset_delete_bulk()

    if not senders or not isinstance(senders, list):
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["error"] = "No senders specified"
        return

    total_senders = len(senders)
    state.delete_bulk_status["total_senders"] = total_senders
    state.delete_bulk_status["message"] = "Collecting emails to delete..."

    service, error = get_gmail_service()
    if error:
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["error"] = error
        return

    all_message_ids = []
    errors = []

    for i, sender in enumerate(senders):
        state.delete_bulk_status["current_sender"] = i + 1
        state.delete_bulk_status["progress"] = int((i / total_senders) * 40)
        state.delete_bulk_status["message"] = f"Finding emails from {sender}..."

        try:
            query = f"from:{sender}"
            results = execute_with_backoff(
                lambda q=query: service.users()
                .messages()
                .list(userId="me", q=q, maxResults=500)
                .execute(),
                status_dict=state.delete_bulk_status,
            )
            messages = results.get("messages", [])

            while "nextPageToken" in results:
                page_token = results["nextPageToken"]
                results = execute_with_backoff(
                    lambda q=query, pt=page_token: service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=q,
                        maxResults=500,
                        pageToken=pt,
                    )
                    .execute(),
                    status_dict=state.delete_bulk_status,
                )
                messages.extend(results.get("messages", []))

            all_message_ids.extend([msg["id"] for msg in messages])
        except Exception as e:
            errors.append(f"{sender}: {str(e)}")

    if not all_message_ids:
        state.delete_bulk_status["progress"] = 100
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["message"] = "No emails found to delete"
        return

    total_emails = len(all_message_ids)
    state.delete_bulk_status["message"] = f"Deleting {total_emails} emails..."

    batch_size = 1000
    deleted = 0

    try:
        for i in range(0, total_emails, batch_size):
            batch = all_message_ids[i : i + batch_size]
            execute_with_backoff(
                lambda b=batch: service.users()
                .messages()
                .batchModify(userId="me", body={"ids": b, "addLabelIds": ["TRASH"]})
                .execute(),
                status_dict=state.delete_bulk_status,
            )
            deleted += len(batch)
            state.delete_bulk_status["deleted_count"] = deleted
            state.delete_bulk_status["progress"] = 40 + int(
                (deleted / total_emails) * 60
            )
            state.delete_bulk_status["message"] = (
                f"Deleted {deleted}/{total_emails} emails..."
            )
    except Exception as e:
        errors.append(f"Batch delete error: {str(e)}")

    state.delete_scan_results = [
        r for r in state.delete_scan_results if r.get("email") not in senders
    ]

    state.delete_bulk_status["progress"] = 100
    state.delete_bulk_status["done"] = True
    state.delete_bulk_status["deleted_count"] = deleted

    if errors:
        state.delete_bulk_status["error"] = f"Some errors: {'; '.join(errors[:3])}"
        state.delete_bulk_status["message"] = (
            f"Deleted {deleted} emails with some errors"
        )
    else:
        state.delete_bulk_status["message"] = f"Successfully deleted {deleted} emails"


def get_delete_bulk_status() -> dict:
    """Get delete bulk operation status."""
    return state.delete_bulk_status.copy()
