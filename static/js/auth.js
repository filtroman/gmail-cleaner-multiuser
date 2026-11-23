/**
 * Gmail Unsubscribe - Authentication Module (Multi-Account)
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Auth = {
    currentEmail: null,

    async checkStatus() {
        try {
            const response = await fetch('/api/auth-status');
            const status = await response.json();
            this.updateUI(status);
        } catch (error) {
            console.error('Error checking auth status:', error);
            GmailCleaner.UI.showView('login');
        }
    },

    updateUI(authStatus) {
        const userSection = document.getElementById('userSection');

        if (authStatus.logged_in && authStatus.email) {
            this.currentEmail = authStatus.email;
            const accounts = Array.isArray(authStatus.accounts) && authStatus.accounts.length > 0
                ? authStatus.accounts
                : [authStatus.email];

            const optionsHtml = accounts.map(acc => {
                const safeAcc = GmailCleaner.UI.escapeHtml(acc);
                const selected = acc === authStatus.email ? 'selected' : '';
                return `<option value="${safeAcc}" ${selected}>${safeAcc}</option>`;
            }).join('');

            const initial = authStatus.email.charAt(0).toUpperCase();
            const safeEmail = GmailCleaner.UI.escapeHtml(authStatus.email);

            userSection.innerHTML = `
                <div class="user-avatar" title="${safeEmail}">${initial}</div>
                <select id="accountSwitcher" class="filter-select" onchange="GmailCleaner.Auth.switchAccount(this.value)" title="Switch Gmail Account">
                    ${optionsHtml}
                </select>
                <button class="btn btn-sm btn-primary" onclick="GmailCleaner.Auth.signIn()">+ Add Account</button>
                <button class="btn btn-sm btn-secondary" onclick="GmailCleaner.Auth.signOut()">Sign Out</button>
            `;
            GmailCleaner.Filters.showBar(true);
            GmailCleaner.UI.showView('unsubscribe');

            this.loadLabelsForFilter();
        } else {
            this.currentEmail = null;
            userSection.innerHTML = '';
            GmailCleaner.Filters.showBar(false);
            GmailCleaner.UI.showView('login');
        }
    },

    async loadLabelsForFilter() {
        try {
            const labels = await GmailCleaner.Labels.loadLabels();
            if (labels && labels.user) {
                GmailCleaner.Filters.populateLabelDropdown(labels.user);
            }
        } catch (error) {
            console.error('Error loading labels for filter:', error);
        }
    },

    async signIn() {
        const signInBtn = document.getElementById('signInBtn');
        if (signInBtn) {
            signInBtn.disabled = true;
            signInBtn.innerHTML = '<span>Redirecting to Google...</span>';
        }

        try {
            const statusResp = await fetch('/api/web-auth-status');
            const status = await statusResp.json();

            if (!status.has_credentials) {
                this.resetSignInButton();
                alert('credentials.json not found or invalid! Check your credentials.json mount.');
                return;
            }

            const signInResp = await fetch('/api/sign-in', { method: 'POST' });
            const signInResult = await signInResp.json();

            if (signInResult.error) {
                this.resetSignInButton();
                alert('Sign-in error: ' + signInResult.error);
                return;
            }

            if (signInResult.auth_url) {
                window.location.href = signInResult.auth_url;
            } else {
                this.resetSignInButton();
                alert('Failed to get authorization URL.');
            }
        } catch (error) {
            alert('Error signing in: ' + error.message);
            this.resetSignInButton();
        }
    },

    async switchAccount(email) {
        if (!email || email === this.currentEmail) return;

        try {
            const resp = await fetch('/api/accounts/switch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email }),
            });
            const result = await resp.json();
            if (!resp.ok || !result.success) {
                alert('Failed to switch account: ' + (result.detail || 'Unknown error'));
                this.checkStatus();
                return;
            }
            window.location.reload();
        } catch (error) {
            alert('Error switching account: ' + error.message);
            this.checkStatus();
        }
    },

    resetSignInButton() {
        const signInBtn = document.getElementById('signInBtn');
        if (signInBtn) {
            signInBtn.disabled = false;
            signInBtn.innerHTML = `<svg viewBox="0 0 24 24" width="20" height="20">
                <path fill="currentColor" d="M12.545,10.239v3.821h5.445c-0.712,2.315-2.647,3.972-5.445,3.972c-3.332,0-6.033-2.701-6.033-6.032s2.701-6.032,6.033-6.032c1.498,0,2.866,0.549,3.921,1.453l2.814-2.814C17.503,2.988,15.139,2,12.545,2C7.021,2,2.543,6.477,2.543,12s4.478,10,10.002,10c8.396,0,10.249-7.85,9.426-11.748L12.545,10.239z"/>
            </svg>
            Sign in with Google`;
        }
    },

    async checkWebAuthMode() {
        return;
    },

    async signOut() {
        const target = this.currentEmail || 'this account';
        if (!confirm(`Sign out of ${target}?`)) return;

        try {
            await fetch('/api/sign-out', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: this.currentEmail }),
            });
            window.location.reload();
        } catch (error) {
            alert('Error signing out: ' + error.message);
        }
    }
};

function signIn() { GmailCleaner.Auth.signIn(); }
function signOut() { GmailCleaner.Auth.signOut(); }
