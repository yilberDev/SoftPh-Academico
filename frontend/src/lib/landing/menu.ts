/**
 * The menu of a header on narrow screens (the landing page and the administrator's area). Inside `header`:
 * [data-menu-button] opens and closes [data-menu]; aria-expanded, the hidden label ([data-menu-label]) and
 * the icon ([data-menu-open] / [data-menu-close]) follow. Escape (focus back on the button) or a chosen
 * [data-menu-link] close it, and so does going back to a wide screen, where the links are in the header.
 */
export function setupMenu(header: HTMLElement, wideScreen = '(min-width: 1024px)'): void {
  const button = header.querySelector<HTMLButtonElement>('[data-menu-button]')!;
  const menu = header.querySelector<HTMLElement>('[data-menu]')!;
  const label = header.querySelector<HTMLElement>('[data-menu-label]')!;
  const openIcon = header.querySelector<HTMLElement>('[data-menu-open]')!;
  const closeIcon = header.querySelector<HTMLElement>('[data-menu-close]')!;

  const setOpen = (open: boolean) => {
    button.setAttribute('aria-expanded', String(open));
    menu.classList.toggle('hidden', !open);
    openIcon.classList.toggle('hidden', open);
    closeIcon.classList.toggle('hidden', !open);
    label.textContent = open ? 'Cerrar menú' : 'Abrir menú';
  };

  button.addEventListener('click', () => setOpen(button.getAttribute('aria-expanded') !== 'true'));
  menu.querySelectorAll('[data-menu-link]').forEach((link) => link.addEventListener('click', () => setOpen(false)));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
      setOpen(false);
      button.focus();
    }
  });
  window.matchMedia(wideScreen).addEventListener('change', (event) => {
    if (event.matches) setOpen(false);
  });
}
