let priorities = {};
let installedPlugins = [];

// --- Toast Notifications ---
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = 'info';
    if (type === 'success') icon = 'check_circle';
    if (type === 'error') icon = 'error';
    if (type === 'warning') icon = 'warning';
    
    toast.innerHTML = `<span class="material-icons-round">${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// --- Navigation ---
function switchView(viewId) {
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    
    const viewEl = document.getElementById('view-' + viewId);
    if (viewEl) viewEl.classList.add('active');
    
    const navItem = document.querySelector(`.nav-item[data-view="${viewId}"]`);
    if(navItem) navItem.classList.add('active');
}

// --- Priority Modal ---
function togglePriorityGear() {
    const modeEl = document.getElementById('display-mode');
    const btn = document.getElementById('priority-gear-btn');
    if (modeEl && btn) {
        btn.style.display = modeEl.value === 'priority' ? 'block' : 'none';
    }
}

function openPriorityModal() {
    const modal = document.getElementById('priority-modal');
    if (!modal) return;
    modal.classList.add('active');
    
    const list = document.getElementById('priority-list');
    list.innerHTML = '';
    
    const sorted = [...installedPlugins].sort((a, b) => {
        const prioA = priorities[a.name] || a.priority || 0;
        const prioB = priorities[b.name] || b.priority || 0;
        return prioB - prioA;
    });
    
    sorted.forEach(p => {
        const li = document.createElement('li');
        li.className = 'priority-item';
        li.draggable = true;
        li.dataset.name = p.name;
        li.innerHTML = `
            <span class="material-icons-round drag-handle">drag_indicator</span>
            <img src="/api/plugins/${p.name}/icon.png" onerror="this.src='default-icon.png'" class="plugin-card-icon" style="width:32px; height:32px; margin-right:12px;">
            <span>${p.name}</span>
        `;
        
        li.addEventListener('dragstart', () => li.classList.add('dragging'));
        li.addEventListener('dragend', () => {
            li.classList.remove('dragging');
            document.querySelectorAll('.priority-item').forEach(el => el.classList.remove('drag-over'));
        });
        li.addEventListener('dragover', e => {
            e.preventDefault();
            const dragging = document.querySelector('.dragging');
            if (!dragging || dragging === li) return;
            li.classList.add('drag-over');
            
            const bounding = li.getBoundingClientRect();
            const offset = bounding.y + (bounding.height / 2);
            if (e.clientY - offset > 0) {
                li.after(dragging);
            } else {
                li.before(dragging);
            }
        });
        li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
        list.appendChild(li);
    });
}

function closePriorityModal() {
    const modal = document.getElementById('priority-modal');
    if (modal) modal.classList.remove('active');
}

async function savePriorities() {
    const items = document.querySelectorAll('.priority-item');
    let currentPrio = items.length * 10;
    
    items.forEach(item => {
        priorities[item.dataset.name] = currentPrio;
        currentPrio -= 10;
    });
    
    await saveSettings();
    closePriorityModal();
}

// --- Repository Modal ---
function openRepoModal() {
    const modal = document.getElementById('repo-modal');
    if (modal) modal.classList.add('active');
    renderRepoList();
}

function closeRepoModal() {
    const modal = document.getElementById('repo-modal');
    if (modal) modal.classList.remove('active');
}

// --- API Wrappers ---
async function fetchAPI(url, options = {}) {
    try {
        const res = await fetch(url, options);
        if (!res.ok) {
            let errorMsg = `Server error: ${res.status}`;
            try {
                const errData = await res.json();
                if (errData.detail || errData.message) errorMsg = errData.detail || errData.message;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        return await res.json();
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// --- Data Fetching ---
async function fetchSettings() {
    try {
        const data = await fetchAPI('/api/settings');
        const ipEl = document.getElementById('pixoo-ip');
        const modeEl = document.getElementById('display-mode');
        
        if (ipEl) ipEl.value = data.pixoo_ip || '';
        if (modeEl) modeEl.value = data.mode || 'priority';
        
        priorities = data.priorities || {};
        togglePriorityGear();
    } catch (e) {
        console.error("Failed to fetch settings", e);
    }
}

async function saveSettings() {
    const ipEl = document.getElementById('pixoo-ip');
    const modeEl = document.getElementById('display-mode');
    if (!ipEl || !modeEl) return;
    
    try {
        await fetchAPI('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                pixoo_ip: ipEl.value, 
                mode: modeEl.value, 
                priorities: priorities 
            })
        });
        showToast('Settings saved successfully!', 'success');
    } catch (e) {
        console.error("Failed to save settings", e);
    }
}

async function fetchCatalogAndPlugins() {
    try {
        const pData = await fetchAPI('/api/plugins');
        installedPlugins = pData.plugins || [];
        
        const rData = await fetchAPI('/api/repositories');
        const repos = rData.repositories || [];
        
        renderCatalog(repos);
        renderSidebarTabs(installedPlugins);
        renderPluginViews(installedPlugins);
    } catch (e) {
        console.error("Failed to fetch catalog data", e);
    }
}

// --- Rendering ---
function renderCatalog(repos) {
    const grid = document.getElementById('catalog-grid');
    if (!grid) return;
    grid.innerHTML = '';
    
    if (!repos || repos.length === 0) {
        grid.innerHTML = '<div style="color:var(--text-muted)">No repositories added yet. Click "Repository +" to add one.</div>';
        return;
    }
    
    repos.forEach(repo => {
        const meta = repo.metadata || {};
        const title = meta.name || repo.name;
        const desc = meta.description || "No description available.";
        const version = meta.version ? `v${meta.version}` : '';
        
        let iconUrl = 'default-icon.png';
        if (repo.installed) {
            iconUrl = `/api/plugins/${repo.name}/icon.png?t=${new Date().getTime()}`; // Cache bust local icon
        } else {
            const parts = (repo.url || "").replace("https://github.com/", "").split('/');
            if (parts.length >= 2) {
                iconUrl = `https://raw.githubusercontent.com/${parts[0]}/${parts[1]}/main/icon.png`;
            }
        }
        
        let actionButtons = '';
        let badge = '';
        
        if (repo.installed) {
            badge = `<span class="badge installed">Installed</span>`;
            actionButtons += `<button class="btn btn-danger btn-full" onclick="uninstallPlugin('${repo.name}')"><span class="material-icons-round">delete</span> Uninstall</button>`;
            if (repo.update_available) {
                actionButtons += `<button class="btn btn-primary btn-full" id="update-btn-${repo.name}" onclick="updatePlugin('${repo.name}')"><span class="btn-content"><span class="material-icons-round">system_update</span> Update</span></button>`;
            }
        } else {
            actionButtons = `<button class="btn btn-primary btn-full" id="install-btn-${repo.name}" onclick="installPlugin('${repo.url}', '${repo.name}')"><span class="btn-content"><span class="material-icons-round">download</span> Install</span></button>`;
        }
        
        grid.innerHTML += `
            <div class="card plugin-card">
                <div class="plugin-card-header">
                    <img src="${iconUrl}" onerror="if(this.src.includes('/main/')) this.src=this.src.replace('/main/', '/master/'); else this.src='default-icon.png';" class="plugin-card-icon" alt="icon">
                    <div class="plugin-card-title">
                        <h3>${title} <span class="badge">${version}</span></h3>
                        ${badge}
                    </div>
                </div>
                <div class="plugin-card-desc">${desc}</div>
                <div class="plugin-card-actions">
                    ${actionButtons}
                </div>
            </div>
        `;
    });
}

