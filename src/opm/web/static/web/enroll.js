/* OPM's self-contained onboarding wizard.
 * Only form navigation and the public SSH fingerprint use JavaScript.
 * SSH passwords stay in HTML password inputs until the final POST.
 * No credentials are persisted in cookies, localStorage, sessions, or
 * background AJAX requests. Django/OPM perform every remote operation.
 */
(() => {
  "use strict";
  const form = document.getElementById("enrollment-form");
  if (!form) return;
  const steps = [...document.querySelectorAll(".enroll-step")];
  const indicators = [...document.querySelectorAll("[data-indicator]")];
  const fingerprint = document.getElementById("id_fingerprint");
  const fingerprintValue = document.getElementById("fingerprint-value");
  const trustCheck = document.getElementById("trust-check");
  const trustAck = document.getElementById("id_trust_ack");
  const panel = document.getElementById("fingerprint-panel");
  const notice = document.getElementById("fingerprint-progress");
  const error = document.getElementById("fingerprint-error");
  const submit = document.getElementById("finish-enroll");
  const address = document.getElementById("id_address");
  const port = document.getElementById("id_ssh_port");
  let inspectedTarget = "";
  let fingerprintInFlight = false;

  function setStep(step) {
    steps.forEach(section => { section.hidden = section.dataset.step !== String(step); });
    indicators.forEach(item => {
      item.classList.toggle("active", Number(item.dataset.indicator) === step);
      item.classList.toggle("done", Number(item.dataset.indicator) < step);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
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

  function clearFingerprint() {
    inspectedTarget = "";
    fingerprint.value = "";
    fingerprintValue.textContent = "";
    panel.hidden = true;
    trustCheck.checked = false;
    trustAck.value = "";
    submit.disabled = true;
  }

  async function discoverFingerprint() {
    if (fingerprintInFlight) return;
    fingerprintInFlight = true;
    const retryButton = document.getElementById("retry-fingerprint");
    retryButton.disabled = true;
    clearFingerprint();
    notice.hidden = false;
    notice.classList.add("loading");
    notice.textContent = "Checking the server's SSH identity...";
    error.hidden = true;
    const target = `${address.value.trim()}:${port.value}`;
    const payload = new FormData();
    payload.append("address", address.value.trim());
    payload.append("ssh_port", port.value);
    payload.append("csrfmiddlewaretoken", form.querySelector('input[name="csrfmiddlewaretoken"]').value);
    try {
      const response = await fetch(form.dataset.fingerprintUrl, {
        method: "POST", body: payload, credentials: "same-origin",
        headers: { "Accept": "application/json" }
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The server did not respond to SSH discovery");
      if (target !== `${address.value.trim()}:${port.value}`) return;
      fingerprint.value = result.fingerprint;
      fingerprintValue.textContent = result.fingerprint;
      inspectedTarget = target;
      notice.hidden = true;
      panel.hidden = false;
    } catch (cause) {
      notice.hidden = true;
      error.textContent = cause.message || "SSH fingerprint discovery failed";
      error.hidden = false;
    } finally {
      fingerprintInFlight = false;
      retryButton.disabled = false;
      notice.classList.remove("loading");
    }
  }

  document.getElementById("to-step-2").addEventListener("click", () => {
    if (checkFields(["name","address","ssh_port","group_name","admin_user","login_password"])) setStep(2);
  });
  document.getElementById("to-step-3").addEventListener("click", () => {
    if (!checkFields(["opm_password","opm_password_confirm"])) return;
    const a = document.getElementById("id_opm_password").value;
    const b = document.getElementById("id_opm_password_confirm").value;
    if (a !== b) {
      document.getElementById("id_opm_password_confirm").setCustomValidity("The passwords do not match");
      document.getElementById("id_opm_password_confirm").reportValidity();
      return;
    }
    document.getElementById("id_opm_password_confirm").setCustomValidity("");
    setStep(3);
    discoverFingerprint();
  });
  document.getElementById("id_opm_password_confirm").addEventListener("input", event => event.target.setCustomValidity(""));
  document.querySelectorAll("[data-back]").forEach(button => button.addEventListener("click", () => setStep(Number(button.dataset.back))));
  document.getElementById("retry-fingerprint").addEventListener("click", discoverFingerprint);
  [address, port].forEach(field => field.addEventListener("input", clearFingerprint));
  trustCheck.addEventListener("change", () => {
    const trusted = trustCheck.checked && inspectedTarget === `${address.value.trim()}:${port.value}`;
    trustAck.value = trusted ? "on" : "";
    submit.disabled = !trusted;
  });
  form.addEventListener("submit", event => {
    if (!fingerprint.value || !trustCheck.checked || inspectedTarget !== `${address.value.trim()}:${port.value}`) {
      event.preventDefault();
      clearFingerprint();
      error.textContent = "Server address or fingerprint changed. Discover the fingerprint again.";
      error.hidden = false;
      setStep(3);
      return;
    }
    submit.disabled = true;
    document.getElementById("setup-running").hidden = false;
  });
  // Server validation failures should not silently submit before identity
  // acceptance. Keeping the first screen visible also avoids echoing secrets.
  setStep(1);
})();
