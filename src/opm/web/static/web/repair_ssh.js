/* Explicit administrator-only recovery; fingerprint approval precedes secrets. */
(() => {
  'use strict';
  const form = document.querySelector('.ssh-repair-form');
  if (!form) return;
  const button = document.getElementById('repair-check-fingerprint');
  const panel = document.getElementById('repair-fingerprint-panel');
  const text = document.getElementById('repair-fingerprint-value');
  const hidden = document.getElementById('id_fingerprint');
  const approve = document.getElementById('id_trust_ack');
  const submit = document.getElementById('repair-submit');
  const error = document.getElementById('repair-fingerprint-error');
  const create = document.getElementById('id_create_if_missing');
  const createFields = document.getElementById('repair-create-fields');
  const newPass = document.getElementById('id_new_opm_password');
  const newPassConfirm = document.getElementById('id_new_opm_password_confirm');
  const setCreation = () => {
    createFields.hidden = !create.checked;
    newPass.required = create.checked;
    newPassConfirm.required = create.checked;
    update();
  };
  approve.checked = false;
  hidden.value = '';
  const update = () => { submit.disabled = !hidden.value || !approve.checked ||
    (create.checked && (newPass.value.length < 12 || newPass.value !== newPassConfirm.value)); };
  create.addEventListener('change', setCreation);
  newPass.addEventListener('input', update);
  newPassConfirm.addEventListener('input', update);
  setCreation();
  approve.addEventListener('change', update);
  button.addEventListener('click', async () => {
    button.disabled = true;
    approve.checked = false; hidden.value = ''; panel.hidden = true; error.hidden = true; update();
    try {
      const body = new FormData();
      body.append('address', form.querySelector('[name="address"]').value);
      body.append('ssh_port', form.querySelector('[name="ssh_port"]').value);
      body.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);
      const response = await fetch(form.dataset.fingerprintUrl, {
        method:'POST', body, credentials:'same-origin', headers: {'Accept':'application/json'}
      });
      const data = await response.json();
      if (!response.ok || !/^SHA256:[a-zA-Z0-9+/]+$/.test(data.fingerprint || '')) {
        throw new Error(data.error || 'Could not check SSH host key');
      }
      hidden.value = data.fingerprint;
      text.textContent = data.fingerprint;
      panel.hidden = false;
    } catch (e) {
      error.textContent = e.message || 'Unable to retrieve SSH fingerprint';
      error.hidden = false;
    } finally { button.disabled = false; update(); }
  });
})();
