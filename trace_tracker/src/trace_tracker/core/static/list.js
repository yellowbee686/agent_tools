// List-page client behavior: per-root lazy loading and local filtering.
(function () {
  let scrollIntentVersion = 0;

  window.addEventListener('wheel', () => scrollIntentVersion++, { passive: true });
  window.addEventListener('touchmove', () => scrollIntentVersion++, { passive: true });
  window.addEventListener('keydown', event => {
    if (['ArrowDown', 'End', 'PageDown', ' '].includes(event.key)) scrollIntentVersion++;
  });

  function applyFilter() {
    const q = (document.getElementById('q')?.value || '').trim().toLowerCase();
    let shown = 0;
    let total = 0;
    document.querySelectorAll('table.sessions tbody').forEach(tbody => {
      const headers = Array.from(tbody.querySelectorAll('.session-group-row'));
      headers.forEach(header => {
        total++;
        const groupKey = header.dataset.sessionGroupKey;
        const children = Array.from(tbody.querySelectorAll('[data-parent-group-key]'))
          .filter(row => row.dataset.parentGroupKey === groupKey);
        const groupMatches = !q || (header.dataset.search || '').includes(q);
        const matchingChildren = children.filter(
          row => !q || (row.dataset.search || '').includes(q)
        );
        const visible = groupMatches || matchingChildren.length > 0;
        header.style.display = visible ? '' : 'none';
        if (visible) shown++;

        const expanded = header.querySelector('.session-group-toggle')?.getAttribute('aria-expanded') === 'true';
        children.forEach(row => {
          if (q) {
            row.hidden = false;
            row.style.display = groupMatches || matchingChildren.includes(row) ? '' : 'none';
          } else {
            row.style.display = '';
            row.hidden = !expanded;
          }
        });
      });

      tbody.querySelectorAll('.session-row[data-session-group-key]').forEach(row => {
        total++;
        const visible = !q || (row.dataset.search || '').includes(q);
        row.style.display = visible ? '' : 'none';
        if (visible) shown++;
      });
    });
    const counter = document.getElementById('match-count');
    if (counter) counter.textContent = shown + ' / ' + total;
  }

  async function loadNextPage(section) {
    if (section.dataset.loading === 'true' || section.dataset.hasMore === 'false') return;

    const tbody = section.querySelector('tbody');
    const status = section.querySelector('.session-loader');
    const button = section.querySelector('.load-more-button');
    const placeholder = section.querySelector('.lazy-placeholder');
    const pageSize = Number(section.dataset.pageSize || 50);
    const offset = Number(section.dataset.nextOffset || 0);
    const params = new URLSearchParams({
      root: section.dataset.rootKey || '',
      source: section.dataset.source || 'all',
      limit: String(pageSize),
      offset: String(offset),
    });

    section.dataset.loading = 'true';
    section.setAttribute('aria-busy', 'true');
    if (status) status.textContent = `Loading ${pageSize} sessions…`;
    if (button) {
      button.disabled = true;
      button.textContent = 'Loading…';
    }

    try {
      const response = await fetch(`/${encodeURIComponent(section.dataset.agent || '')}/sessions.json?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (placeholder) placeholder.remove();

      const knownGroups = new Set(
        Array.from(tbody?.querySelectorAll('[data-session-group-key]') || [])
          .map(row => row.dataset.sessionGroupKey)
      );
      const knownThreads = new Set(
        Array.from(tbody?.querySelectorAll('[data-thread-key]') || [])
          .map(row => row.dataset.threadKey)
      );
      const scratch = document.createElement('tbody');
      scratch.innerHTML = payload.rows_html || '';
      let skipGroup = false;
      Array.from(scratch.children).forEach(row => {
        const groupKey = row.dataset.sessionGroupKey;
        if (groupKey) {
          if (knownGroups.has(groupKey)) {
            skipGroup = row.classList.contains('session-group-row');
            return;
          }
          knownGroups.add(groupKey);
          skipGroup = false;
        }
        if (skipGroup) return;
        const threadKey = row.dataset.threadKey;
        if (threadKey && knownThreads.has(threadKey)) return;
        if (threadKey) knownThreads.add(threadKey);
        tbody?.appendChild(row);
      });

      const loadedSessions = tbody?.querySelectorAll('[data-session-group-key]').length || 0;
      const loadedThreads = tbody?.querySelectorAll('[data-thread-key]').length || 0;
      const loadedCount = section.querySelector('[data-loaded-count]');
      if (loadedCount) loadedCount.textContent = `${loadedSessions} sessions · ${loadedThreads} threads`;
      section.dataset.nextOffset = String(payload.next_offset ?? offset);
      section.dataset.hasMore = payload.has_more ? 'true' : 'false';

      if (payload.has_more) {
        if (status) status.textContent = `${loadedSessions} sessions loaded. Scroll or use the button for more.`;
        if (button) {
          button.hidden = false;
          button.disabled = false;
          button.textContent = `Load ${pageSize} more`;
        }
      } else {
        if (status) status.textContent = loadedSessions ? `${loadedSessions} sessions loaded. End of this root.` : 'No sessions in this root.';
        if (button) button.hidden = true;
      }
      applyFilter();
    } catch (error) {
      if (placeholder) placeholder.remove();
      if (status) status.textContent = `Could not load sessions: ${error.message}`;
      if (button) {
        button.hidden = false;
        button.disabled = false;
        button.textContent = 'Retry';
      }
    } finally {
      section.dataset.loading = 'false';
      section.removeAttribute('aria-busy');
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    const q = document.getElementById('q');
    if (q) {
      q.addEventListener('input', applyFilter);
      applyFilter();
    }

    const sections = document.querySelectorAll('.lazy-session-section');
    const observer = 'IntersectionObserver' in window
      ? new IntersectionObserver(entries => {
          entries.forEach(entry => {
            const section = entry.target.closest('.lazy-session-section');
            if (!section || !entry.isIntersecting) return;
            const lastIntent = Number(section.dataset.lastAutoIntent || -1);
            const hasAutoLoaded = section.dataset.hasAutoLoaded === 'true';
            if (hasAutoLoaded && lastIntent === scrollIntentVersion) return;
            section.dataset.hasAutoLoaded = 'true';
            section.dataset.lastAutoIntent = String(scrollIntentVersion);
            loadNextPage(section);
          });
        }, { rootMargin: '320px 0px' })
      : null;

    sections.forEach(section => {
      const button = section.querySelector('.load-more-button');
      button?.addEventListener('click', () => {
        section.dataset.hasAutoLoaded = 'true';
        section.dataset.lastAutoIntent = String(scrollIntentVersion);
        loadNextPage(section);
      });
      const sentinel = section.querySelector('.load-sentinel');
      if (observer && sentinel) observer.observe(sentinel);
    });

    document.addEventListener('click', event => {
      const button = event.target.closest('.session-group-toggle');
      if (!button) return;
      const expanded = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', String(!expanded));
      const disclosure = button.querySelector('.session-group-disclosure');
      if (disclosure) disclosure.textContent = expanded ? '▸' : '▾';
      applyFilter();
    });
  });
})();
