/**
 * Gmail Unsubscribe - Delete Emails Module
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Delete = {
    formatDateRange(firstDate, lastDate) {
        const formatDate = (dateStr) => {
            try {
                const date = new Date(dateStr);
                if (isNaN(date.getTime())) return null;
                const m = String(date.getMonth() + 1).padStart(2, '0');
                const d = String(date.getDate()).padStart(2, '0');
                const y = date.getFullYear();
                return `${m}/${d}/${y}`;
            } catch {
                return null;
            }
        };

        const first = formatDate(firstDate);
        const last = formatDate(lastDate);

        if (!first || !last) return '';
        if (first === last) return first;

        const firstDateObj = new Date(firstDate);
        const lastDateObj = new Date(lastDate);

        if (firstDateObj <= lastDateObj) {
            return `${first} to ${last}`;
        } else {
            return `${last} to ${first}`;
        }
    },

    async startScan() {
        if (GmailCleaner.deleteScanning) return;

        const authResponse = await fetch('/api/auth-status');
        const authStatus = await authResponse.json();

        if (!authStatus.logged_in) {
            GmailCleaner.Auth.signIn();
            return;
        }

        GmailCleaner.deleteScanning = true;

        const scanBtn = document.getElementById('deleteScanBtn');
        const progressCard = document.getElementById('deleteProgressCard');

        scanBtn.disabled = true;
        scanBtn.innerHTML = `
            <svg class="spinner" viewBox="0 0 24 24" width="18" height="18">
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="60" stroke-linecap="round"/>
            </svg>
            Scanning...
        `;
        progressCard.classList.remove('hidden');

        const limit = document.getElementById('deleteScanLimit').value;
        const groupBy = document.getElementById('deleteGroupBy')?.value || 'sender';
        const filters = GmailCleaner.Filters.get();

        try {
            const response = await fetch('/api/delete-scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    limit: parseInt(limit),
                    group_by: groupBy,
                    filters: filters
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                const errorMsg = errorData.detail || `Request failed with status ${response.status}`;
                throw new Error(errorMsg);
            }

            this.pollProgress();
        } catch (error) {
            alert('Error: ' + error.message);
            this.resetScan();
        }
    },

    async pollProgress() {
        try {
            const response = await fetch('/api/delete-scan-status');
            const status = await response.json();

            const progressBar = document.getElementById('deleteProgressBar');
            const progressText = document.getElementById('deleteProgressText');

            progressBar.style.width = status.progress + '%';
            progressText.textContent = status.message;

            if (status.done) {
                if (!status.error) {
                    const resultsResponse = await fetch('/api/delete-scan-results');
                    GmailCleaner.deleteResults = await resultsResponse.json();
                    const mode = GmailCleaner.deleteResults[0]?.group_type;
                    const sortEl = document.getElementById('deleteSortBy');
                    if (sortEl && mode === 'individual') {
                        sortEl.value = 'size';
                    }
                    this.resortAndDisplay();
                } else {
                    alert('Error: ' + status.error);
                }
                this.resetScan();
            } else {
                setTimeout(() => this.pollProgress(), 300);
            }
        } catch (error) {
            setTimeout(() => this.pollProgress(), 500);
        }
    },

    resetScan() {
        GmailCleaner.deleteScanning = false;
        const scanBtn = document.getElementById('deleteScanBtn');
        scanBtn.disabled = false;
        scanBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="18" height="18">
                <path fill="currentColor" d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
            </svg>
            Scan Emails
        `;
    },

    resortAndDisplay() {
        const sortBy = document.getElementById('deleteSortBy')?.value || 'count';
        if (GmailCleaner.deleteResults && GmailCleaner.deleteResults.length > 0) {
            GmailCleaner.deleteResults.sort((a, b) => {
                if (sortBy === 'size') {
                    return (b.total_size || 0) - (a.total_size || 0);
                }
                const countDiff = (b.count || 0) - (a.count || 0);
                return countDiff !== 0 ? countDiff : (b.total_size || 0) - (a.total_size || 0);
            });
        }
        this.displayResults();
    },

    displayResults() {
        const resultsList = document.getElementById('deleteResultsList');
        const resultsSection = document.getElementById('deleteResultsSection');
        const noResults = document.getElementById('deleteNoResults');
        const badge = document.getElementById('deleteSendersBadge');

        resultsList.innerHTML = '';
        badge.textContent = GmailCleaner.deleteResults.length;

        if (GmailCleaner.deleteResults.length === 0) {
            resultsSection.classList.add('hidden');
            noResults.classList.remove('hidden');
            this.setActionButtonsEnabled(false, false);
            return;
        }

        const mode = GmailCleaner.deleteResults[0]?.group_type || 'sender';
        const isSenderOnlyDisabled = mode !== 'sender';
        resultsSection.classList.remove('hidden');
        noResults.classList.add('hidden');
        this.setActionButtonsEnabled(true, isSenderOnlyDisabled);

        const activeEmail = GmailCleaner.Auth?.currentEmail || '';

        GmailCleaner.deleteResults.forEach((r, i) => {
            const item = document.createElement('div');
            item.className = 'result-item';

            const dateRange = this.formatDateRange(r.first_date, r.last_date);
            const dateRangeDisplay = dateRange ? `<div class="result-date-range">${dateRange}</div>` : '';
            const title = r.display_title || r.email;
            const sizeStr = GmailCleaner.UI.formatSize(r.total_size);
            const sizeBadge = sizeStr ? `<span class="result-count">${sizeStr}</span>` : '';
            const countBadge = mode === 'individual' ? '' : `<span class="result-count">${r.count} emails</span>`;

            let openLink = '';
            if (mode === 'individual' && r.message_id) {
                const authParam = activeEmail ? `?authuser=${encodeURIComponent(activeEmail)}` : '';
                const gmailUrl = `https://mail.google.com/mail/${authParam}#all/${encodeURIComponent(r.message_id)}`;
                openLink = `<a href="${gmailUrl}" target="_blank" rel="noopener" class="link-btn">Open</a>`;
            }

            const btnText = mode === 'individual' ? 'Delete' : `Delete ${r.count}`;

            item.innerHTML = `
                <label class="checkbox-wrapper result-checkbox">
                    <input type="checkbox" class="delete-cb" data-index="${i}" data-email="${GmailCleaner.UI.escapeHtml(r.email)}">
                    <span class="checkmark"></span>
                </label>
                <div class="result-content">
                    <div class="result-sender" title="${GmailCleaner.UI.escapeHtml(title)}">${GmailCleaner.UI.escapeHtml(title)}</div>
                    <div class="result-subject" title="${GmailCleaner.UI.escapeHtml(r.subjects[0] || '')}">${GmailCleaner.UI.escapeHtml(r.subjects[0] || 'No subject')}</div>
                    <div class="result-meta">
                        ${dateRangeDisplay}
                        ${countBadge}
                        ${sizeBadge}
                    </div>
                </div>
                <div class="result-actions">
                    ${openLink}
                    <button class="unsub-btn delete-btn" id="delete-${i}" onclick="GmailCleaner.Delete.deleteSenderEmails(${i})">
                        ${btnText}
                    </button>
                </div>
            `;
            resultsList.appendChild(item);
        });
    },

    setActionButtonsEnabled(enabled, senderOnlyDisabled = false) {
        const senderOnlyButtons = [
            'applyLabelBtn',
            'archiveBtn',
            'importantBtn',
            'downloadBtn'
        ];
        senderOnlyButtons.forEach(id => {
            const btn = document.getElementById(id);
            if (btn) {
                btn.disabled = !enabled || senderOnlyDisabled;
            }
        });
        const deleteBtn = document.getElementById('deleteSelectedBtn');
        if (deleteBtn) {
            deleteBtn.disabled = !enabled;
        }
    },

    toggleSelectAll() {
        const selectAll = document.getElementById('deleteSelectAll');
        document.querySelectorAll('.delete-cb').forEach(cb => {
            cb.checked = selectAll.checked;
        });
    },

    async deleteSenderEmails(index) {
        const r = GmailCleaner.deleteResults[index];
        const btn = document.getElementById('delete-' + index);
        const title = r.display_title || r.email;
        const mode = r.group_type || 'sender';
        const btnResetText = mode === 'individual' ? 'Delete' : `Delete ${r.count}`;

        const promptText = mode === 'individual'
            ? `Delete this email "${title}"?\n\nThis will move it to GMail Trash.`
            : `Delete ALL ${r.count} emails for "${title}"?\n\nThis will move them to GMail Trash.`;

        if (!confirm(promptText)) {
            return;
        }

        btn.disabled = true;
        btn.classList.add('btn-deleting');
        btn.innerHTML = `
            <svg class="spinner" viewBox="0 0 24 24" width="14" height="14">
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="60" stroke-linecap="round"/>
            </svg>
            Deleting...
        `;

        try {
            const response = await fetch('/api/delete-emails', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sender: r.email })
            });
            const result = await response.json();

            if (result.success) {
                btn.classList.remove('btn-deleting');
                btn.innerHTML = '✓ Deleted!';
                btn.classList.add('success');
                setTimeout(() => {
                    GmailCleaner.deleteResults = GmailCleaner.deleteResults.filter((_, i) => i !== index);
                    this.displayResults();
                }, 800);
            } else {
                btn.classList.remove('btn-deleting');
                alert('Error: ' + result.message);
                btn.disabled = false;
                btn.innerHTML = btnResetText;
            }
        } catch (error) {
            alert('Error: ' + error.message);
            btn.classList.remove('btn-deleting');
            btn.disabled = false;
            btn.innerHTML = btnResetText;
        }
    },

    async deleteSelected() {
        const checkboxes = document.querySelectorAll('.delete-cb:checked');
        if (checkboxes.length === 0) {
            alert('Please select at least one item to delete.');
            return;
        }

        let totalEmails = 0;
        const senderEmails = [];
        checkboxes.forEach(cb => {
            const index = parseInt(cb.dataset.index);
            const r = GmailCleaner.deleteResults[index];
            totalEmails += r.count;
            senderEmails.push(r.email);
        });

        const mode = GmailCleaner.deleteResults[0]?.group_type || 'sender';
        const confirmMsg = mode === 'individual'
            ? `Delete ${totalEmails} selected email(s)?\n\nThis will move them to GMail Trash.`
            : `Delete ${totalEmails} emails across ${checkboxes.length} ${mode === 'subject' ? 'repeated subject groups' : 'senders'}?\n\nThis will move them to GMail Trash.`;

        if (!confirm(confirmMsg)) {
            return;
        }

        this.showDeleteOverlay(checkboxes.length, totalEmails);

        checkboxes.forEach(cb => {
            const index = parseInt(cb.dataset.index);
            const btn = document.getElementById('delete-' + index);
            if (btn) {
                btn.disabled = true;
                btn.classList.add('btn-deleting');
                btn.innerHTML = `
                    <svg class="spinner" viewBox="0 0 24 24" width="14" height="14">
                        <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="60" stroke-linecap="round"/>
                    </svg>
                    Deleting...
                `;
            }
        });

        try {
            await fetch('/api/delete-emails-bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: senderEmails })
            });

            this.pollDeleteProgress(checkboxes);
        } catch (error) {
            this.hideDeleteOverlay();
            alert('Error: ' + error.message);
        }
    },

    async pollDeleteProgress(checkboxes) {
        try {
            const response = await fetch('/api/delete-bulk-status');
            const status = await response.json();

            this.updateDeleteOverlay(status);

            if (status.done) {
                this.hideDeleteOverlay();

                if (!status.error) {
                    checkboxes.forEach(cb => {
                        const index = parseInt(cb.dataset.index);
                        const btn = document.getElementById('delete-' + index);
                        if (btn) {
                            btn.classList.remove('btn-deleting');
                            btn.innerHTML = '✓ Deleted!';
                            btn.classList.add('success');
                        }
                    });

                    setTimeout(async () => {
                        const resultsResponse = await fetch('/api/delete-scan-results');
                        GmailCleaner.deleteResults = await resultsResponse.json();
                        this.resortAndDisplay();
                        document.getElementById('deleteSelectAll').checked = false;
                    }, 800);
                } else {
                    alert('Error: ' + status.error);
                    this.displayResults();
                }
            } else {
                setTimeout(() => this.pollDeleteProgress(checkboxes), 300);
            }
        } catch (error) {
            setTimeout(() => this.pollDeleteProgress(checkboxes), 500);
        }
    },

    showDeleteOverlay(senderCount, emailCount) {
        this.hideDeleteOverlay();

        const overlay = document.createElement('div');
        overlay.id = 'deleteOverlay';
        overlay.className = 'delete-overlay';
        overlay.innerHTML = `
            <div class="delete-overlay-content">
                <svg class="delete-overlay-spinner spinner" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10" fill="none" stroke="#3b82f6" stroke-width="2" stroke-dasharray="60" stroke-linecap="round"/>
                </svg>
                <h3>Deleting Emails...</h3>
                <div class="delete-progress-container">
                    <div class="delete-progress-bar" id="deleteBulkProgressBar"></div>
                </div>
                <p id="deleteBulkProgressText">Starting deletion...</p>
                <p class="delete-stats" id="deleteBulkStats">0/${senderCount} items | 0 emails deleted</p>
            </div>
        `;
        overlay.dataset.totalSenders = senderCount;
        document.body.appendChild(overlay);
    },

    updateDeleteOverlay(status) {
        const progressBar = document.getElementById('deleteBulkProgressBar');
        const progressText = document.getElementById('deleteBulkProgressText');
        const stats = document.getElementById('deleteBulkStats');
        const overlay = document.getElementById('deleteOverlay');

        if (progressBar) {
            progressBar.style.width = status.progress + '%';
        }
        if (progressText) {
            progressText.textContent = status.message;
        }
        if (stats && overlay) {
            const totalSenders = overlay.dataset.totalSenders || status.total_senders;
            if (status.progress <= 40) {
                stats.textContent = `Scanning ${status.current_sender || 0}/${totalSenders}...`;
            } else {
                stats.textContent = `${status.deleted_count || 0} emails deleted`;
            }
        }
    },

    hideDeleteOverlay() {
        const overlay = document.getElementById('deleteOverlay');
        if (overlay) {
            overlay.remove();
        }
    },

    async downloadSelected() {
        const checkboxes = document.querySelectorAll('.delete-cb:checked');
        if (checkboxes.length === 0) {
            GmailCleaner.UI.showInfoToast('Please select at least one sender to download emails from.');
            return;
        }

        let totalEmails = 0;
        const senderEmails = [];
        checkboxes.forEach(cb => {
            const index = parseInt(cb.dataset.index);
            const r = GmailCleaner.deleteResults[index];
            totalEmails += r.count;
            senderEmails.push(r.email);
        });

        this.showDownloadOverlay(checkboxes.length, totalEmails);

        try {
            await fetch('/api/download-emails', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: senderEmails })
            });

            this.pollDownloadProgress();
        } catch (error) {
            this.hideDownloadOverlay();
            GmailCleaner.UI.showErrorToast('Error: ' + error.message);
        }
    },

    async pollDownloadProgress() {
        try {
            const response = await fetch('/api/download-status');
            const status = await response.json();

            this.updateDownloadOverlay(status);

            if (status.done) {
                if (!status.error) {
                    window.location.href = '/api/download-csv';
                    setTimeout(() => {
                        this.hideDownloadOverlay();
                        GmailCleaner.UI.showSuccessToast(
                            `Successfully exported ${status.fetched_count.toLocaleString()} emails to CSV file. Check your downloads folder.`,
                            'Tip: You can open the CSV in Excel or Google Sheets for easy viewing'
                        );
                    }, 500);
                } else {
                    this.hideDownloadOverlay();
                    GmailCleaner.UI.showErrorToast('Error: ' + status.error);
                }
            } else {
                setTimeout(() => this.pollDownloadProgress(), 300);
            }
        } catch (error) {
            setTimeout(() => this.pollDownloadProgress(), 500);
        }
    },

    showDownloadOverlay(senderCount, emailCount) {
        this.hideDownloadOverlay();

        const overlay = document.createElement('div');
        overlay.id = 'downloadOverlay';
        overlay.className = 'download-overlay';
        overlay.innerHTML = `
            <div class="download-overlay-content">
                <svg class="download-overlay-spinner spinner" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10" fill="none" stroke="#10b981" stroke-width="2" stroke-dasharray="60" stroke-linecap="round"/>
                </svg>
                <h3>Downloading Email Data...</h3>
                <div class="download-progress-container">
                    <div class="download-progress-bar" id="downloadProgressBar"></div>
                </div>
                <p id="downloadProgressText">Starting download...</p>
                <p class="download-stats" id="downloadStats">0/${emailCount} emails from ${senderCount} senders</p>
                <p class="download-note">This may take a moment for large mailboxes</p>
            </div>
        `;
        overlay.dataset.totalEmails = emailCount;
        document.body.appendChild(overlay);
    },

    updateDownloadOverlay(status) {
        const overlay = document.getElementById('downloadOverlay');
        if (!overlay) return;

        const progressBar = document.getElementById('downloadProgressBar');
        const progressText = document.getElementById('downloadProgressText');
        const stats = document.getElementById('downloadStats');

        if (progressBar) {
            progressBar.style.width = status.progress + '%';
        }
        if (progressText) {
            progressText.textContent = status.message;
        }
        if (stats) {
            const totalEmails = overlay.dataset.totalEmails || status.total_emails;
            stats.textContent = `${status.fetched_count || 0}/${totalEmails} emails fetched`;
        }
    },

    hideDownloadOverlay() {
        const overlay = document.getElementById('downloadOverlay');
        if (overlay) {
            overlay.remove();
        }
    }
};

// Global shortcuts
function startDeleteScan() { GmailCleaner.Delete.startScan(); }
function toggleDeleteSelectAll() { GmailCleaner.Delete.toggleSelectAll(); }
function deleteSelectedSenders() { GmailCleaner.Delete.deleteSelected(); }
function downloadSelectedEmails() { GmailCleaner.Delete.downloadSelected(); }
