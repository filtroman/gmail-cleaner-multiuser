"""
Google Storage Analyzer Service
-------------------------------
Analyzes Google One storage across owned Drive files, Drive Trash, Hidden Drive/App Data, and GMail.
"""

import json
import logging
from googleapiclient.errors import HttpError

from app.services.auth import get_drive_service, get_gmail_service
from app.services.gmail.helpers import (
    execute_batch_with_retry,
    execute_with_backoff,
    get_sender_info,
    get_subject,
)

logger = logging.getLogger(__name__)


def _format_http_error(err: HttpError) -> str:
    """Extract clean human-readable message from Google HttpError."""
    try:
        payload = json.loads(err.content.decode("utf-8"))
        msg = payload.get("error", {}).get("message")
        if msg:
            return f"Google API ({err.resp.status}): {msg}"
    except Exception:
        pass
    return str(err)


def analyze_storage(min_email_mb: int = 5) -> dict:
    """Analyze Drive quota, largest owned Drive files, hidden Drive usage, and largest GMail emails."""
    drive_service, drive_err = get_drive_service()
    if drive_err == "NEEDS_DRIVE_SCOPE":
        return {"needs_drive_scope": True, "error": "Drive permission required."}
    if drive_err:
        return {"error": drive_err}

    gmail_service, gmail_err = get_gmail_service()
    if gmail_err:
        return {"error": gmail_err}

    try:
        # 1. Overall account storage quota
        about = execute_with_backoff(
            lambda: drive_service.about()
            .get(fields="storageQuota,user")
            .execute()
        )
        quota = about.get("storageQuota", {})
        user_info = about.get("user", {})

        total_limit = int(quota.get("limit") or 0)
        total_usage = int(quota.get("usage") or 0)
        usage_in_drive = int(quota.get("usageInDrive") or 0)
        usage_in_drive_trash = int(quota.get("usageInDriveTrash") or 0)

        # 2. Fetch ONLY files in personal Drive owned by 'me'
        all_drive_files = []
        owned_active_sum = 0
        owned_trashed_sum = 0
        page_token = None

        for _ in range(25):
            list_kwargs = {
                "q": "'me' in owners",
                "spaces": "drive",
                "corpora": "user",
                "includeItemsFromAllDrives": False,
                "supportsAllDrives": False,
                "pageSize": 1000,
                "orderBy": "quotaBytesUsed desc",
                "fields": (
                    "nextPageToken, files(id, name, mimeType, size, "
                    "quotaBytesUsed, modifiedTime, trashed, webViewLink, "
                    "ownedByMe, capabilities(canTrash))"
                ),
            }
            if page_token:
                list_kwargs["pageToken"] = page_token

            resp = execute_with_backoff(
                lambda kw=list_kwargs: drive_service.files().list(**kw).execute()
            )
            files = resp.get("files", [])
            if not files:
                break

            hit_zero = False
            for f in files:
                # Strictly require ownership by me
                if f.get("ownedByMe") is not True:
                    continue

                q_bytes = int(f.get("quotaBytesUsed") or 0)
                if q_bytes <= 0:
                    hit_zero = True
                    continue

                is_trashed = bool(f.get("trashed", False))
                if is_trashed:
                    owned_trashed_sum += q_bytes
                else:
                    owned_active_sum += q_bytes

                can_trash = bool(f.get("capabilities", {}).get("canTrash", False))
                if not can_trash and not is_trashed:
                    continue

                all_drive_files.append(
                    {
                        "id": f.get("id"),
                        "name": f.get("name", "(Unnamed)"),
                        "mime_type": f.get("mimeType", ""),
                        "size": q_bytes,
                        "modified_time": f.get("modifiedTime", ""),
                        "trashed": is_trashed,
                        "web_link": f.get("webViewLink", ""),
                    }
                )

            page_token = resp.get("nextPageToken")
            if not page_token or hit_zero:
                break

        total_owned_visible = owned_active_sum + owned_trashed_sum
        hidden_drive_bytes = max(0, usage_in_drive - total_owned_visible)
        effective_drive_trash = (
            usage_in_drive_trash if usage_in_drive_trash > 0 else owned_trashed_sum
        )
        gmail_photos_other_bytes = max(0, total_usage - usage_in_drive)

        # 3. GMail Trash & Spam counts (2 tiny 1-unit calls)
        gmail_trash_count = 0
        gmail_spam_count = 0
        try:
            trash_label = (
                gmail_service.users().labels().get(userId="me", id="TRASH").execute()
            )
            gmail_trash_count = int(trash_label.get("messagesTotal") or 0)
        except Exception:
            pass
        try:
            spam_label = (
                gmail_service.users().labels().get(userId="me", id="SPAM").execute()
            )
            gmail_spam_count = int(spam_label.get("messagesTotal") or 0)
        except Exception:
            pass

        # 4. Largest GMail emails (e.g. larger:5M, top 100)
        min_email_mb = max(1, min(int(min_email_mb), 50))
        large_emails = []
        msg_list = execute_with_backoff(
            lambda: gmail_service.users()
            .messages()
            .list(userId="me", q=f"larger:{min_email_mb}M", maxResults=100)
            .execute()
        )
        msg_refs = msg_list.get("messages", [])

        if msg_refs:
            def on_email(response: dict) -> None:
                headers = response.get("payload", {}).get("headers", [])
                sender_name, sender_email = get_sender_info(headers)
                subject = get_subject(headers)
                email_date = ""
                for h in headers:
                    if h["name"].lower() == "date":
                        email_date = h["value"]
                        break
                large_emails.append(
                    {
                        "id": response.get("id", ""),
                        "sender_name": sender_name,
                        "sender_email": sender_email,
                        "subject": subject,
                        "date": email_date,
                        "size": int(response.get("sizeEstimate") or 0),
                    }
                )

            execute_batch_with_retry(
                service=gmail_service,
                items=msg_refs,
                build_request=lambda m: gmail_service.users()
                .messages()
                .get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ),
                on_success=on_email,
                batch_size=50,
                batch_delay=0.5,
            )

            large_emails.sort(key=lambda x: x["size"], reverse=True)

        return {
            "needs_drive_scope": False,
            "email": user_info.get("emailAddress"),
            "quota": {
                "limit": total_limit,
                "usage": total_usage,
                "percent": round((total_usage / total_limit) * 100, 1)
                if total_limit > 0
                else 0,
                "drive_total": usage_in_drive,
                "drive_visible": owned_active_sum,
                "drive_trash": effective_drive_trash,
                "drive_hidden": hidden_drive_bytes,
                "gmail_photos_other": gmail_photos_other_bytes,
            },
            "gmail_counts": {
                "trash_messages": gmail_trash_count,
                "spam_messages": gmail_spam_count,
            },
            "drive_files": all_drive_files[:100],
            "large_emails": large_emails,
        }

    except HttpError as e:
        msg = str(e)
        if e.resp.status == 403 and (
            "insufficientpermissions" in msg.lower()
            or "accessnotconfigured" in msg.lower()
        ):
            if "accessnotconfigured" in msg.lower():
                return {
                    "error": "Google Drive API is not enabled in your Google Cloud project yet."
                }
            return {"needs_drive_scope": True, "error": "Drive permission required."}
        logger.exception("Drive/Storage API error")
        return {"error": _format_http_error(e)}
    except Exception as e:
        logger.exception("Error analyzing storage")
        return {"error": str(e)}


