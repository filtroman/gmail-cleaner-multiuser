"""
Gmail Delete Operations
-----------------------
Functions for deleting emails and scanning senders, repeated subjects, or individual emails.
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


def _normalize_subject(subject: str) -> tuple[str, str]:
    """Normalize an email subject so repeated threads and calendar invites group together."""
    cleaned = (subject or "").strip()

    while True:
        updated = re.sub(
            r"^(?:re|fwd?|aw|sv)\s*:\s*", "", cleaned, flags=re.IGNORECASE
        )
        if updated == cleaned:
            break
        cleaned = updated.strip()

    invite_prefix = (
        r"^(?:invitation|updated invitation|accepted|declined|tentative|canceled(?: event)?)\s*:"
    )
    if re.match(invite_prefix, cleaned, flags=re.IGNORECASE):
        cleaned = re.sub(
            r"\s+@\s+(?:mon|tue|wed|thu|fri|sat|sun)\b.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "(No Subject)"

    return cleaned.lower(), cleaned


def scan_senders_for_delete(
    limit: int = 1000, filters: Optional[dict] = None, group_by: str = "sender"
):
    """Scan emails and group by sender, repeated subject, or individual email."""
    if limit <= 0:
        state.reset_delete_scan()
        state.delete_scan_status["error"] = "Limit must be greater than 0"
        state.delete_scan_status["done"] = True
        return

    group_by = (group_by or "sender").lower().strip()
    if group_by not in ("sender", "subject", "individual"):
        group_by = "sender"

    state.reset_delete_scan()
    state.delete_scan_status["message"] = "Connecting to GMail..."

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

        groups: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "sender": "",
                "email": "",
                "display_title": "",
                "group_type": group_by,
                "message_id": "",
                "subjects": [],
                "senders_set": [],
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
            size_estimate = int(response.get("sizeEstimate") or 0)

            email_date = None
            for header in headers:
                if header["name"].lower() == "date":
                    email_date = header["value"]
                    break

            if group_by == "individual":
                if not msg_id:
                    return
                key = f"msg:{msg_id}"
                from_display = (
                    f"From: {sender_name} <{sender_email}>"
                    if sender_name and sender_name != sender_email
                    else f"From: {sender_email}"
                )
                entry = groups[key]
                entry["count"] = 1
                entry["sender"] = sender_name
                entry["email"] = key
                entry["message_id"] = msg_id
                entry["display_title"] = subject or "(No Subject)"
                entry["group_type"] = "individual"
                entry["subjects"] = [from_display]
                entry["first_date"] = email_date
                entry["last_date"] = email_date
                entry["message_ids"] = [msg_id]
                entry["total_size"] = size_estimate
            elif group_by == "subject":
                group_key, display_title = _normalize_subject(subject)
                entry = groups[group_key]
                entry["count"] += 1
                entry["email"] = group_key
                entry["display_title"] = entry["display_title"] or display_title
                entry["group_type"] = "subject"
                if msg_id:
                    entry["message_ids"].append(msg_id)
                entry["total_size"] += size_estimate
                if sender_email and sender_email not in entry["senders_set"]:
                    entry["senders_set"].append(sender_email)
                if email_date:
                    if entry["first_date"] is None:
                        entry["first_date"] = email_date
                    entry["last_date"] = email_date
            else:
                if sender_email:
                    entry = groups[sender_email]
                    entry["count"] += 1
                    entry["sender"] = sender_name
                    entry["email"] = sender_email
                    entry["display_title"] = sender_email
                    entry["group_type"] = "sender"
                    if msg_id:
                        entry["message_ids"].append(msg_id)
                    entry["total_size"] += size_estimate
                    if len(entry["subjects"]) < 3:
                        entry["subjects"].append(subject)
                    if email_date:
                        if entry["first_date"] is None:
                            entry["first_date"] = email_date
                        entry["last_date"] = email_date

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

        formatted = []
        for k, v in groups.items():
            if group_by == "subject":
                if v["count"] < 2:
                    continue
                senders_list = v.pop("senders_set", [])
                if senders_list:
                    preview = ", ".join(senders_list[:2])
                    extra = len(senders_list) - 2
                    subtitle = f"From: {preview}" + (
                        f" (+{extra} more)" if extra > 0 else ""
                    )
                else:
                    subtitle = "Multiple senders"
                v["subjects"] = [subtitle]
            else:
                v.pop("senders_set", None)

            formatted.append({"email": k, **v})

        if group_by == "individual":
            sorted_results = sorted(
                formatted,
                key=lambda x: x["total_size"],
                reverse=True,
            )
            label = "emails"
        else:
            sorted_results = sorted(
                formatted,
                key=lambda x: x["count"],
                reverse=True,
            )
            label = "repeated subjects" if group_by == "subject" else "senders"

        state.delete_scan_results = sorted_results
        state.delete_scan_status["message"] = f"Found {len(sorted_results)} {label}"
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
    """Delete emails for a specific sender, repeated subject group, or individual message."""
    if not sender or not sender.strip():
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No sender or item specified",
        }

    sender = sender.strip()

    cached_group = next(
        (
            r
            for r in state.delete_scan_results
            if r.get("email") == sender
            and r.get("group_type") in ("subject", "individual")
        ),
        None,
    )

    if cached_group is not None:
        service, error = get_gmail_service()
        if error:
            return {"success": False, "deleted": 0, "size_freed": 0, "message": error}

        ids = cached_group.get("message_ids", [])
        size_freed = cached_group.get("total_size", 0)
        if not ids:
            return {
                "success": True,
                "deleted": 0,
                "size_freed": 0,
                "message": "No emails found",
            }

        try:
            deleted = 0
            batch_size = 100
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
                "message": f"Moved {deleted} email(s) to GMail Trash",
            }
        except Exception as e:
            return {"success": False, "deleted": 0, "size_freed": 0, "message": str(e)}

    # Standard sender mode
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
            "message": f"Moved {deleted} emails to GMail Trash",
        }

    except Exception as e:
        return {"success": False, "deleted": 0, "size_freed": 0, "message": str(e)}


def delete_emails_bulk(senders: list[str]) -> dict:
    """Delete emails from multiple senders, subject groups, or individual messages."""
    if not senders:
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No items specified",
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
    """Delete emails from multiple senders, subject groups, or individual messages."""
    state.reset_delete_bulk()

    if not senders or not isinstance(senders, list):
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["error"] = "No items specified"
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

    cached_groups = {
        r.get("email"): r
        for r in state.delete_scan_results
        if r.get("group_type") in ("subject", "individual")
    }

    for i, sender in enumerate(senders):
        state.delete_bulk_status["current_sender"] = i + 1
        state.delete_bulk_status["progress"] = int((i / total_senders) * 40)

        if sender in cached_groups:
            group = cached_groups[sender]
            state.delete_bulk_status["message"] = (
                f"Collecting '{group.get('display_title', sender)}'..."
            )
            all_message_ids.extend(group.get("message_ids", []))
            continue

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

    all_message_ids = list(dict.fromkeys(all_message_ids))

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
