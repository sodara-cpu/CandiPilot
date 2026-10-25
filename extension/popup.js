const NAMES = { jobteaser: "JobTeaser", linkedin: "LinkedIn", wttj: "Welcome to the Jungle" };
const LOGIN = { jobteaser: "https://www.jobteaser.com/fr/users/sign_in", linkedin: "https://www.linkedin.com/login",
  wttj: "https://www.welcometothejungle.com/fr/signin" };
const PLATFORMS = { greenhouse: "Greenhouse", lever: "Lever" };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const send = (msg) => chrome.runtime.sendMessage(msg);

function drawSites(sites) {
  if (!sites) return;
  $("sites").innerHTML = Object.entries(NAMES).map(([k, n]) => {
    const s = sites[k] || {};
    const url = k === "jobteaser" && s.portal ? s.portal + "/fr/job-offers" : LOGIN[k];
    return `<li><span class="${s.connected ? "ok" : "ko"}">${s.connected ? "✓" : "✗"}</span>${esc(n)}
      ${s.connected ? "" : `<a href="${esc(url)}" target="_blank">Se connecter</a>`}</li>`;
  }).join("");
}

async function draw() {
  const st = await send({ type: "status" });
  $("ver").textContent = "v" + st.version;
  $("link").innerHTML = st.linked
    ? `<span class="ok">✓ Reliée à ton compte</span><div class="muted">${esc(st.server)}</div>`
    : `<p class="muted">Ouvre CandiPilot et connecte-toi : l'extension se relie toute seule à ton compte.</p>`;
  drawSites(st.sites);
  $("foot").textContent = st.lastError ? "Dernière erreur : " + st.lastError : st.lastPoll ? "Dernier contact : " + new Date(st.lastPoll).toLocaleTimeString("fr-FR") : "";
  $("check").disabled = $("fill").disabled = !st.linked;
}

$("check").onclick = async () => { $("check").disabled = true; $("check").textContent = "Vérification…"; drawSites((await send({ type: "check" })).sites); $("check").textContent = "Vérifier mes connexions"; $("check").disabled = false; };
$("fill").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("fill").disabled = true;
  const r = await send({ type: "fill", tabId: tab.id });
  $("fill").disabled = false;
  $("fillResult").innerHTML = r.error ? `<span class="ko">${esc(r.error)}</span>`
    : `<span class="ok">✓ ${r.filled.length + r.check.length} champ(s) rempli(s)${r.platform ? ` · formulaire ${esc(PLATFORMS[r.platform] || r.platform)} reconnu` : ""}</span>
       ${r.check.length ? `<div class="muted">À vérifier (pointillés orange) : ${esc(r.check.join(", "))}</div>` : ""}
       ${r.todo.length ? `<div class="muted">À compléter par toi (en orange) : ${esc(r.todo.join(", "))}</div>` : ""}
       ${r.steps ? `<div class="muted">Formulaire en plusieurs étapes : clique toi-même sur « Suivant », les champs suivants se remplissent tout seuls.</div>` : ""}
       <div class="muted">Rien n'est envoyé : relis, puis envoie toi-même.</div>`;
};
draw();