def trash_drive_file(file_id: str) -> dict:
    """Move a Google Drive file to Trash."""
    drive_service, err = get_drive_service()
    if err:
        return {"success": False, "message": err}
    try:
        execute_with_backoff(
            lambda: drive_service.files()
            .update(fileId=file_id, body={"trashed": True})
            .execute()
        )
        return {"success": True, "message": "Moved Drive file to Trash"}
    except HttpError as e:
        logger.warning(f"Failed to trash Drive file {file_id}: {e}")
        return {"success": False, "message": _format_http_error(e)}
    except Exception as e:
        logger.exception(f"Unexpected error trashing Drive file {file_id}")
        return {"success": False, "message": str(e)}


def empty_drive_trash() -> dict:
    """Permanently empty Google Drive Trash."""
    drive_service, err = get_drive_service()
    if err:
        return {"success": False, "message": err}
    try:
        execute_with_backoff(lambda: drive_service.files().emptyTrash().execute())
        return {"success": True, "message": "Google Drive Trash emptied"}
    except HttpError as e:
        logger.warning(f"Failed to empty Drive Trash: {e}")
        return {"success": False, "message": _format_http_error(e)}
    except Exception as e:
        logger.exception("Unexpected error emptying Drive Trash")
        return {"success": False, "message": str(e)}


def trash_gmail_message(message_id: str) -> dict:
    """Move a single large GMail message to Trash."""
    gmail_service, err = get_gmail_service()
    if err:
        return {"success": False, "message": err}
    try:
        execute_with_backoff(
            lambda: gmail_service.users()
            .messages()
            .modify(userId="me", id=message_id, body={"addLabelIds": ["TRASH"]})
            .execute()
        )
        return {"success": True, "message": "Moved email to GMail Trash"}
    except HttpError as e:
        logger.warning(f"Failed to trash GMail message {message_id}: {e}")
        return {"success": False, "message": _format_http_error(e)}
    except Exception as e:
        logger.exception(f"Unexpected error trashing GMail message {message_id}")
        return {"success": False, "message": str(e)}