function renderSidebarTabs(plugins) {
    const navList = document.getElementById('nav-list');
    if (!navList) return;
    
    navList.querySelectorAll('.plugin-nav-item').forEach(el => el.remove());
    
    plugins.forEach(p => {
        const li = document.createElement('li');
        li.className = 'nav-item plugin-nav-item';
        li.dataset.view = 'plugin-' + p.name;
        li.onclick = () => switchView('plugin-' + p.name);
        li.innerHTML = `
            <img src="/api/plugins/${p.name}/icon.png" onerror="this.src='default-icon.png'" class="plugin-icon">
            <span>${p.name}</span>
        `;
        navList.appendChild(li);
    });
}

function renderPluginViews(plugins) {
    const container = document.getElementById('dynamic-views-container');
    if (!container) return;
    container.innerHTML = '';
    
    plugins.forEach(p => {
        const schema = p.settings_schema || [];
        const savedSettings = p.settings || {};
        
        const section = document.createElement('section');
        section.id = `view-plugin-${p.name}`;
        section.className = 'view-section';
        
        let formHTML = '';
        schema.forEach(field => {
            if (field.type === 'header') {
                formHTML += `<div class="form-header-group">${field.label}</div>`;
                return;
            }
            
            const infoHTML = field.info ? `<span class="material-icons-round info-icon" title="${field.info}">info</span>` : '';
            const val = savedSettings[field.name] !== undefined ? savedSettings[field.name] : (field.default !== undefined ? field.default : '');
            
            let inputHTML = '';
            
            if (field.type === 'button' || field.type === 'link_button') {
                inputHTML = `<button class="btn btn-secondary" onclick="window.open('${field.url}', '_blank')" style="flex:1">${field.button_text || 'Open Link'}</button>`;
            } else if (field.type === 'switch' || field.type === 'checkbox') {
                const checked = (val === true || val === 'true') ? 'checked' : '';
                inputHTML = `
                    <label class="switch" style="margin: 0;">
                        <input type="checkbox" id="setting-${p.name}-${field.name}" ${checked}>
                        <span class="slider round"></span>
                    </label>
                `;
            } else if (field.type === 'slider' || field.type === 'range') {
                const min = field.min || 0;
                const max = field.max || 100;
                const step = field.step || 1;
                inputHTML = `
                    <div style="display: flex; align-items: center; flex: 1; gap: 10px;">
                        <input type="range" id="setting-${p.name}-${field.name}" min="${min}" max="${max}" step="${step}" value="${val}" style="flex:1" oninput="this.nextElementSibling.innerText = this.value">
                        <span style="min-width: 30px;">${val}</span>
                    </div>
                `;
            } else if (field.type === 'select') {
                const options = field.options || [];
                let optionsHTML = options.map(opt => {
                    const optVal = typeof opt === 'object' ? opt.value : opt;
                    const optLabel = typeof opt === 'object' ? opt.label : opt;
                    const selected = (val == optVal) ? 'selected' : '';
                    return `<option value="${optVal}" ${selected}>${optLabel}</option>`;
                }).join('');
                inputHTML = `<select id="setting-${p.name}-${field.name}" style="flex:1;">${optionsHTML}</select>`;
            } else {
                inputHTML = `<input type="${field.type}" id="setting-${p.name}-${field.name}" value="${val}" style="flex:1">`;
            }
            
            formHTML += `
                <div class="form-group row-group" style="align-items: center; min-height: 40px;">
                    <label style="margin:0; width: 150px;">${field.label} ${infoHTML}</label>
                    ${inputHTML}
                </div>
            `;
        });
        
        if (schema.length === 0) {
            formHTML = '<p style="color:var(--text-muted)">This plugin has no configurable settings.</p>';
        }
        
        section.innerHTML = `
            <div class="view-header">
                <h1>${p.name} Configuration</h1>
            </div>
            <div class="card settings-card">
                ${formHTML}
                ${schema.length > 0 ? `<button class="btn btn-primary" onclick="savePluginSettings('${p.name}')" style="margin-top: 16px;">Save Settings</button>` : ''}
            </div>
        `;
        container.appendChild(section);
    });
}

