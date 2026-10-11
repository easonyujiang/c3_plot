/**
 * C3-Mass Dashboard entry point.
 * Reads server-injected initial data and boots the app.
 */
import { init } from './app.js?v=2026072704';

window.__INITIAL_DATA__ = {
  rockets: window.__ROCKETS || [],
  engines: window.__ENGINES || [],
  missions: window.__MISSIONS || [],
  folders: window.__FOLDERS || { rockets: [], folders: [] },
};

try {
  init();
} catch (error) {
  // Keep primary navigation usable even if a non-critical widget fails.
  console.error('C3-Mass initialization failed:', error);
}

// A data-first fallback keeps the library visible even if an optional widget
// fails during boot. The server injects __ROCKETS before any module executes.
function ensureRocketVisible() {
  const rockets = window.__ROCKETS || [];
  const tree = document.getElementById('nav-tree');
  if (!tree || !rockets.length || tree.querySelector('[data-slug]')) return;
  tree.innerHTML = '';
  rockets.forEach((rocket) => {
    const item = document.createElement('div');
    item.className = 'r-item';
    item.dataset.slug = rocket.slug;
    item.style.paddingLeft = '16px';
    const icon = document.createElement('span');
    icon.textContent = '🚀 ';
    const name = document.createElement('span');
    name.textContent = rocket.name || rocket.slug;
    const stat = document.createElement('span');
    stat.className = 'rleo';
    stat.textContent = rocket.m_pl_max != null ? `${Number(rocket.m_pl_max).toFixed(1)}t` : '--';
    item.append(icon, name, stat);
    item.addEventListener('click', () => window.dispatchEvent(new CustomEvent('rocket-select', { detail: rocket.slug })));
    tree.appendChild(item);
  });
  const overview = document.getElementById('overview-grid');
  if (overview && !overview.children.length) {
    rockets.forEach((rocket) => {
      const card = document.createElement('button');
      card.className = 'db-card rocket-fallback-card';
      card.type = 'button';
      card.style.textAlign = 'left';
      card.innerHTML = `<strong></strong><span>LEO 运力 ${rocket.m_pl_max == null ? '--' : `${Number(rocket.m_pl_max).toFixed(3)} t`}</span>`;
      card.querySelector('strong').textContent = rocket.name || rocket.slug;
      card.addEventListener('click', () => window.dispatchEvent(new CustomEvent('rocket-select', { detail: rocket.slug })));
      overview.appendChild(card);
    });
  }
}
ensureRocketVisible();

// Minimal shell fallback: prevents a failed optional widget from making the
// whole application appear dead. The normal app delegate handles these first
// when initialization succeeds.
if (!window.__C3_APP_READY) {
  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-action]');
    if (!action) return;
    const name = action.dataset.action;
    if (name === 'new-rocket') window.App?.openEditor?.();
    else if (name === 'import') window.App?.openImport?.();
    else if (name === 'new-folder') window.App?.newFolder?.();
    else if (name === 'open-sidebar') document.body.classList.add('sidebar-open');
    else if (name === 'close-sidebar') document.body.classList.remove('sidebar-open');
    else if (name === 'toggle-theme') {
      const dark = document.documentElement.dataset.theme !== 'dark';
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      localStorage.setItem('c3-theme', dark ? 'dark' : 'light');
    }
  });
}
