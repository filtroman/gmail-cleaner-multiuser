"""
Authentication Service
----------------------
Handles multi-account OAuth2 authentication with Gmail API via FastAPI callback.
"""

import json
import logging
import os
import re
import threading
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.core import settings, state

logger = logging.getLogger(__name__)

_accounts_lock = threading.Lock()


def _is_file_empty(file_path: str) -> bool:
    """Check if a file exists and is empty."""
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r") as f:
            return not f.read().strip()
    except OSError:
        return False


def _sanitize_filename(email: str) -> str:
    """Convert an email address into a safe filename inside tokens_dir."""
    safe = re.sub(r"[^a-zA-Z0-9._+-]", "_", email.strip().lower())
    if not safe:
        safe = "account"
    filename = f"{safe}.json"
    base_dir = os.path.abspath(os.path.realpath(settings.tokens_dir))
    resolved = os.path.abspath(os.path.realpath(os.path.join(base_dir, filename)))
    if not resolved.startswith(base_dir + os.sep):
        raise ValueError("Invalid account path")
    return filename


def _load_accounts_index() -> dict:
    """Load accounts.json index safely."""
    os.makedirs(settings.tokens_dir, exist_ok=True)
    data = {"active": None, "accounts": {}}
    if os.path.exists(settings.accounts_file) and not _is_file_empty(
        settings.accounts_file
    ):
        try:
            with open(settings.accounts_file, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data["active"] = loaded.get("active")
                accounts = loaded.get("accounts", {})
                if isinstance(accounts, dict):
                    data["accounts"] = accounts
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read accounts index: {e}")

    # Prune entries whose token files no longer exist
    cleaned_accounts = {}
    for email, filename in data["accounts"].items():
        token_path = os.path.join(settings.tokens_dir, os.path.basename(filename))
        if os.path.exists(token_path) and not _is_file_empty(token_path):
            cleaned_accounts[email] = os.path.basename(filename)
    data["accounts"] = cleaned_accounts

    if data["active"] not in data["accounts"]:
        data["active"] = next(iter(data["accounts"]), None)

    return data


def _save_accounts_index(data: dict) -> None:
    """Persist accounts.json index safely."""
    os.makedirs(settings.tokens_dir, exist_ok=True)
    with open(settings.accounts_file, "w") as f:
        json.dump(data, f, indent=2)


def _get_token_path_for_email(email: str) -> str | None:
    """Get full path to token file for a given email."""
    with _accounts_lock:
        index = _load_accounts_index()
        filename = index["accounts"].get(email)
        if not filename:
            return None
        return os.path.join(settings.tokens_dir, os.path.basename(filename))


def _save_account_token(email: str, creds: Credentials, make_active: bool = True) -> None:
    """Save credentials for an email account and register it in accounts.json."""
    with _accounts_lock:
        index = _load_accounts_index()
        filename = index["accounts"].get(email) or _sanitize_filename(email)
        token_path = os.path.join(settings.tokens_dir, filename)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        index["accounts"][email] = filename
        if make_active or not index["active"]:
            index["active"] = email
        _save_accounts_index(index)


def _remove_account(email: str) -> None:
    """Delete an account's token file and remove it from accounts.json."""
    with _accounts_lock:
        index = _load_accounts_index()
        filename = index["accounts"].pop(email, None)
        if filename:
            token_path = os.path.join(settings.tokens_dir, os.path.basename(filename))
            try:
                if os.path.exists(token_path):
                    os.remove(token_path)
            except OSError as e:
                logger.warning(f"Failed to remove token file for {email}: {e}")
        if index["active"] == email:
            index["active"] = next(iter(index["accounts"]), None)
        _save_accounts_index(index)


def _migrate_legacy_token() -> None:
    """Auto-migrate legacy single token.json into tokens/ if present."""
    if not os.path.exists(settings.token_file) or _is_file_empty(settings.token_file):
        return
    try:
        creds = Credentials.from_authorized_user_file(
            settings.token_file, settings.scopes
        )
        if not creds:
            return
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress")
        if email:
            _save_account_token(email, creds, make_active=True)
            os.remove(settings.token_file)
            logger.info(f"Migrated legacy token.json for {email}")
    except Exception as e:
        logger.warning(f"Could not migrate legacy token.json: {e}")


def is_web_auth_mode() -> bool:
    """Check if we should use web-based auth."""
    return settings.web_auth


def list_accounts() -> list[str]:
    """Return list of saved account emails."""
    _migrate_legacy_token()
    with _accounts_lock:
        index = _load_accounts_index()
        return list(index["accounts"].keys())


def _get_active_email() -> str | None:
    """Get the currently active email address."""
    with _accounts_lock:
        index = _load_accounts_index()
        return index.get("active")


def _try_refresh_creds(creds: Credentials, email: str) -> Credentials | None:
    """Attempt to refresh expired credentials and save to token file."""
    try:
        creds.refresh(Request())
        _save_account_token(email, creds, make_active=False)
        return creds
    except RefreshError as e:
        logger.warning(f"Token refresh failed for {email}: {e}")
        _remove_account(email)
        return None


def _load_valid_creds_for_email(email: str) -> Credentials | None:
    """Load and refresh credentials for a specific email if needed."""
    token_path = _get_token_path_for_email(email)
    if not token_path or not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path, settings.scopes)
    except (ValueError, OSError) as e:
        logger.warning(f"Corrupted token file for {email}: {e}")
        _remove_account(email)
        return None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        return _try_refresh_creds(creds, email)

    _remove_account(email)
    return None


