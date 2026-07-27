// Global utilities and common functions

// Set active navigation link
function setActiveNav() {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.nav-menu a');

    navLinks.forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });

    // Also set active bottom nav item
    const bottomNavItems = document.querySelectorAll('.bottom-nav-item');
    bottomNavItems.forEach(item => {
        item.classList.remove('active');
        if (item.getAttribute('href') === currentPath) {
            item.classList.add('active');
        }
    });
}

// Mobile menu toggle
function toggleMobileMenu() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (sidebar) {
        sidebar.classList.toggle('open');
        if (overlay) overlay.classList.toggle('open');
    }
}

function closeMobileMenu() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('open');
}

// Format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

function formatDate(dateString) {
    // Guard against null/undefined/empty warranty dates
    if (!dateString) {
        return 'N/A';
    }
    // Parse date string directly without Date object to avoid any timezone issues
    const [year, month, day] = dateString.split('-');
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const monthIndex = parseInt(month, 10) - 1;
    const dayNum = parseInt(day, 10);
    return `${monthNames[monthIndex]} ${dayNum}, ${year}`;
}

// Show alert message
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;

    const container = document.querySelector('.container');
    if (container) {
        container.insertBefore(alertDiv, container.firstChild);

        // Auto-remove after 5 seconds
        setTimeout(() => {
            alertDiv.remove();
        }, 5000);
    }
}

// Table sorting functionality
function makeSortable(table) {
    const headers = table.querySelectorAll('th.sortable');

    headers.forEach((header, index) => {
        header.addEventListener('click', () => {
            sortTable(table, index, header);
        });
    });
}

function sortTable(table, columnIndex, header) {
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const isAscending = !header.classList.contains('sort-asc');

    // Remove sort classes from all headers
    table.querySelectorAll('th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
    });

    // Add appropriate class to clicked header
    header.classList.add(isAscending ? 'sort-asc' : 'sort-desc');

    // Sort rows
    rows.sort((a, b) => {
        const aValue = a.cells[columnIndex].textContent.trim();
        const bValue = b.cells[columnIndex].textContent.trim();

        // Try to parse as date first (before numbers)
        const aDate = new Date(aValue);
        const bDate = new Date(bValue);

        if (!isNaN(aDate.getTime()) && !isNaN(bDate.getTime())) {
            return isAscending ? aDate - bDate : bDate - aDate;
        }

        // Try to parse as number
        const aNum = parseFloat(aValue.replace(/[^0-9.-]/g, ''));
        const bNum = parseFloat(bValue.replace(/[^0-9.-]/g, ''));

        if (!isNaN(aNum) && !isNaN(bNum)) {
            return isAscending ? aNum - bNum : bNum - aNum;
        }

        // Sort as string
        return isAscending
            ? aValue.localeCompare(bValue)
            : bValue.localeCompare(aValue);
    });

    // Re-append sorted rows
    rows.forEach(row => tbody.appendChild(row));
}

// Modal handling
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'block';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

// Close modal when clicking outside
window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        e.target.style.display = 'none';
    }
});

// File upload preview
function handleFilePreview(input, previewElementId) {
    const file = input.files[0];
    const preview = document.getElementById(previewElementId);

    if (file && preview) {
        preview.textContent = `Selected: ${file.name}`;
    }
}

// Validate form
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return false;

    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');
    let isValid = true;

    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.style.borderColor = 'var(--danger-color)';
            isValid = false;
        } else {
            input.style.borderColor = 'var(--border-color)';
        }
    });

    return isValid;
}

// Vehicle Management with Caching
function getCurrentVehicleId() {
    return localStorage.getItem('currentVehicleId');
}

function setCurrentVehicleId(vehicleId) {
    localStorage.setItem('currentVehicleId', vehicleId);
}

// Cache management
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes in milliseconds

function getCachedData(key) {
    try {
        const cached = localStorage.getItem(`cache_${key}`);
        if (!cached) return null;

        const { data, timestamp } = JSON.parse(cached);
        const age = Date.now() - timestamp;

        if (age > CACHE_DURATION) {
            localStorage.removeItem(`cache_${key}`);
            return null;
        }

        return data;
    } catch (error) {
        console.error('Error reading cache:', error);
        return null;
    }
}

function setCachedData(key, data) {
    try {
        const cacheEntry = {
            data: data,
            timestamp: Date.now()
        };
        localStorage.setItem(`cache_${key}`, JSON.stringify(cacheEntry));
    } catch (error) {
        console.error('Error writing cache:', error);
    }
}

function clearCache(key) {
    if (key) {
        localStorage.removeItem(`cache_${key}`);
    } else {
        // Clear all cache entries
        Object.keys(localStorage).forEach(k => {
            if (k.startsWith('cache_')) {
                localStorage.removeItem(k);
            }
        });
    }
}

