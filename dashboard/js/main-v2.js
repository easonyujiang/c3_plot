/**
 * Application entry point.
 * Initial data already exists on window before this module is requested.
 */
import { init } from './app.js?v=2026100901';

try {
  init();
} catch (error) {
  console.error('C3-Mass initialization failed:', error);
  const grid = document.getElementById('overview-grid');
  if (grid) {
    const panel = document.createElement('div');
    panel.className = 'empt startup-error';
    panel.textContent = `页面初始化失败：${error.message}`;
    grid.replaceChildren(panel);
  }
}
