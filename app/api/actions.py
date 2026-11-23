"""
Actions API Routes
------------------
POST endpoints for triggering operations.
"""

import logging
from functools import partial
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.core import settings
from app.models import (
    ScanRequest,
    MarkReadRequest,
    DeleteScanRequest,
    UnsubscribeRequest,
    DeleteEmailsRequest,
    DeleteBulkRequest,
    DownloadEmailsRequest,
    CreateLabelRequest,
    ApplyLabelRequest,
    RemoveLabelRequest,
    ArchiveRequest,
    MarkImportantRequest,
    SwitchAccountRequest,
    RemoveAccountRequest,
)
from app.services import (
    scan_emails,
    create_oauth_url,
    switch_account,
    sign_out,
    unsubscribe_single,
    mark_emails_as_read,
    scan_senders_for_delete,
    delete_emails_by_sender,
    delete_emails_bulk_background,
    download_emails_background,
    create_label,
    delete_label,
    apply_label_to_senders_background,
    remove_label_from_senders_background,
    archive_emails_background,
    mark_important_background,
)

router = APIRouter(prefix="/api", tags=["Actions"])
logger = logging.getLogger(__name__)


@router.post("/scan")
async def api_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    """Start email scan for unsubscribe links."""
    filters_dict = (
        request.filters.model_dump(exclude_none=True) if request.filters else None
    )
    background_tasks.add_task(scan_emails, request.limit, filters_dict)
    return {"status": "started"}


@router.post("/sign-in")
async def api_sign_in(request: Request):
    """Generate Google OAuth authorization URL and return it to the browser."""
    try:
        if settings.oauth_redirect_uri:
            redirect_uri = settings.oauth_redirect_uri.strip()
        else:
            proto = (
                request.headers.get("x-forwarded-proto", request.url.scheme)
                .split(",")[0]
                .strip()
            )
            host = (
                request.headers.get(
                    "x-forwarded-host",
                    request.headers.get("host", f"{settings.oauth_host}:{settings.port}"),
                )
                .split(",")[0]
                .strip()
            )
            redirect_uri = f"{proto}://{host}/oauth/callback"

        auth_url = create_oauth_url(redirect_uri)
        return {"status": "redirect", "auth_url": auth_url}
    except Exception as e:
        logger.exception("Error starting sign-in")
        return {"error": str(e)}


@router.post("/accounts/switch")
async def api_switch_account(request: SwitchAccountRequest):
    """Switch the active Gmail account."""
    try:
        return switch_account(request.email)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Error switching account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to switch account",
        ) from e


@router.post("/sign-out")
async def api_sign_out(request: RemoveAccountRequest | None = None):
    """Sign out of a specific account or the current active account."""
    try:
        email = request.email if request else None
        return sign_out(email)
    except Exception as e:
        logger.exception("Error during sign-out")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sign out",
        ) from e


@router.post("/unsubscribe")
async def api_unsubscribe(request: UnsubscribeRequest):
    """Unsubscribe from a single sender."""
    try:
        return unsubscribe_single(request.domain, request.link)
    except Exception as e:
        logger.exception("Error during unsubscribe")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unsubscribe",
        ) from e


@router.post("/mark-read")
async def api_mark_read(request: MarkReadRequest, background_tasks: BackgroundTasks):
    """Mark emails as read."""
    filters_dict = (
        request.filters.model_dump(exclude_none=True) if request.filters else None
    )
    background_tasks.add_task(mark_emails_as_read, request.count, filters_dict)
    return {"status": "started"}


@router.post("/delete-scan")
async def api_delete_scan(
    request: DeleteScanRequest, background_tasks: BackgroundTasks
):
    """Scan senders or repeated subjects for bulk delete."""
    filters_dict = (
        request.filters.model_dump(exclude_none=True) if request.filters else None
    )
    background_tasks.add_task(
        scan_senders_for_delete, request.limit, filters_dict, request.group_by
    )
    return {"status": "started"}


@router.post("/delete-emails")
async def api_delete_emails(request: DeleteEmailsRequest):
    """Delete emails from a specific sender or repeated subject group."""
    if not request.sender or not request.sender.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sender or group key is required",
        )
    try:
        return delete_emails_by_sender(request.sender)
    except Exception as e:
        logger.exception("Error deleting emails")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete emails",
        ) from e


@router.post("/delete-emails-bulk")
async def api_delete_emails_bulk(
    request: DeleteBulkRequest, background_tasks: BackgroundTasks
):
    """Delete emails from multiple senders or subject groups."""
    background_tasks.add_task(delete_emails_bulk_background, request.senders)
    return {"status": "started"}


@router.post("/download-emails")
async def api_download_emails(
    request: DownloadEmailsRequest, background_tasks: BackgroundTasks
):
    """Start downloading email metadata for selected senders."""
    background_tasks.add_task(download_emails_background, request.senders)
    return {"status": "started"}


# ----- Label Management Endpoints -----


@router.post("/labels")
async def api_create_label(request: CreateLabelRequest):
    """Create a new Gmail label."""
    try:
        return create_label(request.name)
    except Exception as e:
        logger.exception("Error creating label")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create label",
        ) from e


@router.delete("/labels/{label_id}")
async def api_delete_label(label_id: str):
    """Delete a Gmail label."""
    if not label_id or not label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    try:
        return delete_label(label_id)
    except Exception as e:
        logger.exception("Error deleting label")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete label",
        ) from e


@router.post("/apply-label")
async def api_apply_label(
    request: ApplyLabelRequest, background_tasks: BackgroundTasks
):
    """Apply a label to emails from selected senders."""
    if not request.label_id or not request.label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        apply_label_to_senders_background, request.label_id, request.senders
    )
    return {"status": "started"}


@router.post("/remove-label")
async def api_remove_label(
    request: RemoveLabelRequest, background_tasks: BackgroundTasks
):
    """Remove a label from emails from selected senders."""
    if not request.label_id or not request.label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        remove_label_from_senders_background, request.label_id, request.senders
    )
    return {"status": "started"}


@router.post("/archive")
async def api_archive(request: ArchiveRequest, background_tasks: BackgroundTasks):
    """Archive emails from selected senders (remove from inbox)."""
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(archive_emails_background, request.senders)
    return {"status": "started"}


@router.post("/mark-important")
async def api_mark_important(
    request: MarkImportantRequest, background_tasks: BackgroundTasks
):
    """Mark/unmark emails from selected senders as important."""
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        partial(mark_important_background, request.senders, important=request.important)
    )
    return {"status": "started"}
