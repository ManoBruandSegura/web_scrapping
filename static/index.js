// Frontend application code for Leboncoin Scraper Web App

document.addEventListener('DOMContentLoaded', () => {
    // Current application state
    const state = {
        activeTab: 'dashboard',
        isScraping: false,
        totalListings: 0,
        logs: [],
        queries: [] // Array of Query objects
    };

    let priceChart = null; // Chart.js instance for the dashboard price chart

    // DOM Elements
    const navItems = document.querySelectorAll('.nav-item');
    const tabPanels = document.querySelectorAll('.tab-panel');
    const pageTitle = document.getElementById('page-title');
    const pageSubtitle = document.getElementById('page-subtitle');
    
    const btnScrapeNow = document.getElementById('btn-scrape-now');
    const autoScraperCheckbox = document.getElementById('auto-scraper-checkbox');
    const connectionStatus = document.getElementById('connection-status');
    const navBadgeCount = document.getElementById('nav-badge-count');
    
    // Dashboard widgets
    const statusText = document.getElementById('status-text');
    const statusIndicator = document.getElementById('status-indicator');
    const statusSubtext = document.getElementById('status-subtext');
    const statTotalCount = document.getElementById('stat-total-count');
    const statLastRun = document.getElementById('stat-last-run');
    const statTimeElapsed = document.getElementById('stat-time-elapsed');
    const statNextRun = document.getElementById('stat-next-run');
    const statCountdown = document.getElementById('stat-countdown');
    
    const btnEditConfig = document.getElementById('btn-edit-config');
    const linkGotoLogs = document.getElementById('link-goto-logs');
    const miniLogs = document.getElementById('mini-logs');
    const cardTotalScraped = document.getElementById('card-total-scraped');
    
    // Listings Panel
    const listingsSearch = document.getElementById('listings-search');
    const listingsSort = document.getElementById('listings-sort');
    const listingsQueryFilter = document.getElementById('listings-query-filter');
    const btnRefreshListings = document.getElementById('btn-refresh-listings');
    const btnResetListings = document.getElementById('btn-reset-listings');
    const listingsGrid = document.getElementById('listings-grid');
    
    // Settings Panel Global
    const settingsGlobalForm = document.getElementById('settings-global-form');
    const btnTestWebhook = document.getElementById('btn-test-webhook');
    const discordWebhookInput = document.getElementById('discord_webhook');
    
    // Settings Panel Queries
    const queriesListContainer = document.getElementById('queries-list');
    const btnAddQuery = document.getElementById('btn-add-query');
    
    // Query Modal
    const queryModal = document.getElementById('query-modal');
    const queryForm = document.getElementById('query-form');
    const btnCloseModal = document.getElementById('btn-close-modal');
    const queryModalTitle = document.getElementById('query-modal-title');
    const modalQueryContainer = document.getElementById('modal-query-container');
    const modalUrlContainer = document.getElementById('modal-url-container');
    const radioQueryModes = document.querySelectorAll('input[name="query_mode"]');
    
    // Logs Panel
    const btnClearLogs = document.getElementById('btn-clear-logs');
    const logsContainer = document.getElementById('logs-container');
    
    // Toast notifications
    const toastContainer = document.getElementById('toast-container');

    /* ==========================================================================
       TAB MANAGEMENT
       ========================================================================== */
    function switchTab(tabId) {
        state.activeTab = tabId;
        
        // Update sidebar links
        navItems.forEach(item => {
            if (item.getAttribute('data-tab') === tabId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
        
        // Show active panel
        tabPanels.forEach(panel => {
            if (panel.id === `tab-${tabId}`) {
                panel.classList.add('active');
            } else {
                panel.classList.remove('active');
            }
        });
        
        // Update titles
        switch(tabId) {
            case 'dashboard':
                pageTitle.textContent = 'Dashboard';
                pageSubtitle.textContent = 'Overview of scraping activity and status';
                btnScrapeNow.classList.remove('hidden');
                break;
            case 'listings':
                pageTitle.textContent = 'Scraped Listings';
                pageSubtitle.textContent = 'Catalog of all discovered LBC listings';
                btnScrapeNow.classList.remove('hidden');
                fetchListings();
                break;
            case 'settings':
                pageTitle.textContent = 'Scraper Settings';
                pageSubtitle.textContent = 'Configure search parameters and alerts';
                btnScrapeNow.classList.add('hidden');
                fetchConfigFormValues();
                break;
            case 'logs':
                pageTitle.textContent = 'System Terminal';
                pageSubtitle.textContent = 'Real-time output stream of backend processes';
                btnScrapeNow.classList.remove('hidden');
                fetchLogs();
                break;
        }
    }

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(item.getAttribute('data-tab'));
        });
    });

    // Cross links
    btnEditConfig.addEventListener('click', () => switchTab('settings'));
    linkGotoLogs.addEventListener('click', (e) => {
        e.preventDefault();
        switchTab('logs');
    });
    if (cardTotalScraped) {
        cardTotalScraped.addEventListener('click', () => switchTab('listings'));
    }

    // Quick-toggle scraper execution mode
    btnScrapeNow.addEventListener('click', triggerManualScrape);

    /* ==========================================================================
       NOTIFICATION / TOAST SYSTEM
       ========================================================================== */
    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        let iconClass = 'fa-circle-info';
        if (type === 'success') iconClass = 'fa-circle-check';
        if (type === 'error') iconClass = 'fa-circle-exclamation';
        
        toast.innerHTML = `
            <i class="fa-solid ${iconClass}"></i>
            <span>${message}</span>
            <i class="fa-solid fa-xmark toast-close"></i>
        `;
        
        toastContainer.appendChild(toast);
        
        // Setup delete trigger
        const closeBtn = toast.querySelector('.toast-close');
        closeBtn.addEventListener('click', () => {
            toast.remove();
        });
        
        setTimeout(() => {
            toast.style.animation = 'fadeIn 0.3s ease reverse';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    /* ==========================================================================
       POLLING & STATE UPDATES (DASHBOARD)
       ========================================================================== */
    let isTogglingAuto = false;

    // Toggle auto scraper via sidebar
    autoScraperCheckbox.addEventListener('change', async () => {
        if (isTogglingAuto) return;
        isTogglingAuto = true;
        
        try {
            const res = await fetch('/api/toggle', { method: 'POST' });
            if (!res.ok) throw new Error('API return code failure');
            const data = await res.json();
            
            autoScraperCheckbox.checked = data.is_running;
            showToast(`Auto-scraper ${data.is_running ? 'started' : 'stopped'}.`, 'success');
            pollStatus();
        } catch (err) {
            showToast('Could not toggle auto-scraper.', 'error');
            autoScraperCheckbox.checked = !autoScraperCheckbox.checked; // Revert
        } finally {
            isTogglingAuto = false;
        }
    });

    async function pollStatus() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) throw new Error('Disconnected');
            const data = await res.json();
            
            // Set Connection Indicator
            connectionStatus.className = 'status-pill status-connected';
            connectionStatus.innerHTML = '<span class="dot"></span> Connected';
            
            // Update Auto Scraper toggle switch
            if (!isTogglingAuto) {
                autoScraperCheckbox.checked = data.status.is_running;
            }
            
            // System status indicators
            state.isScraping = data.status.is_scraping;
            
            if (data.status.blocked_until && new Date() < new Date(data.status.blocked_until)) {
                const blockedDate = new Date(data.status.blocked_until);
                const secondsLeft = Math.max(0, Math.round((blockedDate - new Date()) / 1000));
                const mins = Math.floor(secondsLeft / 60);
                const secs = secondsLeft % 60;
                
                statusText.textContent = 'IP Blocked';
                statusIndicator.className = 'status-indicator-dot dot-gray'; // We will style this with red dynamically if dot-red is missing or just use dot-gray and inline style
                statusIndicator.style.backgroundColor = 'var(--danger)';
                statusSubtext.textContent = `Cooldown for ${mins}m ${secs}s`;
                
                btnScrapeNow.disabled = false;
                btnScrapeNow.dataset.force = "true";
                btnScrapeNow.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Force Scrape';
                btnScrapeNow.style.backgroundColor = 'var(--danger)';
                btnScrapeNow.style.borderColor = 'var(--danger)';
            } else if (data.status.is_scraping) {
                statusIndicator.style.backgroundColor = ''; // Reset inline style
                statusText.textContent = 'Scraping...';
                statusIndicator.className = 'status-indicator-dot dot-blue';
                statusSubtext.textContent = 'Scraper running in background';
                
                // Add spinning classes to buttons
                btnScrapeNow.disabled = true;
                btnScrapeNow.dataset.force = "false";
                btnScrapeNow.style.backgroundColor = '';
                btnScrapeNow.style.borderColor = '';
                btnScrapeNow.innerHTML = '<i class="fa-solid fa-arrows-rotate spin"></i> Scraping';
            } else if (data.status.is_running) {
                statusIndicator.style.backgroundColor = ''; // Reset inline style
                statusText.textContent = 'Active';
                statusIndicator.className = 'status-indicator-dot dot-green';
                statusSubtext.textContent = 'Monitoring background cycles';
                
                btnScrapeNow.disabled = false;
                btnScrapeNow.dataset.force = "false";
                btnScrapeNow.style.backgroundColor = '';
                btnScrapeNow.style.borderColor = '';
                btnScrapeNow.innerHTML = '<i class="fa-solid fa-play"></i> Scrape Now';
            } else {
                statusIndicator.style.backgroundColor = ''; // Reset inline style
                statusText.textContent = 'Idle';
                statusIndicator.className = 'status-indicator-dot dot-gray';
                statusSubtext.textContent = 'Poller is disabled';
                
                btnScrapeNow.disabled = false;
                btnScrapeNow.dataset.force = "false";
                btnScrapeNow.style.backgroundColor = '';
                btnScrapeNow.style.borderColor = '';
                btnScrapeNow.innerHTML = '<i class="fa-solid fa-play"></i> Scrape Now';
            }
            
            // Update stats
            state.totalListings = data.stats.total_listings;
            statTotalCount.textContent = data.stats.total_listings;
            navBadgeCount.textContent = data.stats.unseen_count;
            
            // Last run time
            if (data.stats.last_run) {
                const lastRunDate = new Date(data.stats.last_run);
                statLastRun.textContent = lastRunDate.toLocaleTimeString();
                statTimeElapsed.textContent = getRelativeTime(lastRunDate);
            } else {
                statLastRun.textContent = 'Never';
                statTimeElapsed.textContent = 'Waiting for first execution';
            }
            
            // Next run countdown
            if (data.status.blocked_until && new Date() < new Date(data.status.blocked_until)) {
                statNextRun.textContent = 'Paused';
                statCountdown.textContent = 'Awaiting unblock';
            } else if (data.stats.next_run && data.status.is_running) {
                const nextRunDate = new Date(data.stats.next_run);
                statNextRun.textContent = nextRunDate.toLocaleTimeString();
                
                const secondsLeft = Math.max(0, Math.round((nextRunDate - new Date()) / 1000));
                const mins = Math.floor(secondsLeft / 60);
                const secs = secondsLeft % 60;
                statCountdown.textContent = `Scrape in ${mins}m ${secs}s`;
            } else {
                statNextRun.textContent = 'N/A';
                statCountdown.textContent = 'Auto-scraper inactive';
            }
            
            // Update Config summaries on Dashboard
            const cfg = data.config;
            const activeQueries = cfg.queries ? cfg.queries.filter(q => q.enabled !== false).length : 0;
            document.getElementById('summary-queries-count').textContent = activeQueries;
            
            document.getElementById('summary-interval').textContent = `Every ${cfg.interval_minutes} minutes`;
            document.getElementById('summary-headless').textContent = cfg.headless ? 'Headless' : 'Visible Window';
            
            if (cfg.discord_webhook && cfg.discord_webhook.trim()) {
                document.getElementById('summary-discord').innerHTML = '<span class="badge badge-success">Active</span>';
            } else {
                document.getElementById('summary-discord').innerHTML = '<span class="badge badge-error">Disabled</span>';
            }
            
            // Update Query Stats panel
            const queryStatsTbody = document.getElementById('query-stats-tbody');
            if (queryStatsTbody) {
                if (Object.keys(data.stats.per_query || {}).length === 0) {
                    queryStatsTbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-faint);">No data</td></tr>';
                } else {
                    queryStatsTbody.innerHTML = '';
                    const qMap = {};
                    (cfg.queries || []).forEach(q => qMap[q.id] = q.name);
                    
                    for (const [qId, s] of Object.entries(data.stats.per_query)) {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td>${escapeHtml(qMap[qId] || qId)}</td>
                            <td>${s.count}</td>
                            <td>${s.min.toLocaleString()} €</td>
                            <td style="font-weight: 600; color: var(--accent);">${s.median.toLocaleString()} €</td>
                            <td>${Math.round(s.avg).toLocaleString()} €</td>
                        `;
                        queryStatsTbody.appendChild(tr);
                    }
                }
            }

            renderPriceChart(data.stats.per_query || {});
            
            // Keep state queries in sync to prevent missing filter dropdown data
            if (state.queries.length === 0 && cfg.queries) {
                state.queries = cfg.queries;
                updateListingsFilterDropdown();
            }
            
        } catch (err) {
            connectionStatus.className = 'status-pill disconnected';
            connectionStatus.innerHTML = '<span class="dot"></span> Disconnected';
            statusText.textContent = 'Offline';
            statusIndicator.className = 'status-indicator-dot dot-gray';
            statusSubtext.textContent = 'Failed to connect to backend';
        } finally {
            fetchRunHistory();
        }
    }
    
    async function fetchRunHistory() {
        if (state.activeTab !== 'dashboard') return;
        try {
            const res = await fetch('/api/runs?limit=10');
            if (!res.ok) return;
            const runs = await res.json();
            
            const tbody = document.getElementById('run-history-tbody');
            if (!tbody) return;
            
            if (runs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-faint);">No runs yet</td></tr>';
                return;
            }
            
            tbody.innerHTML = '';
            runs.forEach(run => {
                const tr = document.createElement('tr');
                const timeStr = new Date(run.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                
                let statusHtml = '<span class="badge badge-success">Success</span>';
                if (run.blocked) {
                    statusHtml = '<span class="badge badge-error">Blocked</span>';
                } else if (run.error) {
                    statusHtml = '<span class="badge badge-error" title="'+escapeHtml(run.error)+'">Error</span>';
                } else if (!run.finished_at) {
                    statusHtml = '<span class="badge" style="background:var(--blue);color:white">Running</span>';
                }
                
                tr.innerHTML = `
                    <td>${timeStr}</td>
                    <td>${statusHtml}</td>
                    <td>${run.items_seen}</td>
                    <td style="${run.items_new > 0 ? 'font-weight:bold;color:var(--accent);' : ''}">${run.items_new}</td>
                    <td style="color:var(--text-faint); font-size:0.75rem;">${run.blocked ? 'IP Blocked' : (run.error ? 'Error' : '')}</td>
                `;
                tbody.appendChild(tr);
            });
            
        } catch (err) {
            console.error('Failed to fetch run history', err);
        }
    }

    async function triggerManualScrape() {
        if (state.isScraping) return;
        
        try {
            const isForce = btnScrapeNow.dataset.force === "true";
            const url = isForce ? '/api/scrape?force=true' : '/api/scrape';
            const res = await fetch(url, { method: 'POST' });
            if (!res.ok) throw new Error();
            showToast(isForce ? 'Forced manual scrape cycle initiated.' : 'Manual scrape cycle initiated.', 'info');
            pollStatus();
        } catch (err) {
            showToast('Failed to trigger scrape cycle.', 'error');
        }
    }

    // Helper: relative time
    function getRelativeTime(date) {
        const seconds = Math.floor((new Date() - date) / 1000);
        if (seconds < 60) return 'Just now';
        
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes} minute(s) ago`;
        
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours} hour(s) ago`;
        
        return date.toLocaleDateString();
    }

    /* ==========================================================================
       LISTINGS RENDERING
       ========================================================================== */
    let listingsData = [];

    async function fetchListings() {
        const searchQuery = listingsSearch.value.trim();
        const sortType = listingsSort.value;
        
        try {
            btnRefreshListings.querySelector('i').classList.add('spin');
            
            const params = new URLSearchParams();
            if (searchQuery) params.append('search', searchQuery);
            if (sortType) params.append('sort', sortType);
            
            const res = await fetch(`/api/listings?${params.toString()}`);
            if (!res.ok) throw new Error();
            listingsData = await res.json();
            
            renderListings(listingsData);
        } catch (err) {
            showToast('Could not fetch listings database.', 'error');
        } finally {
            btnRefreshListings.querySelector('i').classList.remove('spin');
        }
    }

    function renderListings(items) {
        const queryFilter = listingsQueryFilter.value;
        const dealsOnly = document.getElementById('listings-deals-only').checked;
        
        let filteredItems = items;
        if (queryFilter && queryFilter !== 'all') {
            filteredItems = filteredItems.filter(i => i.query_id === queryFilter);
        }
        if (dealsOnly) {
            filteredItems = filteredItems.filter(i => i.is_deal === 1);
        }
        
        listingsGrid.innerHTML = '';
        const unseenUrls = [];
        
        if (filteredItems.length === 0) {
            listingsGrid.innerHTML = `
                <div class="listings-empty-state">
                    <i class="fa-solid fa-folder-open"></i>
                    <p>No listings match your filter criteria.</p>
                </div>
            `;
            return;
        }
        
        filteredItems.forEach(item => {
            const isUnseen = item.viewed === 0;
            if (isUnseen) unseenUrls.push(item.url);
            
            const card = document.createElement('div');
            card.className = 'listing-card' + (isUnseen ? ' unseen' : '');
            
            // Format dates
            const firstSeenStr = item.first_seen 
                ? new Date(item.first_seen).toLocaleString() 
                : 'Unknown';
            
            const publishedStr = item.published_date 
                ? new Date(item.published_date).toLocaleString() 
                : 'Unknown';
                
            const publishedRelative = item.published_date 
                ? getRelativeTime(new Date(item.published_date)) 
                : 'Unknown';
            
            // Sparkline and Drop Badge
            let sparklineSvg = '';
            let dropBadge = '';
            
            if (item.price_history && item.price_history.length >= 2) {
                const history = item.price_history;
                const latest = history[history.length - 1].price_value;
                const prev = history[history.length - 2].price_value;
                
                // SVG dimensions
                const width = 80;
                const height = 24;
                const minPrice = Math.min(...history.map(h => h.price_value));
                const maxPrice = Math.max(...history.map(h => h.price_value));
                const range = maxPrice - minPrice || 1;
                
                const points = history.map((h, i) => {
                    const x = (i / (history.length - 1)) * width;
                    const y = height - ((h.price_value - minPrice) / range) * height;
                    return `${x},${y}`;
                }).join(' ');
                
                const color = latest < prev ? 'var(--accent)' : (latest > prev ? 'var(--danger)' : 'var(--text-faint)');
                
                sparklineSvg = `
                    <svg width="${width}" height="${height}" viewBox="0 -2 ${width} ${height + 4}" style="overflow: visible; flex-shrink: 0;">
                        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                `;
                
                if (latest < prev) {
                    const pct = Math.round((prev - latest) / prev * 100);
                    dropBadge = `<span style="color: var(--accent); font-size: 0.85rem; font-weight: 600; margin-left: 6px;" title="Price dropped">▼ -${pct}%</span>`;
                } else if (latest > prev) {
                    const pct = Math.round((latest - prev) / prev * 100);
                    dropBadge = `<span style="color: var(--danger); font-size: 0.85rem; font-weight: 600; margin-left: 6px;" title="Price increased">▲ +${pct}%</span>`;
                }
            }

            const thumbnailHtml = item.thumbnail_url 
                ? `<img src="${escapeHtml(item.thumbnail_url)}" alt="Thumbnail" loading="lazy" style="width: 100%; aspect-ratio: 4/3; object-fit: cover; border-bottom: 1px solid var(--border); display: block;">`
                : `<div style="width: 100%; aspect-ratio: 4/3; background-color: var(--surface-2); display: flex; align-items: center; justify-content: center; border-bottom: 1px solid var(--border); color: var(--text-faint);"><i class="fa-regular fa-image fa-2x"></i></div>`;
                
            const locHtml = item.location
                ? `<div class="listing-location" title="Location: ${escapeHtml(item.location)}" style="color: var(--text-faint); font-size: 0.85rem; margin-top: 8px; display: flex; align-items: center; gap: 4px;">
                     <i class="fa-solid fa-location-dot"></i> <span>${escapeHtml(item.location)}</span>
                   </div>`
                : '';
                
            let dealBadgeHtml = '';
            if (item.is_deal) {
                dealBadgeHtml = `<span style="background: var(--danger); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-left: 8px;">🔥 Good Deal</span>`;
            }
            
            const newBadgeHtml = isUnseen ? `<div class="listing-badge-new">NEW</div>` : '';
            
            card.innerHTML = `
                ${thumbnailHtml}
                ${newBadgeHtml}
                <div class="listing-card-body" style="padding-top: 16px;">
                    <div class="listing-card-header" style="flex-direction: column; align-items: flex-start;">
                        <h4 class="listing-card-title" title="${escapeHtml(item.title)}" style="width: 100%; margin-bottom: 8px;">${escapeHtml(item.title)}</h4>
                        <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
                            <div style="display: flex; align-items: center;">
                                <span class="listing-price-badge">${escapeHtml(item.price)}</span>
                                ${dealBadgeHtml}
                                ${dropBadge}
                            </div>
                            ${sparklineSvg}
                        </div>
                        ${locHtml}
                    </div>
                </div>
                <div class="listing-card-footer" style="flex-wrap: wrap; padding-top: 12px; border-top: 1px solid var(--border);">
                    <div style="display: flex; gap: 10px; width: 100%;">
                        <div class="listing-date" title="Published: ${publishedStr}">
                            <i class="fa-solid fa-calendar-day"></i>
                            <span>${publishedRelative}</span>
                        </div>
                        <div class="listing-date" title="First seen by scraper: ${firstSeenStr}">
                            <i class="fa-solid fa-spider"></i>
                            <span>${getRelativeTime(new Date(item.first_seen))}</span>
                        </div>
                    </div>
                    <a href="${escapeHtml(item.url)}" target="_blank" class="btn btn-secondary btn-view-listing mt-2" style="width: 100%; justify-content: center;">
                        View Item <i class="fa-solid fa-up-right-from-square"></i>
                    </a>
                </div>
            `;
            listingsGrid.appendChild(card);
        });
        
        if (unseenUrls.length > 0) {
            setTimeout(() => {
                fetch('/api/listings/mark-viewed', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ urls: unseenUrls })
                }).then(() => {
                    unseenUrls.forEach(url => {
                        const idx = listingsData.findIndex(i => i.url === url);
                        if (idx !== -1) listingsData[idx].viewed = 1;
                    });
                    pollStatus();
                    document.querySelectorAll('.listing-card.unseen').forEach(el => {
                        el.classList.remove('unseen');
                        const badge = el.querySelector('.listing-badge-new');
                        if (badge) badge.remove();
                    });
                }).catch(err => console.error(err));
            }, 2000);
        }
    }

    // Debounce for search
    let searchTimeout;
    listingsSearch.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(fetchListings, 400);
    });

    listingsSort.addEventListener('change', fetchListings);
    btnRefreshListings.addEventListener('click', fetchListings);
    
    btnResetListings.addEventListener('click', async () => {
        if (!confirm('Are you sure you want to delete ALL listings? This action cannot be undone.')) return;
        try {
            const res = await fetch('/api/listings', { method: 'DELETE' });
            if (!res.ok) throw new Error('Failed to delete listings');
            showToast('All listings cleared successfully.', 'success');
            fetchListings();
            pollStatus(); // Update dashboard counts
        } catch (err) {
            showToast('Failed to reset listings.', 'error');
        }
    });
    
    listingsQueryFilter.addEventListener('change', () => {
        renderListings(listingsData);
    });
    
    document.getElementById('listings-deals-only').addEventListener('change', () => {
        renderListings(listingsData);
    });

    function updateListingsFilterDropdown() {
        const currentValue = listingsQueryFilter.value;
        listingsQueryFilter.innerHTML = '<option value="all">All Queries</option>';
        state.queries.forEach(q => {
            const opt = document.createElement('option');
            opt.value = q.id;
            opt.textContent = q.name;
            listingsQueryFilter.appendChild(opt);
        });
        listingsQueryFilter.value = currentValue || 'all';
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    /* ==========================================================================
       SETTINGS & CONFIG FORM (MULTI-QUERY)
       ========================================================================== */

    async function fetchConfigFormValues() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            const config = data.config;
            
            state.queries = config.queries || [];
            updateListingsFilterDropdown();
            renderQueriesList();
            
            // Set global values
            document.getElementById('interval_minutes').value = config.interval_minutes || 5;
            document.getElementById('headless').checked = config.headless !== false;
            discordWebhookInput.value = config.discord_webhook || '';
            document.getElementById('deal_threshold_pct').value = config.deal_threshold_pct || 25;
            document.getElementById('deal_min_sample').value = config.deal_min_sample || 5;
            document.getElementById('ntfy_topic').value = config.ntfy_topic || '';
            document.getElementById('active_start').value = (config.active_start ?? '');
            document.getElementById('active_end').value = (config.active_end ?? '');
            document.getElementById('proxy').value = config.proxy || '';
            document.getElementById('archive_images').checked = config.archive_images === true;
            
        } catch (err) {
            showToast('Failed to load active scraper configuration.', 'error');
        }
    }

    function renderPriceChart(perQuery) {
        const canvas = document.getElementById('price-chart');
        if (!canvas || typeof Chart === 'undefined') return;

        const qMap = {};
        (state.queries || []).forEach(q => qMap[q.id] = q.name);
        const entries = Object.entries(perQuery);
        const labels = entries.map(([qId]) => qMap[qId] || qId);
        const mins = entries.map(([, s]) => Math.round(s.min));
        const medians = entries.map(([, s]) => Math.round(s.median));
        const avgs = entries.map(([, s]) => Math.round(s.avg));

        if (!priceChart) {
            priceChart = new Chart(canvas, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Min', data: mins, backgroundColor: 'rgba(56, 189, 248, 0.7)' },
                        { label: 'Median', data: medians, backgroundColor: 'rgba(139, 92, 246, 0.85)' },
                        { label: 'Avg', data: avgs, backgroundColor: 'rgba(148, 163, 184, 0.6)' }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#cbd5e1' } } },
                    scales: {
                        x: { ticks: { color: '#cbd5e1' }, grid: { color: 'rgba(148,163,184,0.1)' } },
                        y: { ticks: { color: '#cbd5e1', callback: v => v + ' €' }, grid: { color: 'rgba(148,163,184,0.1)' } }
                    }
                }
            });
        } else {
            priceChart.data.labels = labels;
            priceChart.data.datasets[0].data = mins;
            priceChart.data.datasets[1].data = medians;
            priceChart.data.datasets[2].data = avgs;
            priceChart.update('none'); // no animation on the 2.5s refresh
        }
    }

    function renderQueriesList() {
        queriesListContainer.innerHTML = '';
        
        if (state.queries.length === 0) {
            queriesListContainer.innerHTML = '<div class="empty-queries-state">No queries defined yet. Click "Add Query" to start!</div>';
            return;
        }
        
        state.queries.forEach(q => {
            const card = document.createElement('div');
            card.className = 'query-card';
            
            const isEnabled = q.enabled !== false;
            
            card.innerHTML = `
                <div class="query-info">
                    <span class="query-name">
                        ${escapeHtml(q.name)} 
                        <span class="badge ${isEnabled ? 'badge-success' : 'badge-error'}">${isEnabled ? 'Active' : 'Paused'}</span>
                        ${q.notify === false ? '<i class="fa-solid fa-bell-slash text-danger" title="Notifications Muted" style="margin-left: 6px;"></i>' : ''}
                    </span>
                    <span class="query-details">
                        ${q.mode === 'url' ? 'Custom URL' : escapeHtml(q.query || 'No terms specified')}
                        ${q.required_keywords || q.excluded_keywords ? `<br><span class="text-secondary" style="font-size:0.75rem; display:inline-block; margin-top:2px;">` : ''}
                        ${q.required_keywords ? `<strong>Req:</strong> [${escapeHtml(q.required_keywords)}] ` : ''}
                        ${q.excluded_keywords ? `<strong>Ex:</strong> [${escapeHtml(q.excluded_keywords)}]` : ''}
                        ${q.required_keywords || q.excluded_keywords ? `</span>` : ''}
                    </span>
                </div>
                <div class="query-actions">
                    <label class="switch" title="Toggle execution for this query">
                        <input type="checkbox" class="toggle-query-cb" data-id="${q.id}" ${isEnabled ? 'checked' : ''}>
                        <span class="slider round"></span>
                    </label>
                    <button type="button" class="btn-icon edit-query-btn" data-id="${q.id}" title="Edit Query"><i class="fa-solid fa-pen"></i></button>
                    <button type="button" class="btn-icon text-danger delete-query-btn" data-id="${q.id}" title="Delete Query"><i class="fa-solid fa-trash"></i></button>
                </div>
            `;
            queriesListContainer.appendChild(card);
        });
        
        // Bind events
        queriesListContainer.querySelectorAll('.toggle-query-cb').forEach(cb => {
            cb.addEventListener('change', (e) => toggleQueryStatus(e.target.getAttribute('data-id'), e.target.checked));
        });
        queriesListContainer.querySelectorAll('.edit-query-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const id = e.currentTarget.getAttribute('data-id');
                const query = state.queries.find(q => q.id === id);
                if (query) openQueryModal(query);
            });
        });
        queriesListContainer.querySelectorAll('.delete-query-btn').forEach(btn => {
            btn.addEventListener('click', (e) => deleteQuery(e.currentTarget.getAttribute('data-id')));
        });
    }

    // Modal Interaction
    radioQueryModes.forEach(radio => {
        radio.addEventListener('change', () => {
            if (radio.value === 'query') {
                modalQueryContainer.classList.remove('hidden');
                modalUrlContainer.classList.add('hidden');
            } else {
                modalQueryContainer.classList.add('hidden');
                modalUrlContainer.classList.remove('hidden');
            }
        });
    });

    function openQueryModal(query = null) {
        if (query) {
            queryModalTitle.textContent = 'Edit Query';
            document.getElementById('query-id').value = query.id;
            document.getElementById('query-name').value = query.name || '';
            
            const modeRadio = document.querySelector(`input[name="query_mode"][value="${query.mode}"]`);
            if (modeRadio) {
                modeRadio.checked = true;
                modeRadio.dispatchEvent(new Event('change'));
            }
            
            document.getElementById('query-text').value = query.query || '';
            document.getElementById('query-url').value = query.custom_url || '';
            document.getElementById('query-min').value = query.price_min !== null ? query.price_min : '';
            document.getElementById('query-max').value = query.price_max !== null ? query.price_max : '';
            document.getElementById('query-shippable').checked = query.shippable !== false;
            document.getElementById('query-required-keywords').value = query.required_keywords || '';
            document.getElementById('query-excluded-keywords').value = query.excluded_keywords || '';
            document.getElementById('query-notify').checked = query.notify !== false;
            document.getElementById('query-webhook-override').value = query.webhook_override || '';
        } else {
            queryModalTitle.textContent = 'Add New Query';
            queryForm.reset();
            document.getElementById('query-id').value = '';
            document.getElementById('query-required-keywords').value = '';
            document.getElementById('query-excluded-keywords').value = '';
            document.getElementById('query-notify').checked = true;
            document.getElementById('query-webhook-override').value = '';
            document.querySelector(`input[name="query_mode"][value="query"]`).checked = true;
            document.querySelector(`input[name="query_mode"][value="query"]`).dispatchEvent(new Event('change'));
        }
        
        queryModal.classList.remove('hidden');
    }

    btnCloseModal.addEventListener('click', () => {
        queryModal.classList.add('hidden');
    });

    btnAddQuery.addEventListener('click', () => openQueryModal(null));

    // Form Submit for a Single Query
    queryForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const id = document.getElementById('query-id').value || 'q_' + Math.random().toString(36).substring(2, 9);
        const name = document.getElementById('query-name').value.trim();
        const mode = document.querySelector('input[name="query_mode"]:checked').value;
        const queryStr = document.getElementById('query-text').value;
        const custom_url = document.getElementById('query-url').value;
        const price_min_val = document.getElementById('query-min').value;
        const price_max_val = document.getElementById('query-max').value;
        const shippable = document.getElementById('query-shippable').checked;
        const required_keywords = document.getElementById('query-required-keywords').value.trim();
        const excluded_keywords = document.getElementById('query-excluded-keywords').value.trim();
        const notify = document.getElementById('query-notify').checked;
        const webhook_override = document.getElementById('query-webhook-override').value.trim();
        
        const price_min = price_min_val !== '' ? parseInt(price_min_val) : null;
        const price_max = price_max_val !== '' ? parseInt(price_max_val) : null;
        
        const newQuery = {
            id, name, mode, query: queryStr, custom_url, price_min, price_max, shippable, enabled: true,
            required_keywords, excluded_keywords, notify, webhook_override
        };
        
        const idx = state.queries.findIndex(q => q.id === id);
        if (idx >= 0) {
            newQuery.enabled = state.queries[idx].enabled;
            state.queries[idx] = newQuery;
        } else {
            state.queries.push(newQuery);
        }
        
        await saveGlobalConfig();
        queryModal.classList.add('hidden');
        showToast(`Query "${name}" saved!`, 'success');
    });

    async function toggleQueryStatus(id, enabled) {
        const query = state.queries.find(q => q.id === id);
        if (query) {
            query.enabled = enabled;
            await saveGlobalConfig();
            showToast(`Query "${query.name}" ${enabled ? 'enabled' : 'disabled'}.`, 'info');
        }
    }

    async function deleteQuery(id) {
        if (!confirm('Are you sure you want to delete this query?')) return;
        state.queries = state.queries.filter(q => q.id !== id);
        await saveGlobalConfig();
        showToast('Query deleted.', 'success');
    }

    // Save Global Configuration to backend
    async function saveGlobalConfig() {
        const interval_minutes = parseInt(document.getElementById('interval_minutes').value) || 5;
        const headless = document.getElementById('headless').checked;
        const discord_webhook = discordWebhookInput.value.trim();
        const deal_threshold_pct = parseInt(document.getElementById('deal_threshold_pct').value) || 25;
        const deal_min_sample = parseInt(document.getElementById('deal_min_sample').value) || 5;
        const ntfy_topic = document.getElementById('ntfy_topic').value.trim();
        const startRaw = document.getElementById('active_start').value;
        const endRaw = document.getElementById('active_end').value;
        const active_start = startRaw === '' ? null : parseInt(startRaw);
        const active_end = endRaw === '' ? null : parseInt(endRaw);
        const proxy = document.getElementById('proxy').value.trim();
        const archive_images = document.getElementById('archive_images').checked;

        const payload = {
            queries: state.queries,
            interval_minutes,
            headless,
            discord_webhook,
            deal_threshold_pct,
            deal_min_sample,
            ntfy_topic,
            active_start,
            active_end,
            proxy,
            archive_images
        };

        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error();
            fetchConfigFormValues();
            pollStatus();
            return true;
        } catch (err) {
            showToast('Failed to save settings to server.', 'error');
            return false;
        }
    }

    settingsGlobalForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const success = await saveGlobalConfig();
        if (success) showToast('Global settings saved.', 'success');
    });

    // Test Discord Webhook URL
    btnTestWebhook.addEventListener('click', async () => {
        const webhookUrl = discordWebhookInput.value.trim();
        if (!webhookUrl) {
            showToast('Please enter a Discord Webhook URL first.', 'warning');
            return;
        }

        btnTestWebhook.disabled = true;
        btnTestWebhook.innerHTML = '<i class="fa-solid fa-arrows-rotate spin"></i> Testing';

        try {
            const res = await fetch('/api/test-webhook', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ webhook_url: webhookUrl })
            });
            const data = await res.json();
            
            if (data.success) {
                showToast('Test webhook alert sent to Discord!', 'success');
            } else {
                showToast(`Discord webhook failed: ${data.message}`, 'error');
            }
        } catch (err) {
            showToast('Network error testing Discord webhook.', 'error');
        } finally {
            btnTestWebhook.disabled = false;
            btnTestWebhook.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Test Webhook';
        }
    });

    /* ==========================================================================
       LOGS ENGINE (STREAMING RECONSTRUCT)
       ========================================================================== */
    let lastLogCount = 0;

    async function fetchLogs() {
        try {
            const res = await fetch('/api/logs');
            if (!res.ok) throw new Error();
            const logs = await res.json();
            
            state.logs = logs;
            renderLogs(logs);
            renderMiniLogs(logs);
        } catch (err) {
            // Silence log fetches failures to keep UX stable
        }
    }

    function renderLogs(logs) {
        if (state.activeTab !== 'logs') return;
        
        // Check if user is scrolled to bottom
        const isScrolledToBottom = logsContainer.scrollHeight - logsContainer.clientHeight <= logsContainer.scrollTop + 50;
        
        logsContainer.innerHTML = '';
        if (logs.length === 0) {
            logsContainer.innerHTML = '<div class="log-line system">Terminal initialized. Awaiting backend events...</div>';
            return;
        }
        
        logs.forEach(log => {
            const line = document.createElement('div');
            line.className = 'log-line';
            
            // Apply color classes
            if (log.includes(' - INFO - ')) {
                line.classList.add('info');
            } else if (log.includes(' - WARNING - ')) {
                line.classList.add('warning');
            } else if (log.includes(' - ERROR - ')) {
                line.classList.add('error');
            } else {
                line.classList.add('system');
            }
            
            line.textContent = log;
            logsContainer.appendChild(line);
        });
        
        // Auto scroll to bottom
        if (isScrolledToBottom || lastLogCount === 0) {
            logsContainer.scrollTop = logsContainer.scrollHeight;
        }
        
        lastLogCount = logs.length;
    }

    function renderMiniLogs(logs) {
        miniLogs.innerHTML = '';
        if (logs.length === 0) {
            miniLogs.innerHTML = '<div class="log-placeholder">Waiting for scraper activity...</div>';
            return;
        }
        
        // Show last 6 log lines
        const recentLogs = logs.slice(-6);
        recentLogs.forEach(log => {
            const item = document.createElement('div');
            item.className = 'mini-log-item';
            
            if (log.includes(' - INFO - ')) {
                item.classList.add('info');
            } else if (log.includes(' - WARNING - ')) {
                item.classList.add('warning');
            } else if (log.includes(' - ERROR - ')) {
                item.classList.add('error');
            }
            
            // Format log message slightly shorter by stripping datetime
            const logMsg = log.split(' - ').slice(1).join(' - ');
            item.textContent = logMsg;
            miniLogs.appendChild(item);
        });
        
        // Keep mini-logs at bottom
        miniLogs.scrollTop = miniLogs.scrollHeight;
    }

    btnClearLogs.addEventListener('click', () => {
        // Clear locally (the backend keeps the actual log queue but frontend clears view)
        logsContainer.innerHTML = '<div class="log-line system">Terminal buffer cleared by user.</div>';
        showToast('Logs view cleared.', 'info');
    });

    /* ==========================================================================
       BOOTSTRAP INITIALIZERS
       ========================================================================== */
    // Initial Poll runs
    pollStatus();
    fetchLogs();
    
    // Interval registrations
    setInterval(pollStatus, 2500);
    setInterval(fetchLogs, 2500);
});