async function savePluginSettings(pluginName) {
    const plugin = installedPlugins.find(p => p.name === pluginName);
    if (!plugin) return;
    
    const config = {};
    const schema = plugin.settings_schema || [];
    
    schema.forEach(field => {
        if (field.type === 'header' || field.type === 'button' || field.type === 'link_button') return;
        
        const el = document.getElementById(`setting-${pluginName}-${field.name}`);
        if (el) {
            if (field.type === 'switch' || field.type === 'checkbox') {
                config[field.name] = el.checked;
            } else if (field.type === 'number' || field.type === 'slider' || field.type === 'range') {
                config[field.name] = parseFloat(el.value) || 0;
            } else {
                config[field.name] = el.value;
            }
        }
    });
    
    try {
        await fetchAPI(`/api/plugins/${pluginName}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        showToast(`${pluginName} settings saved!`, 'success');
    } catch (e) {
        console.error("Failed to save plugin settings", e);
    }
}

// --- Plugin Operations & Animations ---
function animateProgressButton(btnId, finalLabel) {
    const btn = document.getElementById(btnId);
    if (!btn) return () => {};
    
    btn.classList.add('btn-progress');
    btn.innerHTML = `
        <div class="water-wave" style="top: 100%;"></div>
        <div class="water-wave wave2" style="top: 100%;"></div>
        <span class="btn-content"><span class="material-icons-round">hourglass_empty</span> <span class="progress-text">0%</span></span>
    `;
    
    const waves = btn.querySelectorAll('.water-wave');
    const text = btn.querySelector('.progress-text');
    let progress = 0;
    
    const updateWave = (p) => {
        const topVal = 100 - (p * 1.5);
        waves.forEach(w => w.style.top = `${topVal}%`);
        if (text) text.innerText = `${p}%`;
    };
    
    const interval = setInterval(() => {
        if (progress < 90) {
            progress += Math.floor(Math.random() * 15) + 5;
            if (progress > 90) progress = 90;
            updateWave(progress);
        }
    }, 500);
    
    return (success = true) => {
        clearInterval(interval);
        if (success) {
            progress = 100;
            updateWave(100);
            setTimeout(() => {
                btn.classList.remove('btn-progress');
                btn.innerHTML = `<span class="btn-content">${finalLabel}</span>`;
                fetchCatalogAndPlugins();
            }, 800);
        } else {
            btn.classList.remove('btn-progress');
            btn.innerHTML = `<span class="btn-content"><span class="material-icons-round">error</span> Failed</span>`;
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-danger');
            fetchCatalogAndPlugins();
        }
    };
}

function startPollingPluginState(pluginName, expectedState, completeAnim) {
    let attempts = 0;
    const maxAttempts = 30; // 45 seconds max wait
    
    const poll = setInterval(async () => {
        attempts++;
        if (attempts >= maxAttempts) {
            clearInterval(poll);
            completeAnim(false);
            showToast(`Operation on ${pluginName} timed out.`, 'error');
            return;
        }
        try {
            const pData = await fetchAPI('/api/plugins');
            const isInstalled = pData.plugins.some(p => p.name === pluginName);
            
            if (isInstalled === expectedState) {
                clearInterval(poll);
                completeAnim(true);
            }
        } catch(e) {
            // Ignore temporary fetch errors during polling
        }
    }, 1500);
}

function startPollingPluginUpdate(pluginName, completeAnim) {
    let attempts = 0;
    const maxAttempts = 30;
    
    const poll = setInterval(async () => {
        attempts++;
        if (attempts >= maxAttempts) {
            clearInterval(poll);
            completeAnim(false);
            showToast(`Update for ${pluginName} timed out.`, 'error');
            return;
        }
        try {
            const rData = await fetchAPI('/api/repositories');
            const repos = rData.repositories || [];
            const targetRepo = repos.find(r => r.name === pluginName);
            
            if (targetRepo && !targetRepo.update_available) {
                // Update cleared!
                clearInterval(poll);
                completeAnim(true);
            }
        } catch(e) {}
    }, 1500);
}

async function installPlugin(url, name) {
    try {
        const complete = animateProgressButton(`install-btn-${name}`, '<span class="material-icons-round">check</span> Done');
        await fetchAPI('/api/plugins/install', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({github_url: url})
        });
        startPollingPluginState(name, true, complete);
    } catch (e) {
        showToast('Installation failed to start', 'error');
    }
}

async function uninstallPlugin(name) {
    if (!confirm(`Are you sure you want to uninstall ${name}?`)) return;
    try {
        await fetchAPI('/api/plugins/uninstall', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({repo_name: name})
        });
        await fetchCatalogAndPlugins();
        showToast(`${name} uninstalled.`, 'success');
    } catch (e) {
        showToast(`Failed to uninstall ${name}.`, 'error');
    }
}

async function updatePlugin(name) {
    try {
        const complete = animateProgressButton(`update-btn-${name}`, '<span class="material-icons-round">check</span> Updated');
        await fetchAPI('/api/plugins/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({repo_name: name})
        });
        startPollingPluginUpdate(name, complete);
    } catch (e) {
        showToast('Update failed to start', 'error');
    }
}

async function forceScan() {
    const scanBtn = document.querySelector('.view-header .btn-secondary');
    if (scanBtn) scanBtn.innerHTML = '<span class="material-icons-round">hourglass_empty</span> Scanning...';
    
    try {
        await fetchAPI('/api/repositories/scan', { method: 'POST' });
        await fetchCatalogAndPlugins();
        showToast('Scan complete!', 'success');
    } catch (e) {
        showToast('Scan failed', 'error');
    } finally {
        if (scanBtn) scanBtn.innerHTML = '<span class="material-icons-round">refresh</span> Scan Updates';
    }
}

// --- Repositories ---
async function addRepository() {
    const inputEl = document.getElementById('new-repo-url');
    if (!inputEl) return;
    
    const url = inputEl.value.trim();
    if (!url) return;
    
    try {
        await fetchAPI('/api/repositories/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({github_url: url})
        });
        inputEl.value = '';
        renderRepoList();
        fetchCatalogAndPlugins();
        showToast('Repository added successfully!', 'success');
    } catch (e) {
        showToast('Failed to add repository', 'error');
    }
}

async function removeRepo(url) {
    try {
        await fetchAPI('/api/repositories/remove', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({github_url: url})
        });
        renderRepoList();
        fetchCatalogAndPlugins();
        showToast('Repository removed', 'info');
    } catch (e) {
        showToast('Failed to remove repository', 'error');
    }
}

async function renderRepoList() {
    try {
        const rData = await fetchAPI('/api/repositories');
        const list = document.getElementById('repo-list');
        if (!list) return;
        
        list.innerHTML = '';
        (rData.repositories || []).forEach(repo => {
            list.innerHTML += `
                <li class="repo-item">
                    <span class="repo-item-title">${repo.url}</span>
                    <button class="btn btn-danger" onclick="removeRepo('${repo.url}')"><span class="material-icons-round">delete</span></button>
                </li>
            `;
        });
    } catch (e) {
        console.error("Failed to render repo list", e);
    }
}

// --- Initialization ---
document.addEventListener("DOMContentLoaded", () => {
    fetchSettings();
    fetchCatalogAndPlugins();
});