async function loadVehicleSelector(forceRefresh = false) {
    try {
        let vehicles;

        // Try to get from cache first
        if (!forceRefresh) {
            vehicles = getCachedData('vehicles');
        }

        // Fetch from API if not cached or force refresh
        if (!vehicles) {
            vehicles = await fetchAPI('/api/vehicles');
            setCachedData('vehicles', vehicles);
        }

        const selector = document.getElementById('vehicleSelector');

        if (!selector) return;

        if (vehicles && vehicles.length > 0) {
            selector.style.display = 'block';
            selector.innerHTML = '<option value="">Select a vehicle...</option>';

            vehicles.forEach(vehicle => {
                const option = document.createElement('option');
                option.value = vehicle.id;
                const nickname = vehicle.nickname || `${vehicle.year} ${vehicle.manufacturer} ${vehicle.model}`;
                option.textContent = nickname;
                selector.appendChild(option);
            });

            // Set current vehicle
            const currentId = getCurrentVehicleId();
            if (currentId) {
                selector.value = currentId;
            } else if (vehicles.length === 1) {
                // Auto-select if only one vehicle
                selector.value = vehicles[0].id;
                setCurrentVehicleId(vehicles[0].id);
            }
        } else {
            selector.style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading vehicle selector:', error);
    }
}

function handleVehicleChange() {
    const selector = document.getElementById('vehicleSelector');
    if (selector && selector.value) {
        setCurrentVehicleId(selector.value);
        // Reload the current page to show data for selected vehicle
        window.location.reload();
    }
}

// Resolves after the vehicle selector has loaded and auto-selected a
// current vehicle if needed. Page scripts that read getCurrentVehicleId()
// on load must wait on this, otherwise a fresh session (first login) races
// the selector and finds no vehicle selected yet.
let appReadyResolve;
const appReady = new Promise(resolve => { appReadyResolve = resolve; });

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    try {
        setActiveNav();
        await loadVehicleSelector();
    } finally {
        appReadyResolve();
    }
});

// API helper functions
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(url, options);
        const data = await response.json();

        if (!response.ok) {
            // If the response has an error message, use it
            const errorMsg = data.error || `HTTP error! status: ${response.status}`;
            throw new Error(errorMsg);
        }

        return data;
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

async function postFormData(url, formData) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showAlert('An error occurred. Please try again.', 'danger');
        throw error;
    }
}

async function postJSON(url, data) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showAlert('An error occurred. Please try again.', 'danger');
        throw error;
    }
}

