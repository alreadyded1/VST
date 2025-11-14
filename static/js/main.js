// Global utilities and common functions

// View mode toggle
function toggleView() {
    const body = document.body;
    const button = document.querySelector('.view-toggle');

    if (body.classList.contains('mobile-view')) {
        body.classList.remove('mobile-view');
        body.classList.add('desktop-view');
        button.textContent = 'Switch to Mobile';
        localStorage.setItem('viewMode', 'desktop');
    } else {
        body.classList.remove('desktop-view');
        body.classList.add('mobile-view');
        button.textContent = 'Switch to Desktop';
        localStorage.setItem('viewMode', 'mobile');
    }
}

// Initialize view mode from localStorage
function initViewMode() {
    const savedMode = localStorage.getItem('viewMode') || 'desktop';
    const body = document.body;
    const button = document.querySelector('.view-toggle');

    if (savedMode === 'mobile') {
        body.classList.add('mobile-view');
        body.classList.remove('desktop-view');
        if (button) button.textContent = 'Switch to Desktop';
    } else {
        body.classList.add('desktop-view');
        body.classList.remove('mobile-view');
        if (button) button.textContent = 'Switch to Mobile';
    }
}

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
}

// Format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

// Format date for display
function formatDate(dateString) {
    // Parse date as local date to avoid timezone shifts
    const [year, month, day] = dateString.split('-').map(num => parseInt(num, 10));
    const date = new Date(year, month - 1, day); // month is 0-indexed
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
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

// Vehicle Management
function getCurrentVehicleId() {
    return localStorage.getItem('currentVehicleId');
}

function setCurrentVehicleId(vehicleId) {
    localStorage.setItem('currentVehicleId', vehicleId);
}

async function loadVehicleSelector() {
    try {
        const vehicles = await fetchAPI('/api/vehicles');
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    initViewMode();
    setActiveNav();
    loadVehicleSelector();
});

// API helper functions
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(url, options);

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
