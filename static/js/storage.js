/**
 * GMail Cleaner - Google Storage Analyzer Module
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Storage = {
    async analyze() {
        const btn = document.getElementById('storageScanBtn');
        const content = document.getElementById('storageContent');
        const scopePrompt = document.getElementById('storageScopePrompt');
        const minMb = document.getElementById('storageMinEmailMb')?.value || 5;

        if (scopePrompt) scopePrompt.classList.add('hidden');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Analyzing...';
        }

        try {
            const resp = await fetch(`/api/storage/analyze?min_email_mb=${encodeURIComponent(minMb)}`);
            const data = await resp.json();

            if (data.needs_drive_scope) {
                if (content) content.classList.add('hidden');
                if (scopePrompt) scopePrompt.classList.remove('hidden');
                return;
            }

            if (data.error) {
                GmailCleaner.UI.showErrorToast(data.error);
                return;
            }

            this.render(data);
            if (content) content.classList.remove('hidden');
        } catch (err) {
            GmailCleaner.UI.showErrorToast('Failed to analyze storage: ' + err.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Analyze Storage';
            }
        }
    },

    fmtBytes(bytes) {
        if (!bytes || bytes <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let val = bytes;
        let idx = 0;
        while (val >= 1024 && idx < units.length - 1) {
            val /= 1024;
            idx++;
        }
        return `${val.toFixed(idx >= 2 ? 2 : 0)} ${units[idx]}`;
    },

    render(data) {
        const q = data.quota;
        const pct = q.percent || 0;
        const activeEmail = data.email || GmailCleaner.Auth?.currentEmail || '';

        const headline = document.getElementById('storageQuotaHeadline');
        const bar = document.getElementById('storageQuotaBar');
        if (headline) {
            headline.textContent = q.limit > 0
                ? `${this.fmtBytes(q.usage)} of ${this.fmtBytes(q.limit)} used (${pct}%)`
                : `${this.fmtBytes(q.usage)} used`;
        }
        if (bar) {
            bar.style.width = `${Math.min(100, pct)}%`;
            bar.style.background = pct >= 85 ? 'var(--danger-color)' : 'var(--primary-color)';
        }

        document.getElementById('statDriveVisible').textContent = this.fmtBytes(q.drive_visible);
        document.getElementById('statDriveTrash').textContent = this.fmtBytes(q.drive_trash);
        document.getElementById('statDriveHidden').textContent = this.fmtBytes(q.drive_hidden);
        document.getElementById('statOtherQuota').textContent = this.fmtBytes(q.gmail_photos_other);

        const emptyTrashBtn = document.getElementById('emptyDriveTrashBtn');
        if (emptyTrashBtn) {
            emptyTrashBtn.disabled = q.drive_trash <= 0;
        }

        const gc = data.gmail_counts || {};
        const gmailInfo = document.getElementById('storageGmailTrashInfo');
        if (gmailInfo) {
            const totalMsgs = (gc.total_messages || 0).toLocaleString();
            gmailInfo.textContent = `GMail has ${totalMsgs} total messages (${gc.trash_messages || 0} in Trash, ${gc.spam_messages || 0} in Spam).`;
        }

        // Render Drive Files
        const driveList = document.getElementById('storageDriveList');
        const driveCount = document.getElementById('storageDriveCount');
        const files = data.drive_files || [];
        if (driveCount) driveCount.textContent = files.length;

        if (driveList) {
            if (files.length === 0) {
                driveList.innerHTML = '<div class="label-empty">No large Drive files owned by you found.</div>';
            } else {
                driveList.innerHTML = files.map(f => {
                    const safeName = GmailCleaner.UI.escapeHtml(f.name);
                    const trashedBadge = f.trashed ? '<span class="type-badge type-manual">In Trash</span>' : '';
                    const dateStr = f.modified_time ? new Date(f.modified_time).toLocaleDateString() : '';
                    const openLink = f.web_link
                        ? `<a href="${GmailCleaner.UI.escapeHtml(f.web_link)}" target="_blank" rel="noopener" class="link-btn">Open</a>`
                        : '';
                    const trashBtn = !f.trashed
                        ? `<button class="unsub-btn delete-btn" onclick="GmailCleaner.Storage.trashDriveFile('${GmailCleaner.UI.escapeHtml(f.id)}', this)">Trash</button>`
                        : '';

                    return `
                        <div class="result-item">
                            <div class="result-content">
                                <div class="result-sender" title="${safeName}">${safeName}${trashedBadge}</div>
                                <div class="result-subject">${dateStr}</div>
                                <div class="result-meta">
                                    <span class="result-count">${this.fmtBytes(f.size)}</span>
                                </div>
                            </div>
                            <div class="result-actions">
                                ${openLink}
                                ${trashBtn}
                            </div>
                        </div>
                    `;
                }).join('');
            }
        }

        // Render Large GMail Emails
        const emailList = document.getElementById('storageEmailList');
        const emailCount = document.getElementById('storageEmailCount');
        const emails = data.large_emails || [];
        const totalEmailBytes = gc.large_emails_total_size || 0;
        if (emailCount) {
            emailCount.textContent = `${emails.length} · ${this.fmtBytes(totalEmailBytes)}`;
        }

        if (emailList) {
            if (emails.length === 0) {
                emailList.innerHTML = '<div class="label-empty">No large emails found at this size threshold.</div>';
            } else {
                emailList.innerHTML = emails.map(m => {
                    const safeSender = GmailCleaner.UI.escapeHtml(m.sender_email || m.sender_name);
                    const safeSubj = GmailCleaner.UI.escapeHtml(m.subject);
                    let dateStr = '';
                    if (m.date) {
                        const d = new Date(m.date);
                        if (!isNaN(d.getTime())) dateStr = d.toLocaleDateString();
                    }
                    const authParam = activeEmail ? `?authuser=${encodeURIComponent(activeEmail)}` : '';
                    const gmailUrl = `https://mail.google.com/mail/${authParam}#all/${encodeURIComponent(m.id)}`;

                    return `
                        <div class="result-item">
                            <div class="result-content">
                                <div class="result-sender" title="${safeSender}">${safeSender}</div>
                                <div class="result-subject" title="${safeSubj}">${safeSubj}</div>
                                <div class="result-meta">
                                    ${dateStr ? `<div class="result-date-range">${dateStr}</div>` : ''}
                                    <span class="result-count">${this.fmtBytes(m.size)}</span>
                                </div>
                            </div>
                            <div class="result-actions">
                                <a href="${gmailUrl}" target="_blank" rel="noopener" class="link-btn">Open</a>
                                <button class="unsub-btn delete-btn" onclick="GmailCleaner.Storage.trashGmailMessage('${GmailCleaner.UI.escapeHtml(m.id)}', this)">Trash</button>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        }
    },

    async trashDriveFile(fileId, btn) {
        if (!confirm('Move this Drive file to Trash?')) return;
        btn.disabled = true;
        btn.textContent = '...';
        const resp = await fetch('/api/storage/drive/trash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId }),
        });
        const res = await resp.json();
        if (res.success) {
            btn.classList.add('success');
            btn.textContent = 'Trashed';
            GmailCleaner.UI.showSuccessToast('Moved Drive file to Trash');
        } else {
            btn.disabled = false;
            btn.textContent = 'Trash';
            GmailCleaner.UI.showErrorToast(res.message || 'Failed');
        }
    },

    async emptyDriveTrash() {
        if (!confirm('PERMANENTLY empty your Google Drive Trash? This cannot be undone!')) return;
        const resp = await fetch('/api/storage/drive/empty-trash', { method: 'POST' });
        const res = await resp.json();
        if (res.success) {
            GmailCleaner.UI.showSuccessToast('Google Drive Trash emptied!');
            this.analyze();
        } else {
            GmailCleaner.UI.showErrorToast(res.message || 'Failed');
        }
    },

    async trashGmailMessage(msgId, btn) {
        if (!confirm('Move this email to GMail Trash?')) return;
        btn.disabled = true;
        btn.textContent = '...';
        const resp = await fetch('/api/storage/gmail/trash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message_id: msgId }),
        });
        const res = await resp.json();
        if (res.success) {
            btn.classList.add('success');
            btn.textContent = 'Trashed';
            GmailCleaner.UI.showSuccessToast('Moved email to GMail Trash');
        } else {
            btn.disabled = false;
            btn.textContent = 'Trash';
            GmailCleaner.UI.showErrorToast(res.message || 'Failed');
        }
    }
};