async function deleteAPI(url) {
    try {
        const response = await fetch(url, {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showAlert('An error occurred. Please try again.', 'danger');
        throw error;
    }
}

async function putJSON(url, data) {
    try {
        const response = await fetch(url, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showAlert('An error occurred. Please try again.', 'danger');
        throw error;
    }
}

async function putFormData(url, formData) {
    try {
        const response = await fetch(url, {
            method: 'PUT',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showAlert('An error occurred. Please try again.', 'danger');
        throw error;
    }
}

// Pagination utilities
function createPagination(totalItems, currentPage, itemsPerPage, onPageChange) {
    const totalPages = Math.ceil(totalItems / itemsPerPage);

    if (totalPages <= 1) return '';

    let html = '<div class="pagination" style="display: flex; justify-content: center; align-items: center; gap: 0.5rem; margin-top: 1rem;">';

    // Previous button
    if (currentPage > 1) {
        html += `<button class="btn btn-small btn-secondary" onclick="${onPageChange}(${currentPage - 1})">Previous</button>`;
    }

    // Page numbers
    const maxVisiblePages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);

    if (endPage - startPage < maxVisiblePages - 1) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    if (startPage > 1) {
        html += `<button class="btn btn-small btn-secondary" onclick="${onPageChange}(1)">1</button>`;
        if (startPage > 2) {
            html += '<span style="padding: 0 0.5rem;">...</span>';
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        if (i === currentPage) {
            html += `<button class="btn btn-small btn-primary" disabled>${i}</button>`;
        } else {
            html += `<button class="btn btn-small btn-secondary" onclick="${onPageChange}(${i})">${i}</button>`;
        }
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            html += '<span style="padding: 0 0.5rem;">...</span>';
        }
        html += `<button class="btn btn-small btn-secondary" onclick="${onPageChange}(${totalPages})">${totalPages}</button>`;
    }

    // Next button
    if (currentPage < totalPages) {
        html += `<button class="btn btn-small btn-secondary" onclick="${onPageChange}(${currentPage + 1})">Next</button>`;
    }

    html += '</div>';

    // Add page info
    const startItem = (currentPage - 1) * itemsPerPage + 1;
    const endItem = Math.min(currentPage * itemsPerPage, totalItems);
    html += `<div style="text-align: center; margin-top: 0.5rem; color: var(--light-text); font-size: 0.9rem;">
        Showing ${startItem}-${endItem} of ${totalItems}
    </div>`;

    return html;
}

function paginateArray(array, page, itemsPerPage) {
    const startIndex = (page - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    return array.slice(startIndex, endIndex);
}

// Debounce utility for search/filter inputs
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Create mobile card layout from data
function createMobileCard(title, fields, actions) {
    let html = '<div class="mobile-card">';

    // Header
    html += '<div class="mobile-card-header">';
    html += `<h4>${title}</h4>`;
    html += '</div>';

    // Body
    html += '<div class="mobile-card-body">';
    fields.forEach(field => {
        html += '<div class="mobile-card-row">';
        html += `<span class="mobile-card-label">${field.label}</span>`;
        html += `<span class="mobile-card-value">${field.value}</span>`;
        html += '</div>';
    });
    html += '</div>';

    // Actions
    if (actions && actions.length > 0) {
        html += '<div class="mobile-card-actions">';
        actions.forEach(action => {
            html += `<button class="btn ${action.class || 'btn-secondary'}" onclick="${action.onclick}">${action.label}</button>`;
        });
        html += '</div>';
    }

    html += '</div>';
    return html;
}

// ============================================================
// GLOBAL SEARCH
// ============================================================

let _searchDebounce = null;

function openGlobalSearch() {
    const overlay = document.getElementById('searchOverlay');
    const input = document.getElementById('globalSearchInput');
    if (!overlay) return;
    overlay.classList.add('open');
    setTimeout(() => input && input.focus(), 50);
}

function closeGlobalSearch() {
    const overlay = document.getElementById('searchOverlay');
    const input = document.getElementById('globalSearchInput');
    const results = document.getElementById('searchResults');
    if (!overlay) return;
    overlay.classList.remove('open');
    if (input) input.value = '';
    if (results) results.innerHTML = '<div class="search-hint">Type at least 2 characters to search across all sections.</div>';
}

function handleSearchOverlayClick(e) {
    if (e.target === document.getElementById('searchOverlay')) closeGlobalSearch();
}

function runGlobalSearch(query) {
    const resultsEl = document.getElementById('searchResults');
    if (!resultsEl) return;

    if (!query || query.length < 2) {
        resultsEl.innerHTML = '<div class="search-hint">Type at least 2 characters to search across all sections.</div>';
        return;
    }

    resultsEl.innerHTML = '<div class="search-loading">Searching...</div>';

    const vehicleId = getCurrentVehicleId();
    const params = new URLSearchParams({ q: query });
    if (vehicleId) params.append('vehicle_id', vehicleId);

    fetch(`/api/search?${params}`)
        .then(r => r.json())
        .then(data => renderSearchResults(data.results, query))
        .catch(() => {
            resultsEl.innerHTML = '<div class="search-empty">Search failed. Please try again.</div>';
        });
}

const SEARCH_SECTIONS = {
    services:  { label: 'Service Records', url: '/services',  icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>' },
    fuel:      { label: 'Fuel Records',    url: '/fuel',      icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 22V9l7-7 7 7v13"/><path d="M9 22V12h4v10"/></svg>' },
    supplies:  { label: 'Parts & Supplies', url: '/supplies', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>' },
    reminders: { label: 'Reminders',       url: '/reminders', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>' },
    documents: { label: 'Documents',       url: '/documents', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' },
    tires:     { label: 'Tires',           url: '/tires',     icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>' },
};

function renderSearchResults(results, query) {
    const resultsEl = document.getElementById('searchResults');
    if (!resultsEl) return;

    const keys = Object.keys(results);
    if (keys.length === 0) {
        resultsEl.innerHTML = `<div class="search-empty">No results found for "<strong>${escapeHtml(query)}</strong>".</div>`;
        return;
    }

    let html = '';
    keys.forEach(section => {
        const items = results[section];
        if (!items || items.length === 0) return;
        const meta = SEARCH_SECTIONS[section];
        if (!meta) return;

        html += `<div class="search-section">`;
        html += `<div class="search-section-title">${meta.label}</div>`;

        items.forEach(item => {
            const sub = item.sub ? `<div class="search-result-sub">${escapeHtml(item.sub)}</div>` : '';
            let badge = '';
            if (section === 'reminders') {
                badge = item.completed
                    ? '<span class="search-result-badge badge-completed">Done</span>'
                    : '<span class="search-result-badge badge-pending">Pending</span>';
            } else if (section === 'services' && item.cost != null) {
                badge = `<span class="search-result-sub" style="flex-shrink:0">$${parseFloat(item.cost).toFixed(2)}</span>`;
            }

            html += `<a class="search-result-item" href="${meta.url}" onclick="closeGlobalSearch()">
                <div class="search-result-icon">${meta.icon}</div>
                <div class="search-result-body">
                    <div class="search-result-label">${escapeHtml(item.label)}</div>
                    ${sub}
                </div>
                ${badge}
            </a>`;
        });

        html += `</div>`;
    });

    resultsEl.innerHTML = html;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Wire up search input once DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('globalSearchInput');
    if (!input) return;
    input.addEventListener('input', () => {
        clearTimeout(_searchDebounce);
        _searchDebounce = setTimeout(() => runGlobalSearch(input.value.trim()), 280);
    });
    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeGlobalSearch();
    });
});

// Keyboard shortcut: Ctrl+K / Cmd+K to open search
document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const overlay = document.getElementById('searchOverlay');
        if (overlay && overlay.classList.contains('open')) {
            closeGlobalSearch();
        } else {
            openGlobalSearch();
        }
    }
});
