"use strict";

const listEl = document.getElementById("mail-list");
const countEl = document.getElementById("inbox-count");
const statusEl = document.getElementById("status-pill");
const threadView = document.getElementById("thread-view");
const placeholderEl = document.getElementById("placeholder");
const subjectEl = document.getElementById("mail-subject");
const fromEl = document.getElementById("mail-from");
const bodyEl = document.getElementById("mail-body");
const replyEl = document.getElementById("reply-text");
const sendBtn = document.getElementById("btn-send");
const skipBtn = document.getElementById("btn-skip");
const toastEl = document.getElementById("toast");

// Settings
const settingsBtn = document.getElementById("btn-settings");
const overlayEl = document.getElementById("settings-overlay");
const keyInput = document.getElementById("set-gemini-key");
const modelSelect = document.getElementById("set-gemini-model");
const credsInput = document.getElementById("set-gmail-creds");
const keyCurrentEl = document.getElementById("key-current");
const gmailCurrentEl = document.getElementById("gmail-current");
const saveSettingsBtn = document.getElementById("btn-save-settings");
const testKeyBtn = document.getElementById("btn-test-key");
const reconnectBtn = document.getElementById("btn-reconnect");
const closeSettingsBtn = document.getElementById("btn-close-settings");

let emails = [];
let activeId = null;
let drafting = false;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function showToast(msg, isError = false) {
  toastEl.textContent = msg;
  toastEl.classList.toggle("error", isError);
  toastEl.classList.add("show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.remove("show"), 2600);
}

async function loadInbox() {
  try {
    const health = await api("/api/health");
    if (health.gemini_key_set) {
      statusEl.classList.remove("warn");
      statusEl.innerHTML = '<span class="dot"></span> Connected';
    } else {
      statusEl.classList.add("warn");
      statusEl.innerHTML = '<span class="dot"></span> Gemini key missing — open Settings';
    }

    emails = await api("/api/inbox");
    countEl.textContent = `Unread · ${emails.length}`;

    if (!emails.length) {
      listEl.innerHTML = '<div class="empty">No unread emails. 🎉</div>';
      return;
    }

    listEl.innerHTML = "";
    emails.forEach((email) => {
      const item = document.createElement("div");
      item.className = "mail-item" + (email.id === activeId ? " active" : "");
      item.dataset.id = email.id;

      const top = document.createElement("div");
      top.className = "mail-top";
      const sender = document.createElement("div");
      sender.className = "mail-sender";
      sender.textContent = email.sender;
      top.appendChild(sender);

      const subject = document.createElement("div");
      subject.className = "mail-subject";
      subject.textContent = email.subject;

      item.appendChild(top);
      item.appendChild(subject);

      if (email.draft) {
        const badge = document.createElement("span");
        badge.className = "badge-drafted";
        badge.textContent = "Draft ready";
        item.appendChild(badge);
      }

      item.addEventListener("click", () => selectEmail(email.id));
      listEl.appendChild(item);
    });

    if (!activeId || !emails.some((e) => e.id === activeId)) {
      selectEmail(emails[0].id);
    } else {
      renderActive();
    }
  } catch (err) {
    listEl.innerHTML = `<div class="empty">Couldn't load inbox.<br><small>${escapeHtml(err.message)}</small></div>`;
    statusEl.classList.add("warn");
    statusEl.innerHTML = '<span class="dot"></span> Connection error';
    showToast(err.message, true);
  }
}

function selectEmail(id) {
  activeId = id;
  document.querySelectorAll(".mail-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === id);
  });
  renderActive();
}

function renderActive() {
  const email = emails.find((e) => e.id === activeId);
  if (!email) return;

  placeholderEl.style.display = "none";
  threadView.style.display = "block";
  subjectEl.textContent = email.subject;
  fromEl.innerHTML = `From <b>${escapeHtml(email.sender)}</b> · ${escapeHtml(email.sender_email || "")}`;
  bodyEl.textContent = email.snippet || email.body || "(no text body)";

  if (email.draft) {
    replyEl.value = email.draft;
    setButtons(true);
  } else {
    replyEl.value = "";
    setButtons(false);
    generateDraft(email);
  }
}