def needs_auth_setup() -> bool:
    """Check if authentication is needed (no valid accounts)."""
    _migrate_legacy_token()
    active_email = _get_active_email()
    if not active_email:
        return True
    return _load_valid_creds_for_email(active_email) is None


def get_web_auth_status() -> dict:
    """Get current web auth status."""
    return {
        "needs_setup": needs_auth_setup(),
        "web_auth_mode": is_web_auth_mode(),
        "has_credentials": _get_credentials_path() is not None,
        "pending_auth_url": state.pending_auth_url.get("url"),
    }


def _get_credentials_path() -> str | None:
    """Get credentials - from file or create from env var."""
    if os.path.exists(settings.credentials_file):
        if _is_file_empty(settings.credentials_file):
            logger.error(f"Credentials file {settings.credentials_file} is empty.")
            return None
        try:
            with open(settings.credentials_file, "r") as f:
                json.loads(f.read().strip())
            return settings.credentials_file
        except Exception as e:
            logger.error(f"Failed to read credentials file: {e}")
            return None

    env_creds = os.environ.get("GOOGLE_CREDENTIALS")
    if env_creds:
        try:
            json.loads(env_creds)
            with open(settings.credentials_file, "w") as f:
                f.write(env_creds)
            return settings.credentials_file
        except Exception as e:
            logger.error(f"Failed to process GOOGLE_CREDENTIALS env var: {e}")
            return None

    return None


def create_oauth_url(redirect_uri: str) -> str:
    """Start an OAuth flow and return the Google authorization URL."""
    creds_path = _get_credentials_path()
    if not creds_path:
        raise ValueError(
            "credentials.json not found or invalid! Please check your credentials.json file."
        )

    flow = Flow.from_client_secrets_file(
        creds_path,
        scopes=settings.scopes,
        redirect_uri=redirect_uri,
    )

    authorization_url, oauth_state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    with state.oauth_state_lock:
        # Clean up expired flows older than 15 minutes
        now = time.time()
        expired = [
            k
            for k, v in state.pending_flows.items()
            if now - v.get("created_at", 0) > 900
        ]
        for k in expired:
            state.pending_flows.pop(k, None)

        state.pending_flows[oauth_state] = {
            "flow": flow,
            "created_at": now,
        }
        state.oauth_state["state"] = oauth_state

    logger.info(f"Generated OAuth URL with redirect_uri={redirect_uri}")
    return authorization_url


def complete_oauth_callback(
    oauth_state: str | None, code: str | None, error: str | None
) -> tuple[bool, str]:
    """Process OAuth callback from FastAPI route, save account token, and set active."""
    if error:
        return False, f"Google returned an error: {error}"
    if not oauth_state:
        return False, "Missing OAuth state parameter."
    if not code:
        return False, "Missing authorization code."

    with state.oauth_state_lock:
        flow_entry = state.pending_flows.pop(oauth_state, None)

    if not flow_entry:
        return False, "OAuth state expired or invalid. Please try signing in again."

    flow: Flow = flow_entry["flow"]

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds:
            return False, "No credentials received from Google."

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress")
        if not email:
            return False, "Could not determine Gmail address for this account."

        _save_account_token(email, creds, make_active=True)
        state.current_user = {"email": email, "logged_in": True}
        state.reset_all_operations()
        logger.info(f"OAuth completed successfully for {email}")
        return True, email
    except Exception as e:
        logger.exception("Failed to complete OAuth callback")
        return False, f"OAuth token exchange failed: {str(e)}"


