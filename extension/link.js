// Liaison automatique : sur une page CandiPilot où l'utilisateur est connecté, l'extension reçoit
// son jeton. L'utilisateur n'a rien à saisir.
(() => {
  if (!document.querySelector('meta[name="candipilot"]')) return;     // pas une page CandiPilot
  document.documentElement.dataset.candipilotExtension = chrome.runtime.getManifest().version;
  let done = false;
  async function tryLink() {
    if (done) return;
    const me = await fetch("/api/auth/me", { credentials: "include" }).catch(() => null);
    if (!me || !me.ok) return;                                       // pas encore connecté à CandiPilot
    const user = await me.json();
    const st = await chrome.runtime.sendMessage({ type: "cp-page", origin: location.origin, userId: user.id });
    if (st && st.linked) { done = true; return; }
    const r = await fetch("/api/agent/link", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: chrome.runtime.getManifest().version }) });
    if (!r.ok) return;
    const { token, user_id } = await r.json();
    await chrome.runtime.sendMessage({ type: "cp-token", origin: location.origin, token, userId: user_id });
    done = true;
  }
  tryLink();
  window.addEventListener("hashchange", () => { done = false; tryLink(); });
  setInterval(tryLink, 5000);
})();
