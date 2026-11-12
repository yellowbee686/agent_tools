// Detail-page client behavior — role/keyword filter + expand-all toggle.
(function () {
  function applyMessageFilter() {
    const role = document.getElementById('role-filter').value;
    const q = (document.getElementById('msg-q').value || '').trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.message').forEach(m => {
      const r = m.dataset.role;
      const txt = (m.dataset.search || '');
      const okRole = (role === 'all' || role === r);
      const okQ = !q || txt.includes(q);
      const ok = okRole && okQ;
      m.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    const counter = document.getElementById('match-count');
    if (counter) counter.textContent = shown;
  }

  function toggleExpandAll() {
    const expand = document.getElementById('expand-all').checked;
    document.querySelectorAll('details.expandable').forEach(d => { d.open = expand; });
    document.querySelectorAll('details.tool-call').forEach(d => { d.open = expand; });
  }

  window.addEventListener('DOMContentLoaded', () => {
    const r = document.getElementById('role-filter');
    const q = document.getElementById('msg-q');
    const e = document.getElementById('expand-all');
    if (r) r.addEventListener('change', applyMessageFilter);
    if (q) q.addEventListener('input', applyMessageFilter);
    if (e) e.addEventListener('change', toggleExpandAll);
    applyMessageFilter();

    // Smooth-scroll + flash for hash anchors (e.g. #call-xxx).
    const anchor = location.hash;
    if (anchor) {
      const el = document.querySelector(anchor);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.style.outline = '2px solid var(--accent)';
        setTimeout(() => { el.style.outline = ''; }, 2000);
      }
    }
  });
})();
