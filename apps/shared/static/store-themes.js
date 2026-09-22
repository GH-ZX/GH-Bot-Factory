/* One palette catalog for tenant previews and the storefront. */
window.StoreThemes = (() => {
  const ready = fetch('/shared/store-themes.json').then(r => { if (!r.ok) throw new Error('Theme catalog unavailable'); return r.json(); });
  // A rejected catalog must not prevent legacy stores from opening.
  ready.catch(() => {});
  function apply(root, catalog, key, accent) {
    const theme = catalog[key] || catalog.midnight;
    if (!theme) return;
    root.dataset.storeTheme = catalog[key] ? key : 'midnight';
    root.style.colorScheme = theme.scheme;
    const colors = {bg:theme.bg,surface:theme.surface,'surface-2':theme.surface2,text:theme.text,muted:theme.muted,accent:/^#[0-9a-f]{6}$/i.test(accent || '') ? accent : theme.accent};
    for (const [name,value] of Object.entries(colors)) root.style.setProperty(`--${name}`,value);
    const rgb = colors.accent.slice(1).match(/../g).map(v => parseInt(v,16)/255).map(v => v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    root.style.setProperty('--accent-text', .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2] > .179 ? '#101820' : '#ffffff');
    root.style.setProperty('--border',theme.scheme==='light'?'rgba(25,45,40,.14)':'rgba(215,235,255,.14)');
    root.style.setProperty('--glass',theme.surface);
    root.style.setProperty('--danger',theme.scheme==='light'?'#b4233d':'#ff91a3');
    root.style.setProperty('--success',theme.scheme==='light'?'#116b45':'#74edb4');
    root.style.setProperty('--warning',theme.scheme==='light'?'#815a06':'#ffcf66');
  }
  return {ready,apply};
})();
