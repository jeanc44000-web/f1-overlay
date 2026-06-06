const teamClass = (team) => {
  const t = (team || '').toLowerCase();
  if (t.includes('audi') || t.includes('sauber')) return 'team-audi';
  if (t.includes('cadillac')) return 'team-cadillac';
  return '';
};

const loadData = async () => {
  const res = await fetch('/api/data');
  const data = await res.json();

  const title = document.getElementById('sessionTitle');
  const rows = document.getElementById('rows');
  const empty = document.getElementById('emptyState');
  const dot = document.getElementById('statusDot');

  if (!data.ok) {
    title.textContent = 'aucune séance en cours';
    rows.innerHTML = '';
    empty.classList.remove('hidden');
    dot.style.background = '#f39c12';
    dot.style.boxShadow = '0 0 18px rgba(243, 156, 18, 0.7)';
    return;
  }

  title.textContent = data.session || 'session';
  empty.classList.add('hidden');
  dot.style.background = '#2ecc71';
  dot.style.boxShadow = '0 0 18px rgba(46, 204, 113, 0.7)';

  rows.innerHTML = '';
  (data.rows || []).forEach(r => {
    const tr = document.createElement('tr');
    tr.className = teamClass(r.team_name);
    tr.innerHTML = `
      <td class="pos">${r.position ?? ''}</td>
      <td>${r.last_name ?? ''}</td>
      <td class="gap">${r.gap ?? ''}</td>
    `;
    rows.appendChild(tr);
  });
};

loadData();
setInterval(loadData, 5000);