def get_gmail_service():
    """Get authenticated Gmail API service for the active account."""
    _migrate_legacy_token()
    active_email = _get_active_email()
    if not active_email:
        return None, "Not signed in. Please click 'Sign in with Google' first."

    creds = _load_valid_creds_for_email(active_email)
    if not creds:
        return (
            None,
            f"Session expired for {active_email}. Please sign in again.",
        )

    try:
        service = build("gmail", "v1", credentials=creds)
        state.current_user["email"] = active_email
        state.current_user["logged_in"] = True
        return service, None
    except Exception as e:
        logger.error(f"Failed to build Gmail service: {e}", exc_info=True)
        return None, f"Failed to connect to Gmail API: {str(e)}"

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def get_drive_service():
    """Get authenticated Google Drive API service for the active account."""
    _migrate_legacy_token()
    active_email = _get_active_email()
    if not active_email:
        return None, "Not signed in. Please sign in with Google first."

    token_path = _get_token_path_for_email(active_email)
    if not token_path or not os.path.exists(token_path):
        return None, f"No token found for {active_email}."

    # Inspect actual granted scopes stored in the token JSON
    try:
        with open(token_path, "r") as f:
            token_data = json.load(f)
        granted_scopes = token_data.get("scopes") or []
        if granted_scopes and DRIVE_SCOPE not in granted_scopes:
            return None, "NEEDS_DRIVE_SCOPE"
    except Exception:
        pass

    creds = _load_valid_creds_for_email(active_email)
    if not creds:
        return None, f"Session expired for {active_email}. Please sign in again."

    try:
        service = build("drive", "v3", credentials=creds)
        return service, None
    except Exception as e:
        logger.error(f"Failed to build Drive service: {e}", exc_info=True)
        return None, f"Failed to connect to Google Drive API: {str(e)}"

def switch_account(email: str) -> dict:
    """Switch the active Gmail account."""
    email = email.strip()
    creds = _load_valid_creds_for_email(email)
    if not creds:
        raise ValueError(f"Account '{email}' is not signed in or its token expired.")

    with _accounts_lock:
        index = _load_accounts_index()
        if email not in index["accounts"]:
            raise ValueError(f"Account '{email}' not found.")
        index["active"] = email
        _save_accounts_index(index)

    state.current_user = {"email": email, "logged_in": True}
    state.reset_all_operations()
    logger.info(f"Switched active account to {email}")

    return {
        "success": True,
        "email": email,
        "accounts": list_accounts(),
    }


def sign_out(email: str | None = None) -> dict:
    """Sign out of a specific account (or current active account)."""
    target_email = email or _get_active_email()
    if target_email:
        _remove_account(target_email)

    next_email = _get_active_email()
    if next_email and _load_valid_creds_for_email(next_email):
        state.current_user = {"email": next_email, "logged_in": True}
    else:
        state.current_user = {"email": None, "logged_in": False}

    state.reset_all_operations()
    return {
        "success": True,
        "message": f"Signed out {target_email or ''}".strip(),
        "active_email": state.current_user["email"],
        "accounts": list_accounts(),
        "results_cleared": True,
    }


def check_login_status() -> dict:
    """Check login status for active account and return all saved accounts."""
    _migrate_legacy_token()
    accounts = list_accounts()
    active_email = _get_active_email()

    if active_email:
        creds = _load_valid_creds_for_email(active_email)
        if creds:
            state.current_user["email"] = active_email
            state.current_user["logged_in"] = True
            return {
                "email": active_email,
                "logged_in": True,
                "accounts": list_accounts(),
            }
        # Active token failed/expired; check if another valid account exists
        accounts = list_accounts()
        active_email = _get_active_email()
        if active_email and _load_valid_creds_for_email(active_email):
            state.current_user["email"] = active_email
            state.current_user["logged_in"] = True
            return {
                "email": active_email,
                "logged_in": True,
                "accounts": list_accounts(),
            }

    state.current_user["email"] = None
    state.current_user["logged_in"] = False
    return {
        "email": None,
        "logged_in": False,
        "accounts": accounts,
    }
