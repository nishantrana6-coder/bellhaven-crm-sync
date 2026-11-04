// "View current record" opens a dialog with the record read live from the CRM.
document.addEventListener('click', async (event) => {
  const opener = event.target.closest('[data-record]');
  if (opener) {
    event.preventDefault();
    const dialog = document.getElementById('record-dialog');
    const body = document.getElementById('record-body');
    body.innerHTML = '<p class="hint">Reading the current record…</p>';
    dialog.showModal();
    try {
      const response = await fetch(opener.dataset.record);
      body.innerHTML = await response.text();
    } catch (err) {
      body.textContent = 'Could not read the record: ' + err;
    }
  }
  if (event.target.closest('[data-close]')) document.getElementById('record-dialog').close();
});

// Close the dialog by clicking outside it.
document.getElementById('record-dialog')?.addEventListener('click', (event) => {
  if (event.target.id === 'record-dialog') event.target.close();
});

// Show progress and block double clicks while a decision or refresh is running.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!form.classList.contains('busy-form')) return;
  const clicked = event.submitter;
  setTimeout(() => {
    form.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    if (clicked) clicked.textContent = 'Working…';
  }, 0);
});

// The "type of change" filter applies as soon as it changes.
document.querySelectorAll('[data-autosubmit]').forEach((el) => {
  el.addEventListener('change', () => el.form.submit());
});

// Keep the selected item visible in the list.
document.querySelector('.item.selected')?.scrollIntoView({ block: 'nearest' });
