const host = document.querySelector('.mast');
const trigger = document.createElement('button');
trigger.className = 'nav-trigger';
trigger.type = 'button';
trigger.setAttribute('aria-label', 'Open navigation');
trigger.setAttribute('aria-expanded', 'false');
trigger.setAttribute('aria-controls', 'appNavigation');
trigger.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>';
const drawer = document.createElement('dialog');
drawer.id = 'appNavigation';
drawer.className = 'app-drawer';
drawer.setAttribute('aria-label', 'Navigation');
drawer.innerHTML = `<div class="drawer-head">Volcano<button class="nav-trigger" aria-label="Close navigation"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div><nav><a href="/">Home</a><a href="/remote-control">Remote Control</a></nav><p class="drawer-note">Show console & fixture controls</p>`;
for (const link of drawer.querySelectorAll('a')) {
  if (link.getAttribute('href') === location.pathname) link.setAttribute('aria-current', 'page');
}
document.body.append(drawer);
host.prepend(trigger);
trigger.onclick = () => {
  document.dispatchEvent(new Event('navigation-open'));
  drawer.showModal();
  trigger.setAttribute('aria-expanded', 'true');
};
drawer.querySelector('button').onclick = () => drawer.close();
drawer.addEventListener('click', event => { if (event.target === drawer && event.clientX > drawer.getBoundingClientRect().right) drawer.close(); });
drawer.addEventListener('close', () => {trigger.setAttribute('aria-expanded', 'false');trigger.focus();});
