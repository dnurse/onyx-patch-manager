/* Three-step OPM enrollment: authenticated SSH fingerprint, read-only account
 * discovery, explicit account action, then provisioning. All passwords stay
 * in form inputs until a single authenticated POST; probes never persist them.
 */
(() => {
  'use strict';
  const form = document.getElementById('enrollment-form');
  if (!form) return;
  const steps = [...document.querySelectorAll('.enroll-step')];
  const indicators = [...document.querySelectorAll('[data-indicator]')];
  const fingerprint = document.getElementById('id_fingerprint');
  const fingerprintValue = document.getElementById('fingerprint-value');
  const trustCheck = document.getElementById('trust-check');
  const trustAck = document.getElementById('id_trust_ack');
  const panel = document.getElementById('fingerprint-panel');
  const notice = document.getElementById('fingerprint-progress');
  const error = document.getElementById('fingerprint-error');
  const submit = document.getElementById('finish-enroll');
  const next = document.getElementById('to-step-3');
  const address = document.getElementById('id_address');
  const port = document.getElementById('id_ssh_port');
  const adminUser = document.getElementById('id_admin_user');
  const adminPassword = document.getElementById('id_login_password');
  const accountMode = document.getElementById('id_account_mode');
  const accountNotice = document.getElementById('account-probe-notice');
  const accountCredentials = document.getElementById('account-credentials');
  const choices = document.getElementById('account-choices');
  const noExisting = document.getElementById('account-create-notice');
  const passwordFields = document.getElementById('account-password-fields');
  const passwordInput = document.getElementById('id_opm_password');
  const confirmInput = document.getElementById('id_opm_password_confirm');
  const confirmRow = document.getElementById('opm-confirm-row');
  const replaceConfirmation = document.getElementById('replace-confirmation');
  const replaceAck = document.getElementById('id_replace_ack');
  const passwordLabel = document.getElementById('opm-password-label');
  const passwordHint = document.getElementById('opm-password-hint');
  const actionSummary = document.getElementById('account-operation-summary');
  const retryButton = document.getElementById('retry-fingerprint');
  let targetFingerprint = '';
  let fingerprintInFlight = false;
  let accountInFlight = false;
  let accountChecked = false;
  let probeSequence = 0;

  function setStep(step) {
    steps.forEach(section => { section.hidden = section.dataset.step !== String(step); });
    indicators.forEach(item => {
      item.classList.toggle('active', Number(item.dataset.indicator) === step);
      item.classList.toggle('done', Number(item.dataset.indicator) < step);
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function checkFields(names) {
    for (const name of names) {
      const field = document.getElementById(`id_${name}`);
      if (!field || !field.checkValidity()) {
        if (field) field.reportValidity();
        return false;
      }
    }
    return true;
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function selectedTarget() { return `${address.value.trim()}:${port.value}`; }

  function resetAccount() {
    probeSequence += 1;
    accountChecked = false;
    accountMode.value = '';
    accountCredentials.hidden = true;
    accountNotice.hidden = true;
    choices.hidden = true;
    noExisting.hidden = true;
    passwordFields.hidden = true;
    replaceConfirmation.hidden = true;
    replaceAck.checked = false;
    passwordInput.value = '';
    confirmInput.value = '';
    confirmInput.setCustomValidity('');
    document.querySelectorAll('input[name="account-choice"]').forEach(radio => { radio.checked = false; });
    next.disabled = true;
    submit.disabled = true;
  }

  function clearFingerprint() {
    targetFingerprint = '';
    fingerprint.value = '';
    fingerprintValue.textContent = '';
    panel.hidden = true;
    trustCheck.checked = false;
    trustAck.value = '';
    resetAccount();
  }

  function chooseMode(mode) {
    accountMode.value = mode;
    replaceConfirmation.hidden = mode !== 'replace';
    passwordFields.hidden = !mode;
    const reuse = mode === 'reuse';
    confirmRow.hidden = reuse;
    confirmInput.required = !reuse;
    passwordInput.minLength = reuse ? 1 : 12;
    confirmInput.minLength = 12;
    passwordLabel.textContent = reuse ? 'Existing opm sudo password' : 'New opm sudo password';
    passwordHint.textContent = reuse
      ? 'Enter the current password for the existing opm account. This is verified before any SSH key changes.'
      : 'Choose a new password of at least 12 characters. You will enter it for future patch operations.';
    actionSummary.textContent = mode === 'reuse'
      ? 'Verify the existing opm sudo password, preserve its account and keys, and add this controller SSH key.'
      : mode === 'replace'
        ? 'Delete the existing opm Linux account and home, then create a new account with the supplied password.'
        : 'Create a dedicated opm Linux account with the supplied password.';
    passwordInput.value = '';
    confirmInput.value = '';
    replaceAck.checked = false;
    next.disabled = !mode;
    submit.disabled = true;
  }

  async function discoverFingerprint() {
    if (fingerprintInFlight) return;
    fingerprintInFlight = true;
    retryButton.disabled = true;
    clearFingerprint();
    const target = selectedTarget();
    notice.hidden = false;
    notice.classList.add('loading');
    notice.textContent = 'Checking the server SSH identity...';
    error.hidden = true;
    const payload = new FormData();
    payload.append('address', address.value.trim());
    payload.append('ssh_port', port.value);
    payload.append('csrfmiddlewaretoken', form.querySelector('input[name="csrfmiddlewaretoken"]').value);
    try {
      const response = await fetch(form.dataset.fingerprintUrl, {
        method: 'POST', body: payload, credentials: 'same-origin',
        headers: {Accept: 'application/json'}
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'SSH fingerprint discovery failed');
      if (target !== selectedTarget()) return;
      fingerprint.value = result.fingerprint;
      fingerprintValue.textContent = result.fingerprint;
      targetFingerprint = target;
      notice.hidden = true;
      panel.hidden = false;
    } catch (cause) {
      notice.hidden = true;
      showError(cause.message || 'SSH fingerprint discovery failed');
    } finally {
      fingerprintInFlight = false;
      retryButton.disabled = false;
      notice.classList.remove('loading');
    }
  }

  async function checkAccount() {
    if (accountInFlight || !trustCheck.checked || targetFingerprint !== selectedTarget()) return;
    accountInFlight = true;
    resetAccount();
    const sequence = probeSequence;
    const target = selectedTarget();
    const user = adminUser.value;
    const password = adminPassword.value;
    const approvedFingerprint = fingerprint.value;
    accountNotice.textContent = 'Checking for an existing remote opm account...';
    accountNotice.hidden = false;
    retryButton.disabled = true;
    const payload = new FormData();
    for (const [key, value] of Object.entries({
      address: address.value.trim(), ssh_port: port.value,
      admin_user: user, login_password: password,
      fingerprint: approvedFingerprint, trust_ack: 'yes'
    })) payload.append(key, value);
    payload.append('csrfmiddlewaretoken', form.querySelector('input[name="csrfmiddlewaretoken"]').value);
    try {
      const response = await fetch(form.dataset.accountUrl, {
        method: 'POST', body: payload, credentials: 'same-origin',
        headers: {Accept: 'application/json'}
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Account discovery failed');
      if (sequence !== probeSequence || target !== selectedTarget() ||
          user !== adminUser.value || password !== adminPassword.value ||
          approvedFingerprint !== fingerprint.value || !trustCheck.checked) return;
      accountChecked = true;
      accountNotice.hidden = true;
      accountCredentials.hidden = false;
      if (result.exists) {
        choices.hidden = false;
      } else {
        noExisting.hidden = false;
        chooseMode('create');
      }
    } catch (cause) {
      if (sequence === probeSequence) {
        accountNotice.hidden = true;
        showError(cause.message || 'Unable to inspect existing account');
        trustCheck.checked = false;
        trustAck.value = '';
      }
    } finally {
      accountInFlight = false;
      retryButton.disabled = false;
    }
  }

  document.getElementById('to-step-2').addEventListener('click', () => {
    if (checkFields(['name','address','ssh_port','group_name','admin_user','login_password'])) {
      setStep(2);
      discoverFingerprint();
    }
  });
  trustCheck.addEventListener('change', () => {
    trustAck.value = trustCheck.checked && targetFingerprint === selectedTarget() ? 'on' : '';
    if (trustAck.value) { error.hidden = true; checkAccount(); }
    else resetAccount();
  });
  document.querySelectorAll('input[name="account-choice"]').forEach(radio => {
    radio.addEventListener('change', () => { if (radio.checked) chooseMode(radio.value); });
  });
  next.addEventListener('click', () => {
    if (!accountChecked || !accountMode.value || !trustAck.value ||
        targetFingerprint !== selectedTarget()) return;
    if (!checkFields(['opm_password'])) return;
    if (accountMode.value !== 'reuse') {
      if (!checkFields(['opm_password_confirm'])) return;
      if (passwordInput.value !== confirmInput.value) {
        confirmInput.setCustomValidity('The passwords do not match');
        confirmInput.reportValidity();
        return;
      }
    }
    if (accountMode.value === 'replace' && !replaceAck.checked) {
      replaceAck.reportValidity();
      showError('Approve deletion of the existing account before continuing.');
      return;
    }
    confirmInput.setCustomValidity('');
    submit.disabled = false;
    setStep(3);
  });
  confirmInput.addEventListener('input', () => confirmInput.setCustomValidity(''));
  document.querySelectorAll('[data-back]').forEach(button => {
    button.addEventListener('click', () => setStep(Number(button.dataset.back)));
  });
  retryButton.addEventListener('click', discoverFingerprint);
  [address, port, adminUser, adminPassword].forEach(field => field.addEventListener('input', clearFingerprint));
  form.addEventListener('submit', event => {
    if (!accountChecked || !accountMode.value || !trustAck.value ||
        !trustCheck.checked || targetFingerprint !== selectedTarget() ||
        (accountMode.value === 'replace' && !replaceAck.checked)) {
      event.preventDefault();
      submit.disabled = true;
      showError('Server identity or account choice changed. Verify the account again.');
      setStep(2);
      return;
    }
    submit.disabled = true;
    document.getElementById('setup-running').hidden = false;
  });
  setStep(1);
})();
