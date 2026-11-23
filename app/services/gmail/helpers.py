"""
GMail Service Helpers
---------------------
Shared utility functions: security, filters, email parsing, and rate-limit retries.
"""

import ipaddress
import logging
import random
import re
import socket
import time
from typing import Any, Callable, Optional, Union
from urllib.parse import urlparse

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def is_retryable_gmail_error(err: Exception) -> bool:
    """Return True if an exception is a GMail rate limit or transient server error."""
    if isinstance(err, HttpError):
        status = getattr(err.resp, "status", None)
        if status in (429, 500, 502, 503, 504):
            return True
        if status == 403:
            msg = str(err).lower()
            return (
                "ratelimitexceeded" in msg
                or "userratelimitexceeded" in msg
                or "quota exceeded" in msg
            )
    return False


def execute_with_backoff(
    request_fn: Callable[[], Any],
    max_retries: int = 6,
    status_dict: Optional[dict] = None,
) -> Any:
    """Execute a GMail API request with exponential backoff on rate limits."""
    for attempt in range(max_retries + 1):
        try:
            return request_fn()
        except Exception as e:
            if not is_retryable_gmail_error(e) or attempt >= max_retries:
                raise

            wait = min(60.0, 3.0 * (2**attempt)) + random.uniform(0.5, 2.0)
            logger.warning(
                f"GMail API rate limit/transient error (attempt {attempt + 1}/{max_retries}): "
                f"sleeping {wait:.1f}s - {e}"
            )
            if status_dict is not None:
                status_dict["message"] = (
                    f"GMail rate limit hit — pausing {int(wait)}s before retrying..."
                )
            time.sleep(wait)


def execute_batch_with_retry(
    service: Any,
    items: list,
    build_request: Callable[[Any], Any],
    on_success: Callable[[Any], None],
    status_dict: Optional[dict] = None,
    batch_size: int = 50,
    batch_delay: float = 1.0,
    max_retries: int = 6,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Execute GMail batch requests with gentle pacing and automatic per-item retries."""
    total = len(items)
    completed = 0

    for i in range(0, total, batch_size):
        to_send = items[i : i + batch_size]

        for attempt in range(max_retries + 1):
            retry_items = []

            def make_callback(current_items: list, retry_bucket: list):
                def _cb(request_id, response, exception):
                    nonlocal completed
                    if exception:
                        if is_retryable_gmail_error(exception):
                            retry_bucket.append(current_items[int(request_id)])
                        else:
                            logger.warning(
                                f"Skipping message due to non-retryable error: {exception}"
                            )
                            completed += 1
                        return

                    on_success(response)
                    completed += 1

                return _cb

            batch = service.new_batch_http_request(
                callback=make_callback(to_send, retry_items)
            )
            for idx, item in enumerate(to_send):
                batch.add(build_request(item), request_id=str(idx))

            execute_with_backoff(batch.execute, max_retries=max_retries, status_dict=status_dict)

            if not retry_items:
                break

            if attempt >= max_retries:
                logger.error(
                    f"Dropping {len(retry_items)} items after {max_retries} rate-limit retries"
                )
                completed += len(retry_items)
                break

            wait = min(60.0, 4.0 * (2**attempt)) + random.uniform(0.5, 2.0)
            logger.warning(
                f"Batch hit GMail rate limit on {len(retry_items)} items — pausing {wait:.1f}s"
            )
            if status_dict is not None:
                status_dict["message"] = (
                    f"GMail rate limit hit — pausing {int(wait)}s ({completed}/{total} done)..."
                )
            time.sleep(wait)
            to_send = retry_items

        if progress_cb is not None:
            progress_cb(completed, total)

        if i + batch_size < total and batch_delay > 0:
            time.sleep(batch_delay)


def validate_unsafe_url(url: str) -> str:
    """
    Validate URL to prevent SSRF.
    Checks scheme and resolves hostname to ensure it's not a local/private IP.
    """
    try:
        parsed = urlparse(url)
    except Exception as err:
        raise ValueError("Invalid URL format") from err

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL scheme. Only HTTP and HTTPS are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: No hostname found.")

    validated_ips = []
    try:
        addr_infos = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]

            try:
                ip = ipaddress.ip_address(ip_str)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_unspecified
                ):
                    raise ValueError(f"Blocked restricted IP: {ip_str}")

                validated_ips.append(ip_str)
            except ValueError as err:
                raise ValueError(f"Invalid or restricted IP address: {ip_str}") from err

        if not validated_ips:
            raise ValueError(
                f"Could not resolve any valid IP addresses for hostname: {hostname}"
            )

    except socket.gaierror as err:
        raise ValueError(f"Could not resolve hostname: {hostname}") from err
    except ValueError:
        raise

    return url


def build_gmail_query(filters: Optional[Union[dict, Any]] = None) -> str:
    """Build GMail search query from filter parameters."""
    if not filters:
        return ""

    if hasattr(filters, "model_dump"):
        filters = filters.model_dump(exclude_none=True)

    query_parts = []

    if after_date := filters.get("after_date", ""):
        query_parts.append(f"after:{after_date}")

    if before_date := filters.get("before_date", ""):
        query_parts.append(f"before:{before_date}")

    if not after_date and not before_date:
        if older_than := filters.get("older_than", ""):
            query_parts.append(f"older_than:{older_than}")

    if larger_than := filters.get("larger_than", ""):
        query_parts.append(f"larger:{larger_than}")

    if category := filters.get("category", ""):
        query_parts.append(f"category:{category}")

    if sender := filters.get("sender", ""):
        query_parts.append(f"from:{sender}")

    if label := filters.get("label", ""):
        query_parts.append(f"label:{label}")

    return " ".join(query_parts)


def get_unsubscribe_from_headers(headers: list) -> tuple[Optional[str], Optional[str]]:
    """Extract unsubscribe link from email headers."""
    for header in headers:
        if header["name"].lower() == "list-unsubscribe":
            value = header["value"]

            for h in headers:
                if h["name"].lower() == "list-unsubscribe-post":
                    urls = re.findall(r"<(https?://[^>]+)>", value)
                    if urls:
                        return urls[0], "one-click"

            urls = re.findall(r"<(https?://[^>]+)>", value)
            if urls:
                return urls[0], "manual"

            mailto = re.findall(r"<(mailto:[^>]+)>", value)
            if mailto:
                return mailto[0], "manual"

    return None, None


def get_sender_info(headers: list) -> tuple[str, str]:
    """Extract sender name and email from headers."""
    for header in headers:
        if header["name"].lower() == "from":
            from_value = header["value"]
            match = re.search(r"([^<]*)<([^>]+)>", from_value)
            if match:
                name = match.group(1).strip().strip('"')
                email = match.group(2).strip()
                return name or email, email
            return from_value, from_value
    return "Unknown", "unknown"


def get_subject(headers: list) -> str:
    """Extract subject from email headers."""
    for header in headers:
        if header["name"].lower() == "subject":
            return header["value"]
    return "(No Subject)"
