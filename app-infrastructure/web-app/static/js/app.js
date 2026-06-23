/* ============================================================
   NetVigil — SPA Application Logic
   ============================================================ */

(function () {
    'use strict';

    // ---- State ----
    let currentPage = 'dashboard';
    let logData = [];
    let chartTimeline = null;
    let chartContainers = null;
    let chartProtocols = null;
    let refreshInterval = null;

    // ---- DOM Helpers ----
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ---- Clock ----
    function updateClock() {
        const now = new Date();
        $('#clock').textContent = now.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    setInterval(updateClock, 1000);
    updateClock();

    // ---- SPA Routing ----
    function navigateTo(page) {
        currentPage = page;
        $$('.page').forEach(p => p.classList.remove('active'));
        $$('.nav-item').forEach(n => n.classList.remove('active'));
        const pageEl = $(`#page-${page}`);
        const navEl = $(`[data-page="${page}"]`);
        if (pageEl) pageEl.classList.add('active');
        if (navEl) navEl.classList.add('active');

        const titles = { dashboard: 'Dashboard', logs: 'Log Viewer', traffic: 'Traffic Analysis', alerts: 'Alerts', 'telegram-history': 'Telegram History', chat: 'AI Chat' };
        $('#page-title').textContent = titles[page] || 'Dashboard';

        if (page === 'dashboard') loadDashboard();
        if (page === 'alerts') loadAlerts();
        if (page === 'telegram-history') loadTelegramHistory();

    }

    $$('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            window.location.hash = page;
            navigateTo(page);
            // Close mobile sidebar
            $('#sidebar').classList.remove('open');
        });
    });

    // Handle hash routing
    window.addEventListener('hashchange', () => {
        const hash = window.location.hash.slice(1) || 'dashboard';
        navigateTo(hash);
    });

    // Mobile menu toggle
    $('#menu-toggle').addEventListener('click', () => {
        $('#sidebar').classList.toggle('open');
    });

    // Refresh button
    $('#btn-refresh').addEventListener('click', () => {
        if (currentPage === 'dashboard') loadDashboard();
        else if (currentPage === 'alerts') loadAlerts();
        else if (currentPage === 'telegram-history') loadTelegramHistory();
    });

    // ---- API Helpers ----
    async function api(url, options = {}) {
        try {
            const resp = await fetch(url, options);
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                throw new Error(errData.error || `HTTP ${resp.status}`);
            }
            return await resp.json();
        } catch (e) {
            console.error(`API Error [${url}]:`, e);
            return { error: e.message };
        }
    }

    function formatBytes(bytes) {
        if (bytes === 0) return '0 B/s';
        const k = 1024;
        const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
        const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    // ---- Dashboard ----
    async function loadDashboard() {
        const [dashData, timelineData, metricsData] = await Promise.all([
            api('/api/dashboard'),
            api('/api/dashboard/timeline'),
            api('/api/metrics'),
        ]);

        if (dashData) {
            $('#kpi-events-value').textContent = dashData.total_events.toLocaleString();
            $('#kpi-errors-value').textContent = dashData.error_events.toLocaleString();
            $('#kpi-alerts-value').textContent = dashData.active_alerts;
            $('#kpi-cpu-value').textContent = dashData.system.cpu_usage + '%';

            // Alert badge
            if (dashData.active_alerts > 0) {
                $('#alert-badge').style.display = 'inline';
                $('#alert-badge').textContent = dashData.active_alerts;
            } else {
                $('#alert-badge').style.display = 'none';
            }

            // Container chart
            renderContainerChart(dashData.containers);
        }

        if (timelineData) {
            renderTimelineChart(timelineData);
        }

        if (metricsData) {
            updateRing('memory', metricsData.memory_percent);
            updateRing('disk', metricsData.disk_percent);
            $('#metric-http').innerHTML = `${metricsData.http_rps} <small>req/s</small>`;
            $('#metric-rx').textContent = formatBytes(metricsData.network_rx_bps);
            $('#metric-tx').textContent = formatBytes(metricsData.network_tx_bps);
        }
    }

    function updateRing(name, value) {
        const circumference = 2 * Math.PI * 52;
        const offset = circumference - (value / 100) * circumference;
        const fill = $(`#ring-${name}-fill`);
        const label = $(`#ring-${name}-label`);
        fill.style.strokeDashoffset = offset;
        label.textContent = Math.round(value) + '%';

        // Color based on value
        if (value > 80) fill.style.stroke = 'var(--accent-red)';
        else if (value > 60) fill.style.stroke = 'var(--accent-orange)';
        else fill.style.stroke = 'var(--accent-cyan)';
    }

    function renderTimelineChart(data) {
        const ctx = $('#chart-timeline');
        if (chartTimeline) chartTimeline.destroy();

        const labels = data.requests.map(d => d.time);

        chartTimeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Requests/s',
                        data: data.requests.map(d => d.value),
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59,130,246,0.1)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 2,
                        pointRadius: 0,
                    },
                    {
                        label: 'Errors/s',
                        data: data.errors.map(d => d.value),
                        borderColor: '#ef4444',
                        backgroundColor: 'rgba(239,68,68,0.1)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 2,
                        pointRadius: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } } },
                scales: {
                    x: { ticks: { color: '#64748b', maxTicksLimit: 12 }, grid: { color: 'rgba(71,85,105,0.2)' } },
                    y: { ticks: { color: '#64748b' }, grid: { color: 'rgba(71,85,105,0.2)' }, beginAtZero: true },
                },
            },
        });
    }

    function renderContainerChart(containers) {
        const ctx = $('#chart-containers');
        if (chartContainers) chartContainers.destroy();

        const labels = Object.keys(containers);
        const values = Object.values(containers);
        const colors = ['#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444'];

        chartContainers = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{ data: values, backgroundColor: colors.slice(0, labels.length), borderWidth: 0 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    legend: { position: 'bottom', labels: { color: '#94a3b8', padding: 12, font: { family: 'Inter', size: 11 } } },
                },
            },
        });
    }

    // ---- Log Viewer ----
    $('#btn-fetch-logs').addEventListener('click', fetchLogs);
    $('#btn-analyze-logs').addEventListener('click', analyzeLogs);

    async function fetchLogs() {
        const container = $('#log-container').value;
        const keyword = $('#log-keyword').value;
        const hours = $('#log-hours').value;
        const limit = $('#log-limit').value;

        const params = new URLSearchParams({ container, keyword, hours, limit });
        $('#log-tbody').innerHTML = '<tr><td colspan="4" class="empty-state"><div class="spinner" style="margin:0 auto"></div></td></tr>';

        const data = await api(`/api/logs?${params}`);
        if (!data) {
            $('#log-tbody').innerHTML = '<tr><td colspan="4" class="empty-state">Failed to fetch logs</td></tr>';
            return;
        }

        logData = data.logs;
        $('#log-count').textContent = `(${data.total} results)`;
        $('#btn-analyze-logs').disabled = logData.length === 0;

        if (logData.length === 0) {
            $('#log-tbody').innerHTML = '<tr><td colspan="4" class="empty-state">No logs found</td></tr>';
            return;
        }

        $('#log-tbody').innerHTML = logData.map(log => `
            <tr>
                <td>${log.timestamp}</td>
                <td>${log.container}</td>
                <td><span class="log-level ${log.level}">${log.level}</span></td>
                <td>${escapeHtml(log.message)}</td>
            </tr>
        `).join('');
    }

    async function analyzeLogs() {
        if (logData.length === 0) return;
        const btn = $('#btn-analyze-logs');
        btn.disabled = true;
        btn.innerHTML = '<div class="spinner" style="width:16px;height:16px;margin:0;border-width:2px"></div> Analyzing...';

        const logsText = logData.slice(0, 50).map(l => `[${l.timestamp}] [${l.container}] ${l.message}`).join('\n');
        const data = await api('/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ logs: logsText, context: 'system logs analysis' }),
        });

        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg> Analyze with AI';

        if (!data || data.error || !data.analysis) {
            $('#log-analysis-card').style.display = 'block';
            $('#log-analysis-content').innerHTML = `<p style="color:var(--accent-red)">Analysis failed: ${data?.error || 'LLM may be unavailable.'}</p>`;
            return;
        }

        renderAnalysis(data.analysis, 'log-analysis');
    }

    function renderAnalysis(analysis, prefix) {
        const card = $(`#${prefix}-card`);
        const content = $(`#${prefix}-content`);
        card.style.display = 'block';

        const riskClass = (analysis.risk_score || 0) <= 3 ? 'risk-low' : (analysis.risk_score || 0) <= 6 ? 'risk-medium' : 'risk-high';

        let html = `
            <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
                <div class="risk-score ${riskClass}">${analysis.risk_score || 0}</div>
                <div>
                    <strong>Risk Score: ${analysis.risk_score || 0}/10</strong>
                    <div style="color:var(--text-muted);font-size:0.82rem">Threat found: ${analysis.threat_found ? '✅ Yes' : '❌ No'}</div>
                </div>
            </div>
        `;

        if (analysis.summary) {
            html += `<p style="margin-bottom:16px">${escapeHtml(analysis.summary)}</p>`;
        }

        if (analysis.threats && analysis.threats.length > 0) {
            html += '<h4 style="margin-bottom:12px">Detected Threats:</h4>';
            for (const threat of analysis.threats) {
                const sevClass = `severity-${(threat.severity || 'low').toLowerCase()}`;
                html += `
                    <div class="threat-item">
                        <h4>${escapeHtml(threat.type || 'Unknown')} <span class="severity ${sevClass}">${threat.severity || 'unknown'}</span></h4>
                        <p>${escapeHtml(threat.description || '')}</p>
                        ${threat.recommendations ? `<p><strong>Recommendations:</strong> ${escapeHtml(threat.recommendations)}</p>` : ''}
                    </div>
                `;
            }
        }

        content.innerHTML = html;
    }

    // ---- Traffic Analysis (PCAP) ----
    const uploadZone = $('#upload-zone');
    const pcapInput = $('#pcap-file');

    ['dragenter', 'dragover'].forEach(ev => {
        uploadZone.addEventListener(ev, (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(ev => {
        uploadZone.addEventListener(ev, (e) => { e.preventDefault(); uploadZone.classList.remove('dragover'); });
    });

    uploadZone.addEventListener('drop', (e) => {
        const file = e.dataTransfer.files[0];
        if (file) uploadPcap(file);
    });

    pcapInput.addEventListener('change', () => {
        if (pcapInput.files[0]) uploadPcap(pcapInput.files[0]);
    });

    async function uploadPcap(file) {
        $('#upload-zone').style.display = 'none';
        $('#upload-progress').style.display = 'block';

        const formData = new FormData();
        formData.append('file', file);

        const data = await api('/api/analyze/pcap', { method: 'POST', body: formData });

        $('#upload-progress').style.display = 'none';
        $('#upload-zone').style.display = 'block';

        if (!data || data.error) {
            alert(data ? `PCAP analysis failed: ${data.error}` : 'PCAP analysis failed: Server unreachable');
            return;
        }

        // Stats
        if (data.statistics) {
            const stats = data.statistics;
            $('#pcap-stats-card').style.display = 'block';
            $('#pcap-stats').innerHTML = `
                <div class="stat-item"><div class="stat-value">${stats.total_packets}</div><div class="stat-label">Total Packets</div></div>
                <div class="stat-item"><div class="stat-value">${stats.unique_sources}</div><div class="stat-label">Unique Sources</div></div>
                <div class="stat-item"><div class="stat-value">${stats.unique_destinations}</div><div class="stat-label">Unique Destinations</div></div>
                <div class="stat-item"><div class="stat-value">${Object.keys(stats.protocols || {}).length}</div><div class="stat-label">Protocols</div></div>
            `;

            // Protocol chart
            if (stats.protocols) {
                const ctx = $('#chart-protocols');
                if (chartProtocols) chartProtocols.destroy();
                const labels = Object.keys(stats.protocols);
                const values = Object.values(stats.protocols);
                const colors = ['#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#ec4899', '#14b8a6'];
                chartProtocols = new Chart(ctx, {
                    type: 'bar',
                    data: { labels, datasets: [{ label: 'Packets', data: values, backgroundColor: colors.slice(0, labels.length) }] },
                    options: {
                        responsive: true,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                            y: { ticks: { color: '#64748b' }, grid: { color: 'rgba(71,85,105,0.2)' }, beginAtZero: true },
                        },
                    },
                });
            }
        }

        // Packets table
        if (data.packets && data.packets.length > 0) {
            $('#pcap-packets-card').style.display = 'block';
            $('#pcap-tbody').innerHTML = data.packets.map(pkt => `
                <tr>
                    <td>${pkt.num}</td>
                    <td>${pkt.timestamp}</td>
                    <td><span class="log-level info">${pkt.protocol}</span></td>
                    <td>${escapeHtml(pkt.src)}</td>
                    <td>${escapeHtml(pkt.dst)}</td>
                    <td>${pkt.length}</td>
                    <td>${escapeHtml(pkt.info)}</td>
                </tr>
            `).join('');
        }

        // Analysis
        if (data.analysis) {
            renderAnalysis(data.analysis, 'pcap-analysis');
        }
    }

    // ---- Alerts ----
    $('#btn-refresh-alerts').addEventListener('click', loadAlerts);

    async function loadAlerts() {
        const data = await api('/api/alerts');
        if (!data) {
            $('#alerts-list').innerHTML = '<div class="empty-state">Failed to load alerts</div>';
            return;
        }

        if (data.alerts.length === 0) {
            $('#alerts-list').innerHTML = '<div class="empty-state">✅ No active alerts — system is secure</div>';
            return;
        }

        $('#alerts-list').innerHTML = data.alerts.map(alert => {
            const cls = alert.severity === 'critical' ? 'critical' : alert.status === 'resolved' ? 'resolved' : '';
            return `
                <div class="alert-item ${cls}">
                    <h4>${escapeHtml(alert.name)} ${alert.problem_type !== 'N/A' ? `— ${escapeHtml(alert.problem_type)}` : ''}</h4>
                    <div class="alert-meta">
                        <span>📦 ${escapeHtml(alert.container)}</span>
                        <span>⚠️ ${escapeHtml(alert.severity)}</span>
                        ${alert.risk_score !== 'N/A' ? `<span>🎯 Risk: ${alert.risk_score}/10</span>` : ''}
                        <span>🕐 ${alert.starts_at ? new Date(alert.starts_at).toLocaleString('uk-UA') : 'N/A'}</span>
                    </div>
                    ${alert.description ? `<div class="alert-desc">${escapeHtml(alert.description)}</div>` : ''}
                    ${alert.recommendations ? `<div class="alert-desc"><strong>💡 Recommendations:</strong> ${escapeHtml(alert.recommendations)}</div>` : ''}
                </div>
            `;
        }).join('');
    }

    // ---- Telegram History ----
    $('#btn-refresh-telegram-history').addEventListener('click', loadTelegramHistory);

    async function loadTelegramHistory() {
        const tbody = $('#telegram-history-tbody');
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state"><div class="spinner" style="margin:0 auto"></div></td></tr>';

        const data = await api('/api/telegram/history');
        if (!data) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Failed to load registry</td></tr>';
            return;
        }

        if (data.error) {
            tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color:var(--accent-red)">Error: ${escapeHtml(data.error)}</td></tr>`;
            return;
        }

        const history = data.history || [];
        if (history.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">✅ No incidents recorded in registry yet</td></tr>';
            return;
        }

        tbody.innerHTML = history.map((item) => {
            const riskClass = (item.severity === 'critical' || parseInt(item.risk_score) >= 7) ? 'error' : (parseInt(item.risk_score) >= 4) ? 'warning' : 'info';
            const riskText = item.risk_score && item.risk_score !== '0' ? `Risk: ${item.risk_score}/10` : item.severity;
            const statusClass = item.status === 'resolved' ? 'resolved' : 'firing';
            const statusText = item.status === 'resolved' ? 'Resolved' : 'Firing';

            return `
                <tr class="history-main-row" data-id="${item.id}">
                    <td>${item.start_time}</td>
                    <td><span class="${item.end_time === 'Active' ? 'status-badge firing' : ''}">${item.end_time}</span></td>
                    <td><code>${escapeHtml(item.container)}</code></td>
                    <td><strong>${escapeHtml(item.problem_type || item.alert_name)}</strong></td>
                    <td><span class="log-level ${riskClass}">${riskText}</span></td>
                    <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                    <td>
                        <button class="detail-toggle-btn" onclick="window.toggleTelegramDetail('${item.id}')">
                            Details
                        </button>
                    </td>
                </tr>
                <tr class="detail-row" id="telegram-detail-${item.id}" style="display:none">
                    <td colspan="7">
                        <div class="event-detail-container">
                            <!-- Left Column: Timeline & Logs -->
                            <div class="event-detail-col">
                                <div class="event-detail-card">
                                    <h4>Incident Timeline & Messages</h4>
                                    <div class="event-timeline">
                                        ${(item.messages || []).map(msg => `
                                            <div class="event-timeline-item ${msg.status === 'resolved' ? 'resolved' : ''}">
                                                <div class="event-timeline-meta">${msg.timestamp} [${msg.status.toUpperCase()}]</div>
                                                <div style="font-size:0.8rem; background:rgba(0,0,0,0.15); padding:8px; border-radius:4px; margin-top:4px">
                                                    ${msg.message}
                                                </div>
                                            </div>
                                        `).join('')}
                                    </div>
                                </div>
                                ${item.logs ? `
                                    <div class="event-detail-card">
                                        <h4>Triggering Container Logs</h4>
                                        <pre style="max-height:180px; overflow-y:auto; font-family:'JetBrains Mono',monospace; font-size:0.75rem; background:rgba(0,0,0,0.2); padding:10px; border-radius:4px; border:1px solid var(--border); white-space:pre-wrap; word-break:break-all">${item.logs}</pre>
                                    </div>
                                ` : ''}
                            </div>

                            <!-- Right Column: AI Retrospective & Correlation -->
                            <div class="event-detail-col">
                                <div class="event-detail-card" id="retro-card-${item.id}">
                                    <h4>
                                        <span>AI Retrospective</span>
                                        ${!item.retrospective ? `<button class="btn btn-xs btn-primary" onclick="window.conductRetrospective('${item.id}')">Run AI Analysis</button>` : ''}
                                    </h4>
                                    <div id="retro-content-${item.id}">
                                        ${item.retrospective ? formatRetrospectiveHtml(item.retrospective) : '<div class="empty-state">No retrospective conducted yet.</div>'}
                                    </div>
                                </div>

                                <div class="event-detail-card" id="correlation-card-${item.id}">
                                    <h4>
                                        <span>Log Correlation</span>
                                        ${!item.correlated_events ? `<button class="btn btn-xs btn-primary" onclick="window.runCorrelation('${item.id}')">Correlate</button>` : ''}
                                    </h4>
                                    <div id="correlation-content-${item.id}">
                                        ${item.correlated_events ? formatCorrelationHtml(item.correlated_events) : '<div class="empty-state">Run correlation to scan Loki for simultaneous logs across services.</div>'}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    }

    function formatRetrospectiveHtml(retro) {
        if (!retro) return '';
        
        const timelineLi = (retro.timeline || []).map(step => {
            if (typeof step === 'object') {
                const time = step.time || step.timestamp || '';
                const desc = step.step || step.event || step.description || '';
                return `<li>${time ? `<code>${escapeHtml(time)}</code>: ` : ''}${escapeHtml(desc)}</li>`;
            }
            return `<li>${escapeHtml(step)}</li>`;
        }).join('');
        
        const actionsLi = (retro.preventative_actions || []).map(action => `<li>${escapeHtml(action)}</li>`).join('');
        
        let impactText = '';
        if (typeof retro.impact_assessment === 'object') {
            impactText = Object.entries(retro.impact_assessment)
                .map(([k, v]) => `<strong>${escapeHtml(k)}</strong>: ${escapeHtml(v)}`)
                .join(' | ');
        } else {
            impactText = escapeHtml(retro.impact_assessment || 'N/A');
        }
        
        return `
            <div class="retro-section">
                <div class="retro-title">Root Cause Analysis (Першопричина)</div>
                <div class="retro-content">${escapeHtml(retro.root_cause || 'N/A')}</div>
            </div>
            <div class="retro-section">
                <div class="retro-title">Timeline & Investigation (Хронологія)</div>
                <ul style="padding-left:16px; margin:4px 0">${timelineLi}</ul>
            </div>
            <div class="retro-section">
                <div class="retro-title">Impact Assessment (Оцінка наслідків)</div>
                <div class="retro-content">${impactText}</div>
            </div>
            <div class="retro-section">
                <div class="retro-title">Preventative Actions (Рекомендовані заходи)</div>
                <ul style="padding-left:16px; margin:4px 0">${actionsLi}</ul>
            </div>
            <div class="retro-section" style="margin-bottom:0">
                <div class="retro-title">Correlation Summary (Зв'язок із паттернами загроз)</div>
                <div class="retro-content">${escapeHtml(retro.correlation_summary || 'N/A')}</div>
            </div>
        `;
    }

    function formatCorrelationHtml(corr) {
        if (!corr) return '';
        const events = corr.events || [];
        const logs = corr.logs || [];
        
        let html = '';
        
        if (events.length > 0) {
            html += '<div class="retro-title">Simultaneous Alerts (±10 хв)</div>';
            html += '<div class="correlation-list" style="margin-bottom:12px">';
            html += events.map(ev => `
                <div class="correlation-item">
                    🚨 <strong>${escapeHtml(ev.alert_name)}</strong> on <code>${escapeHtml(ev.container)}</code> (${ev.start_time}) - diff: ${ev.time_diff_seconds}s
                </div>
            `).join('');
            html += '</div>';
        } else {
            html += '<div class="retro-title">Simultaneous Alerts</div>';
            html += '<div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:12px">No concurrent alerts detected in registry.</div>';
        }
        
        if (logs.length > 0) {
            html += '<div class="retro-title">Correlated Error Logs in Loki (±5 хв)</div>';
            html += '<div class="correlation-list" style="max-height:200px; overflow-y:auto">';
            html += logs.map(log => `
                <div class="correlation-item log-item">
                    📁 <code>${escapeHtml(log.container)}</code> [${log.timestamp}]: <span style="font-family:monospace">${escapeHtml(log.message)}</span>
                </div>
            `).join('');
            html += '</div>';
        } else {
            html += '<div class="retro-title">Correlated Error Logs in Loki</div>';
            html += '<div style="font-size:0.8rem; color:var(--text-secondary)">No error/warning logs found on other containers during this timeframe.</div>';
        }
        
        return html;
    }

    window.conductRetrospective = async function(eventId) {
        const btn = document.querySelector(`#retro-card-${eventId} button`);
        if (btn) btn.disabled = true;
        const container = document.getElementById(`retro-content-${eventId}`);
        if (container) container.innerHTML = '<div class="empty-state"><div class="spinner" style="margin:0 auto"></div> Conducting AI Retrospective via Ollama (Llama-3)...</div>';
        
        const data = await api(`/api/telegram/history/${eventId}/retrospective`, { method: 'POST' });
        if (data && data.status === 'success' && data.retrospective) {
            if (container) container.innerHTML = formatRetrospectiveHtml(data.retrospective);
            if (btn) btn.remove();
        } else {
            if (container) container.innerHTML = '<div class="empty-state" style="color:var(--accent-red)">Failed to generate retrospective. Make sure Ollama is running and healthy.</div>';
            if (btn) btn.disabled = false;
        }
    };

    window.runCorrelation = async function(eventId) {
        const btn = document.querySelector(`#correlation-card-${eventId} button`);
        if (btn) btn.disabled = true;
        const container = document.getElementById(`correlation-content-${eventId}`);
        if (container) container.innerHTML = '<div class="empty-state"><div class="spinner" style="margin:0 auto"></div> Running Loki correlation scan...</div>';
        
        const data = await api(`/api/telegram/history/${eventId}/correlate`, { method: 'POST' });
        if (data && data.status === 'success' && data.correlated_events) {
            if (container) container.innerHTML = formatCorrelationHtml(data.correlated_events);
            if (btn) btn.remove();
        } else {
            if (container) container.innerHTML = '<div class="empty-state" style="color:var(--accent-red)">Failed to scan Loki logs.</div>';
            if (btn) btn.disabled = false;
        }
    };

    // Expose toggle function globally so inline onclick works
    window.toggleTelegramDetail = function(id) {
        const detailRow = document.getElementById(`telegram-detail-${id}`);
        if (detailRow) {
            if (detailRow.style.display === 'none') {
                detailRow.style.display = 'table-row';
            } else {
                detailRow.style.display = 'none';
            }
        }
    };

    // ---- AI Chat ----
    const chatInput = $('#chat-input');
    const chatMessages = $('#chat-messages');

    $('#btn-send-chat').addEventListener('click', sendChatMessage);
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
    });

    async function sendChatMessage() {
        const msg = chatInput.value.trim();
        if (!msg) return;

        // Add user message
        chatMessages.innerHTML += `
            <div class="chat-message user">
                <div class="chat-avatar user-avatar">RK</div>
                <div class="chat-bubble"><p>${escapeHtml(msg)}</p></div>
            </div>
        `;
        chatInput.value = '';
        chatMessages.scrollTop = chatMessages.scrollHeight;

        // Loading indicator
        const loadingId = 'chat-loading-' + Date.now();
        chatMessages.innerHTML += `
            <div class="chat-message bot" id="${loadingId}">
                <div class="chat-avatar bot-avatar">AI</div>
                <div class="chat-bubble"><div class="spinner" style="width:20px;height:20px;margin:0;border-width:2px"></div></div>
            </div>
        `;
        chatMessages.scrollTop = chatMessages.scrollHeight;

        const data = await api('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg }),
        });

        // Remove loading
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) loadingEl.remove();

        const response = data?.response || 'Sorry, I could not process your request.';

        chatMessages.innerHTML += `
            <div class="chat-message bot">
                <div class="chat-avatar bot-avatar">AI</div>
                <div class="chat-bubble">${formatChatResponse(response)}</div>
            </div>
        `;
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function formatChatResponse(text) {
        // Basic markdown-like formatting
        let html = escapeHtml(text);
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/`(.*?)`/g, '<code>$1</code>');
        html = html.replace(/\n\n/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');
        return `<p>${html}</p>`;
    }

    // ---- Utilities ----
    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ---- Init ----
    const initPage = window.location.hash.slice(1) || 'dashboard';
    navigateTo(initPage);

    // Auto-refresh dashboard every 30s
    refreshInterval = setInterval(() => {
        if (currentPage === 'dashboard') loadDashboard();
    }, 30000);

})();