async function generateDraft(email) {
  drafting = true;
  replyEl.placeholder = "Generating draft…";
  try {
    const res = await api("/api/reply", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    email.draft = res.draft;
    replyEl.value = res.draft;
    markBadge(email.id);
    setButtons(true);
  } catch (err) {
    replyEl.placeholder = "Draft failed — try again by selecting the email.";
    showToast(err.message, true);
  } finally {
    drafting = false;
  }
}

function markBadge(id) {
  const item = listEl.querySelector(`[data-id="${id}"]`);
  if (item && !item.querySelector(".badge-drafted")) {
    const badge = document.createElement("span");
    badge.className = "badge-drafted";
    badge.textContent = "Draft ready";
    item.appendChild(badge);
  }
}

function setButtons(enabled) {
  sendBtn.disabled = !enabled;
}

async function sendActive() {
  const email = emails.find((e) => e.id === activeId);
  const reply = replyEl.value.trim();
  if (!email || !reply) {
    showToast("Write a reply first.", true);
    return;
  }
  sendBtn.disabled = true;
  try {
    await api("/api/send", {
      method: "POST",
      body: JSON.stringify({ email, reply }),
    });
    showToast("Reply sent ✓");
    removeEmail(activeId);
  } catch (err) {
    showToast(err.message, true);
    setButtons(true);
  }
}

async function skipActive() {
  const email = emails.find((e) => e.id === activeId);
  if (!email) return;
  skipBtn.disabled = true;
  try {
    await api("/api/skip", { method: "POST", body: JSON.stringify({ id: email.id }) });
    showToast("Marked read & skipped");
    removeEmail(activeId);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    skipBtn.disabled = false;
  }
}

function removeEmail(id) {
  emails = emails.filter((e) => e.id !== id);
  activeId = null;
  countEl.textContent = `Unread · ${emails.length}`;
  if (emails.length) {
    loadInbox();
  } else {
    listEl.innerHTML = '<div class="empty">No unread emails. 🎉</div>';
    threadView.style.display = "none";
    placeholderEl.style.display = "block";
  }
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------- Settings ----------

function openSettings() {
  overlayEl.classList.add("open");
  loadSettings();
}

function closeSettings() {
  overlayEl.classList.remove("open");
}

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    keyInput.value = "";
    keyInput.placeholder = s.gemini_key_set
      ? `Saved key (${s.gemini_key_masked}) — type to replace`
      : "Paste your Gemini API key (starts with AIza)";
    keyCurrentEl.innerHTML = s.gemini_key_set
      ? `Current: ${s.gemini_key_masked}`
      : "Not set yet";
    modelSelect.value = s.gemini_model || "gemini-3.6-flash";
    credsInput.value = "";
    credsInput.placeholder = s.gmail_configured
      ? `Gmail connected${s.gmail_email ? " (" + escapeHtml(s.gmail_email) + ")" : ""} — paste new to switch`
      : 'Paste JSON contents of credentials.json';
    gmailCurrentEl.innerHTML = s.gmail_configured
      ? `Connected ${s.gmail_email ? "· " + escapeHtml(s.gmail_email) : ""}`
      : "Not connected yet";
  } catch (err) {
    showToast(err.message, true);
  }
}

async function saveSettings() {
  const payload = {
    gemini_model: modelSelect.value,
    gemini_api_key: keyInput.value.trim(),
    gmail_credentials: credsInput.value.trim(),
  };
  saveSettingsBtn.disabled = true;
  saveSettingsBtn.textContent = "Saving…";
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showToast("Settings saved");
    closeSettings();
    loadInbox();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    saveSettingsBtn.disabled = false;
    saveSettingsBtn.textContent = "Save settings";
  }
}

async function testKey() {
  const key = keyInput.value.trim();
  if (!key) {
    showToast("Paste an API key first.", true);
    return;
  }
  testKeyBtn.disabled = true;
  testKeyBtn.textContent = "Testing…";
  try {
    const res = await api("/api/reply", {
      method: "POST",
      body: JSON.stringify({
        email: { id: "test", sender: "test@example.com", subject: "test", body: "Hello" },
      }),
    });
    showToast("Key works ✓");
  } catch (err) {
    showToast("Key test failed: " + err.message, true);
  } finally {
    testKeyBtn.disabled = false;
    testKeyBtn.textContent = "Test AI key";
  }
}

async function reconnectGmail() {
  reconnectBtn.disabled = true;
  try {
    await api("/api/reconnect", { method: "POST" });
    showToast("Gmail token cleared — reload to re-login");
    closeSettings();
    loadInbox();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    reconnectBtn.disabled = false;
  }
}

sendBtn.addEventListener("click", sendActive);
skipBtn.addEventListener("click", skipActive);
settingsBtn.addEventListener("click", openSettings);
closeSettingsBtn.addEventListener("click", closeSettings);
saveSettingsBtn.addEventListener("click", saveSettings);
testKeyBtn.addEventListener("click", testKey);
reconnectBtn.addEventListener("click", reconnectGmail);
overlayEl.addEventListener("click", (e) => {
  if (e.target === overlayEl) closeSettings();
});

loadInbox();
