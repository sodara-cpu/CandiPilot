/* CandiPilot — interface (écrans de la maquette + CRUD), sans framework. */
"use strict";

const $app = document.getElementById("app");
let ME = null;              // utilisateur connecté
let pollTimer = null;

// ---------- utilitaires ----------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, init);
  if (res.status === 401 && !path.startsWith("/api/auth/")) { ME = null; go("#/login"); throw new Error("Session expirée, reconnecte-toi."); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    let msg = data?.detail || `Erreur ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join(" ; ");
    throw new Error(msg);
  }
  return data;
}

function toast(msg, err = false) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 4200);
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); throw e; }
}

const go = (hash) => { if (location.hash !== hash) location.hash = hash; else route(); };

function initials(name) {
  return (name || "?").split(/[\s·\-&]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}
const PALETTE = [["#E9F0FF", "#2453C9"], ["#E2F6F1", "#0B7A65"], ["#FFF3DD", "#8A5300"], ["#FDECEF", "#B4233C"], ["#EFEDFF", "#4B3FE0"], ["#EEF0F6", "#4A5266"]];
function mono(name, size = 40) {
  let h = 0; for (const c of name || "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const [bg, fg] = PALETTE[h % PALETTE.length];
  return `<span class="mono" style="background:${bg};color:${fg};width:${size}px;height:${size}px">${esc(initials(name))}</span>`;
}

function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  if (s < 172800) return `hier, ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}
const monthLabel = (iso) => iso ? new Date(iso + "T00:00:00").toLocaleDateString("fr-FR", { month: "long", year: "numeric" }).replace(/^./, (c) => c.toUpperCase()) : "";

const STATUS = {
  decouverte: ["Découverte", "b-mute"], qualifiee: ["À préparer", "b-mute"], preparation: ["En préparation", "b-blue"],
  a_valider: ["À valider", "b-violet"], soumission: ["En cours d'envoi", "b-blue"], confirmee: ["Envoyée", "b-green"],
  echec: ["Échec", "b-red"], incertaine: ["Incertaine", "b-amber"], ignoree: ["Ignorée", "b-mute"],
};
const statusBadge = (s) => `<span class="badge ${STATUS[s]?.[1] || "b-mute"}">${esc(STATUS[s]?.[0] || s)}</span>`;
const MODES = { auto: "Envoi automatique", assisted: "Envoi assisté", manual: "Envoi manuel" };

function appState(a) {
  if (a.statut === "a_valider") {
    if (a.mode === "manual") return `<span class="badge b-mute">Envoi manuel</span>`;
    if (a.open_questions) return `<span class="badge b-amber">${a.open_questions > 1 ? a.open_questions + " questions ouvertes" : "Question ouverte"}</span>`;
    return `<span class="badge b-violet">Prête à valider</span>`;
  }
  return statusBadge(a.statut);
}

const ICON = {
  home: '<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
  board: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16M15 4v16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  bell: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  out: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  tick: '<path d="M5 12l5 5 9-10"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
};
const svg = (name, size = 20, sw = 1.8) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name]}</svg>`;
const LOGO = `<span class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-8 6 8"/><circle cx="12" cy="19" r="1.2" fill="#fff"/></svg></span>`;

// ---------- squelette ----------
let navBadge = 0;
function shell(active, content) {
  const name = `${ME.first_name || ""} ${ME.last_name ? ME.last_name[0] + "." : ""}`.trim() || ME.email;
  const link = (hash, key, icon, label, extra = "") =>
    `<a href="${hash}" ${active === key ? 'aria-current="page"' : ""}>${svg(icon)}${label}${extra}</a>`;
  $app.innerHTML = `
  <div class="shell">
    <nav class="nav" aria-label="Navigation principale">
      <div class="brand">${LOGO}<span>CandiPilot</span></div>
      ${link("#/", "home", "home", "Accueil")}
      ${link("#/a-valider", "validate", "check", "À valider", navBadge ? `<span class="badge b-violet">${navBadge}</span>` : "")}
      ${link("#/candidatures", "apps", "board", "Candidatures")}
      ${link("#/offres", "offers", "search", "Offres")}
      <div class="sep"></div>
      ${link("#/criteres", "prefs", "sliders", "Critères")}
      ${link("#/profil", "profile", "user", "Profil & documents")}
      <div class="me">
        <span class="avatar">${esc(initials(name))}</span>
        <span style="display:flex;flex-direction:column;line-height:1.3;min-width:0;flex:1">
          <b style="font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis">${esc(name)}</b>
          <small style="font-size:12px;color:var(--muted)">Formule gratuite</small>
        </span>
        <button class="btn btn-quiet btn-sm" id="logout" title="Se déconnecter" aria-label="Se déconnecter">${svg("out", 18)}</button>
      </div>
    </nav>
    <main>${content}</main>
  </div>`;
  document.getElementById("logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); ME = null; go("#/login"); };
}

function modal(html, onMount) {
  const el = document.createElement("div");
  el.className = "scrim";
  el.innerHTML = `<div class="card modal pop" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => el.remove();
  el.addEventListener("click", (e) => { if (e.target === el || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
  document.body.appendChild(el);
  onMount?.(el, close);
  el.querySelector("input,textarea,select,button")?.focus();
}

// ---------- authentification ----------
function authView(mode) {
  const reg = mode === "register";
  $app.innerHTML = `
  <div class="auth"><div class="card pop">
    <div class="row">${LOGO}<b style="font-size:19px;letter-spacing:-.03em">CandiPilot</b></div>
    <h1>${reg ? "Créer un compte" : "Connexion"}</h1>
    <p class="sub" style="margin:0 0 20px">${reg ? "Ton copilote de candidatures, en quelques minutes." : "Content de te revoir."}</p>
    <form class="form" id="authForm" novalidate>
      ${reg ? `<div class="form two"><label class="f">Prénom<input type="text" name="first_name" autocomplete="given-name" required></label>
               <label class="f">Nom<input type="text" name="last_name" autocomplete="family-name"></label></div>` : ""}
      <label class="f">E-mail<input type="email" name="email" autocomplete="email" required></label>
      <label class="f">Mot de passe<input type="password" name="password" autocomplete="${reg ? "new-password" : "current-password"}" minlength="8" required>
        ${reg ? '<span class="hint">8 caractères minimum.</span>' : ""}</label>
      <button class="btn btn-primary" type="submit">${reg ? "Créer mon compte" : "Se connecter"}</button>
    </form>
    <p class="sub" style="font-size:14px;margin:18px 0 0">${reg ? 'Déjà inscrit ? <a href="#/login">Se connecter</a>' : 'Pas encore de compte ? <a href="#/register">Créer un compte</a>'}</p>
  </div></div>`;
  document.getElementById("authForm").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector("button"); btn.disabled = true;
    try { await act(() => api(`/api/auth/${reg ? "register" : "login"}`, { method: "POST", body })); ME = await api("/api/auth/me"); go(reg ? "#/profil" : "#/"); }
    catch { btn.disabled = false; }
  };
}

// ---------- accueil ----------
async function dashboardView() {
  const d = await api("/api/dashboard");
  navBadge = d.stats.to_validate;
  const s = d.stats, cp = d.copilot;
  const max = Math.max(1, ...d.chart.map((w) => w.confirmed + w.pending));
  const running = cp.jobs.find((j) => j.statut === "en_cours" || j.statut === "en_attente");
  const JOBN = { search: "Recherche des offres", prepare: "Préparation d'un dossier", submit: "Envoi d'une candidature", verify: "Vérification d'un envoi" };
  shell("home", `
    <header class="head">
      <div class="titles"><h1>Bonjour ${esc(ME.first_name || "")} 👋</h1>
        <p class="sub" style="margin:4px 0 0">Votre copilote continue de rechercher les meilleures opportunités pour vous.</p></div>
      <button class="bell" id="bell" aria-label="Notifications">${svg("bell", 20)}${d.unread ? '<span class="pip"></span>' : ""}</button>
      <a class="btn btn-primary" href="#/a-valider">Valider ${s.to_validate} dossier${s.to_validate > 1 ? "s" : ""}</a>
    </header>
    ${cp.fake ? `<div class="banner"><span class="badge b-amber">Robot simulé</span> Mode démonstration : aucune candidature réelle n'est envoyée.</div>` : ""}
    <section class="grid g4">
      <div class="card pad stat"><div class="label">Offres analysées</div><div class="value">${s.offers.toLocaleString("fr-FR")}</div><div class="note ${s.offers_week ? "up" : ""}">+${s.offers_week} cette semaine</div></div>
      <div class="card pad stat"><div class="label">Correspondent à votre profil</div><div class="value">${s.matching}</div><div class="note">dont ${s.matching_high} au-dessus de 85 %</div></div>
      <div class="card pad stat"><div class="label">Candidatures envoyées</div><div class="value">${s.sent}</div><div class="note">envoi confirmé · ${s.pending} en attente</div></div>
      <div class="card pad stat hero" onclick="location.hash='#/a-valider'"><div class="label">À valider par vous</div><div class="value">${s.to_validate}</div><div class="note" style="font-weight:600">Voir les dossiers →</div></div>
    </section>
    <section class="grid g2">
      <div class="card pad copilot stack">
        <div class="spread"><div><h2>Votre copilote</h2><div class="sub" style="font-size:13px">${cp.last_search_at ? "Dernière recherche " + ago(cp.last_search_at) : "Pas encore de recherche"}</div></div>
          <span class="badge" style="background:${cp.paused ? "rgba(255,255,255,.12)" : "rgba(18,184,154,.18)"};color:${cp.paused ? "#fff" : "#5EE6C8"}"><span class="dot" style="background:${cp.paused ? "#aaa" : "#12B89A"}"></span>${cp.paused ? "En pause" : "Actif"}</span></div>
        <div>
          ${running ? `<div class="task"><span class="tick"><span class="spin"></span></span>${esc(JOBN[running.type] || running.type)}<span class="meta">${esc(running.progress || (running.statut === "en_attente" ? "En file" : "En cours"))}</span></div>` : ""}
          <div class="task"><span class="tick">${svg("tick", 14, 2.2)}</span>Recherche automatique des offres<span class="meta">${cp.paused ? "Suspendue" : "Toutes les " + cp.every_hours + " h"}</span></div>
          <div class="task"><span class="tick">${svg("tick", 14, 2.2)}</span>Vérification des confirmations<span class="meta">Après chaque envoi</span></div>
          ${cp.jobs.filter((j) => j !== running).slice(0, 1).map((j) => `<div class="task"><span class="tick">${svg("tick", 14, 2.2)}</span>${esc(JOBN[j.type] || j.type)}<span class="meta">${j.statut === "erreur" ? "Erreur" : "Terminé"} · ${ago(j.updated_at)}</span></div>`).join("")}
        </div>
        <div class="row" style="flex-wrap:wrap;margin-top:auto">
          <button class="btn btn-ghost" id="pause">${cp.paused ? "Reprendre" : "Mettre en pause"}</button>
          <button class="btn btn-ghost" id="searchNow">Lancer une recherche</button>
          <a class="btn btn-ghost" href="#/criteres">Modifier les critères</a>
        </div>
      </div>
      <div class="card pad">
        <div class="spread"><div><h2>Candidatures envoyées</h2><div class="sub" style="font-size:13px">8 dernières semaines</div></div>
          <div class="legend"><span><i style="background:#5A4DF5"></i>Envoi confirmé</span><span><i style="background:#C9D3FF"></i>En attente</span></div></div>
        <div class="chart" role="img" aria-label="Candidatures envoyées par semaine">
          ${d.chart.map((w) => { const t = w.confirmed + w.pending; return `<div class="col"><span class="n">${t || ""}</span>
            <div class="bar" style="height:${(t / max) * 140}px" title="${w.week} : ${w.confirmed} confirmée(s), ${w.pending} en attente"><div class="p" style="flex:${w.pending}"></div><div class="c" style="flex:${w.confirmed}"></div></div>
            <span class="w">${w.week}</span></div>`; }).join("")}
        </div>
      </div>
    </section>
    <section class="grid g2">
      <div class="card pad">
        <div class="spread" style="margin-bottom:8px"><h2>Candidatures à valider</h2><span class="badge b-violet">${s.to_validate} dossier${s.to_validate > 1 ? "s" : ""}</span></div>
        ${d.to_validate.length ? d.to_validate.map((a) => `
          <a class="item" href="#/candidature/${a.id}">${mono(a.entreprise)}<span class="grow"><div class="t">${esc(a.poste)}</div><div class="s">${esc(a.entreprise)}${a.localisation ? " · " + esc(a.localisation) : ""}</div></span>${appState(a)}${svg("chev", 18)}</a>`).join("")
          : `<div class="empty">Aucun dossier à valider. Prépare une offre recommandée ci-dessous.</div>`}
      </div>
      <div class="card pad">
        <div class="spread" style="margin-bottom:8px"><h2>Activité récente</h2><button class="btn btn-quiet btn-sm" id="allEvents">Tout voir</button></div>
        ${d.events.length ? d.events.map(eventRow).join("") : `<div class="empty">Rien pour le moment.</div>`}
      </div>
    </section>
    <section class="stack">
      <div class="spread"><h2 style="font-size:19px">Offres recommandées</h2><a href="#/offres" style="font-weight:600;font-size:14px">Tout voir</a></div>
      <div class="grid g3">
        ${d.recommended.length ? d.recommended.map((a) => `
          <div class="card pad stack">
            <div class="spread">${mono(a.entreprise, 44)}<span class="badge b-violet">${a.score} % match</span></div>
            <div><div style="font-weight:600;font-size:17px">${esc(a.poste)}</div><div class="sub">${esc(a.entreprise)}</div></div>
            <div class="chips">${a.localisation ? `<span class="badge b-mute">${esc(a.localisation)}</span>` : ""}${a.contrat ? `<span class="badge b-mute">${esc(a.contrat)}</span>` : ""}${a.start_date ? `<span class="badge b-mute">${esc(monthLabel(a.start_date))}</span>` : ""}</div>
            <button class="btn btn-ghost" data-prepare="${a.id}">Préparer ma candidature</button>
          </div>`).join("") : `<div class="card empty">Aucune offre pour l'instant. Renseigne tes critères puis lance une recherche.</div>`}
      </div>
    </section>`);
  document.getElementById("pause").onclick = () => act(() => api(`/api/copilot/${cp.paused ? "resume" : "pause"}`, { method: "POST" })).then(route);
  document.getElementById("searchNow").onclick = () => act(() => api("/api/search", { method: "POST" }), "Recherche lancée : une fenêtre du robot peut s'ouvrir.").then(route);
  document.getElementById("bell").onclick = document.getElementById("allEvents").onclick = showEvents;
  $app.querySelectorAll("[data-prepare]").forEach((b) => b.onclick = () => prepare(b.dataset.prepare));
  schedulePoll(running ? 3000 : 15000);
}

const EV_COLOR = { success: "#12B89A", info: "#5A4DF5", warning: "#F5A524", error: "#F04438" };
const eventRow = (e) => `<div class="ev"><span class="dot" style="background:${EV_COLOR[e.kind] || "#8A93A6"}"></span><div><div style="font-size:14px">${esc(e.message)}</div><div class="s">${ago(e.created_at)}</div></div></div>`;

async function showEvents() {
  const evs = await api("/api/events");
  await api("/api/events/read", { method: "POST" });
  modal(`<div class="spread"><h2>Notifications</h2><button class="btn btn-quiet btn-sm" data-close>Fermer</button></div>
         <div style="margin-top:12px">${evs.length ? evs.map(eventRow).join("") : '<div class="empty">Aucune notification.</div>'}</div>`);
}

async function prepare(id) {
  await act(() => api(`/api/applications/${id}/prepare`, { method: "POST" }), "Préparation lancée : lettre et lecture du formulaire.");
  go(`#/candidature/${id}`);
}

// ---------- à valider ----------
async function toValidateView() {
  const [ready, prep] = await Promise.all([api("/api/applications?statut=a_valider"), api("/api/applications?statut=preparation")]);
  navBadge = ready.length;
  shell("validate", `
    <header class="head"><div class="titles"><h1>À valider</h1><p class="sub" style="margin:4px 0 0">Les dossiers préparés par ton copilote attendent ta décision.</p></div></header>
    <div class="card pad">
      ${ready.length ? ready.map((a) => `<a class="item" href="#/candidature/${a.id}">${mono(a.entreprise)}<span class="grow"><div class="t">${esc(a.poste)}</div><div class="s">${esc(a.entreprise)} · ${esc(MODES[a.mode])} · ${a.score} %</div></span>${appState(a)}${svg("chev", 18)}</a>`).join("")
        : `<div class="empty">Aucun dossier à valider.</div>`}
    </div>
    ${prep.length ? `<h2 style="font-size:17px">En préparation</h2><div class="card pad">${prep.map((a) => `<a class="item" href="#/candidature/${a.id}">${mono(a.entreprise)}<span class="grow"><div class="t">${esc(a.poste)}</div><div class="s">${esc(a.entreprise)}</div></span><span class="badge b-blue"><span class="spin"></span>En préparation</span></a>`).join("")}</div>` : ""}`);
  schedulePoll(prep.length ? 3000 : 0);
}

// ---------- validation d'une candidature ----------
async function applicationView(id) {
  const [a, docs] = await Promise.all([api(`/api/applications/${id}`), api("/api/documents")]);
  const cvs = docs.filter((d) => d.type === "cv");
  const ready = a.readiness.filter((r) => r.ok).length;
  const openQ = a.questions.filter((q) => !q.answer?.trim());
  const editable = !["soumission", "confirmee"].includes(a.statut);
  const busy = a.job && ["en_attente", "en_cours"].includes(a.job.statut);
  const pa = a.prepared_answers || {};
  const prepared = [["Date de disponibilité", pa.availability], ["Durée du stage", pa.duration], ["Langues", pa.languages], ["Convention de stage", pa.internship_agreement], ["Genre (formulaire JobTeaser)", pa.gender]];
  const isJT = a.apply_type === "jobteaser";
  const canValidate = a.statut === "a_valider" && a.mode !== "manual" && !(a.mode === "auto" && openQ.length) && !busy;

  let banner = "";
  if (a.statut === "confirmee") banner = `<section class="card pad pop" style="background:#E2F6F1;border-color:#BDEBDF"><b style="color:#08584A">Candidature envoyée</b><div style="font-size:14px;color:#0B6B59">Preuve : ${esc(a.preuve)} · ${a.date_envoi ? new Date(a.date_envoi).toLocaleString("fr-FR") : ""}</div></section>`;
  else if (a.statut === "soumission") banner = `<section class="card pad pop" style="background:#E9F0FF;border-color:#C9D7FF"><b style="color:#1D3F9E"><span class="spin"></span> Envoi en cours</b><div style="font-size:14px;color:#2453C9">${a.mode === "assisted" ? "Le robot a ouvert le formulaire : vérifie-le et clique « Postuler maintenant » dans sa fenêtre." : "Le robot remplit et envoie le formulaire."} Elle passera en « Envoyée » seulement quand JobTeaser confirmera la réception.</div></section>`;
  else if (a.statut === "preparation") banner = `<section class="card pad" style="background:#E9F0FF;border-color:#C9D7FF"><b style="color:#1D3F9E"><span class="spin"></span> Préparation en cours</b><div style="font-size:14px;color:#2453C9">${esc(a.job?.progress || "Le robot rédige la lettre et lit le formulaire de candidature.")}</div></section>`;
  else if (a.statut === "echec") banner = `<section class="card pad" style="background:#FDECEA;border-color:#F5C2BC"><b style="color:#912018">Échec</b><div style="font-size:14px;color:#B42318">${esc(a.detail)}</div></section>`;
  else if (a.statut === "incertaine") banner = `<section class="card pad" style="background:#FFF3DD;border-color:#F6E2B8"><b style="color:#8A5300">Envoi incertain</b><div style="font-size:14px;color:#8A5300">${esc(a.detail)} — lance une vérification ou marque-la comme envoyée si tu as la confirmation.</div></section>`;

  shell("validate", `
    <a href="#/a-valider" class="btn btn-quiet" style="align-self:flex-start;padding:0 8px">${svg("back", 16, 2)}Candidatures à valider</a>
    <header class="head">
      ${mono(a.entreprise, 60)}
      <div class="titles"><h1 style="font-size:30px">${esc(a.poste)}</h1>
        <span class="sub">${esc(a.entreprise)}${a.localisation ? " · " + esc(a.localisation) : ""}${a.start_date ? " · " + esc(monthLabel(a.start_date)) : ""} · ${isJT ? "JobTeaser" : "Site carrière"} · <a href="${esc(a.url)}" target="_blank" rel="noopener">Voir l'offre</a></span></div>
      <span class="badge b-violet" style="font-size:14px;padding:6px 14px">${a.score} % de compatibilité</span>
    </header>
    ${banner}
    <section class="modes" aria-label="Mode d'envoi">
      ${[["auto", "Envoi automatique", isJT ? "Le robot remplit et envoie, puis vérifie la preuve de réception." : "Indisponible : candidature sur le site de l'entreprise."],
         ["assisted", "Envoi assisté", isJT ? "Le robot pré-remplit, tu cliques sur « Postuler maintenant »." : "Indisponible : candidature sur le site de l'entreprise."],
         ["manual", "Envoi manuel", "Dossier prêt à télécharger, envoi par tes soins."]].map(([k, t, d]) =>
        `<button class="mode" data-mode="${k}" aria-pressed="${a.mode === k}" ${!editable || (k !== "manual" && !isJT) ? "disabled" : ""}><b>${a.mode === k ? "● " : ""}${t}</b><span>${d}</span></button>`).join("")}
    </section>
    <div class="side">
      <div class="stack" style="gap:20px">
        <section class="card pad grid g2" style="gap:24px">
          <div class="doc"><div class="thumb"><i class="d"></i><i></i><i></i><i class="v"></i><i></i></div>
            <div class="stack" style="gap:4px;min-width:0"><span class="hint">CV sélectionné</span>
              ${a.cv ? `<b style="overflow-wrap:anywhere">${esc(a.cv.name)}</b><span class="sub" style="font-size:13px">Version ${a.cv.version}</span>` : `<b>Aucun CV</b>`}
              ${editable && cvs.length ? `<select id="cvSel" aria-label="Changer de CV" style="min-height:36px;padding:4px 10px;font-size:14px">${cvs.map((d) => `<option value="${d.id}" ${a.cv?.id === d.id ? "selected" : ""}>${esc(d.name)} (v${d.version})</option>`).join("")}</select>` : `<a href="#/profil">Ajouter un CV</a>`}
            </div></div>
          <div class="doc"><div class="thumb"><i></i><i class="v"></i><i></i><i></i><i class="v"></i></div>
            <div class="stack" style="gap:4px;min-width:0"><span class="hint">Lettre adaptée</span>
              <b>${a.letter_path ? "Lettre PDF prête" : "Pas encore générée"}</b>
              <span class="sub" style="font-size:13px">${a.letter_needed === 0 ? "Non demandée par le recruteur" : a.letter_needed === 1 ? "Demandée par le recruteur" : "Adaptée aux missions de l'offre"}</span>
              <span class="row" style="gap:12px">${a.letter_path ? `<a href="/api/applications/${a.id}/letter" target="_blank" rel="noopener">Prévisualiser</a>` : ""}${editable && a.letter_text ? `<a href="#" id="editLetter">Modifier</a>` : ""}</span>
            </div></div>
        </section>
        <section class="card pad">
          <div class="spread" style="margin-bottom:6px"><h2>Réponses préparées</h2><span class="badge b-green">${prepared.filter((p) => p[1]).length} prêtes</span></div>
          <div class="answers">${prepared.map(([k, v]) => `<div class="a"><span style="color:${v ? "#12B89A" : "#C3C8D4"}">${svg("tick", 18, 2.2)}</span><div><div class="k">${k}</div><div class="v">${v ? esc(v) : '<span class="sub" style="font-weight:400">Non renseigné — <a href="#/profil">compléter le profil</a></span>'}</div></div></div>`).join("")}</div>
        </section>
        ${a.questions.length ? `
        <section class="card pad warn stack">
          <div class="spread"><h2>Votre intervention est nécessaire</h2><span class="badge b-amber">${openQ.length} question${openQ.length > 1 ? "s" : ""} ouverte${openQ.length > 1 ? "s" : ""}</span></div>
          <p style="margin:0;font-size:14px">Le recruteur pose ${a.questions.length > 1 ? "ces questions" : "cette question"}. CandiPilot ne répond pas à ta place aux questions personnelles ou de motivation.</p>
          <form id="qForm" class="stack">${a.questions.map((q, i) => `<label class="f">${esc(q.label)}<textarea name="q${i}" maxlength="1500" placeholder="Ta réponse, en quelques phrases…" ${editable ? "" : "disabled"}>${esc(q.answer || "")}</textarea></label>`).join("")}
            ${editable ? `<button class="btn btn-ghost" type="submit" style="align-self:flex-start">Enregistrer mes réponses</button>` : ""}</form>
        </section>` : ""}
        ${a.detail && !["echec", "incertaine"].includes(a.statut) ? `<p class="hint" style="margin:0">Robot : ${esc(a.detail)}</p>` : ""}
      </div>
      <aside class="card pad stack" style="position:sticky;top:20px">
        <h2>Votre décision</h2>
        <div class="spread" style="font-size:14px"><span class="sub">Dossier prêt</span><b>${ready} / ${a.readiness.length}</b></div>
        <div class="progress"><i style="width:${(ready / a.readiness.length) * 100}%"></i></div>
        <ul style="list-style:none;margin:0;padding:0;font-size:13px;display:grid;gap:4px">${a.readiness.map((r) => `<li style="color:${r.ok ? "var(--green)" : "var(--muted)"}">${r.ok ? "✓" : "○"} ${esc(r.label)}</li>`).join("")}</ul>
        ${actionsFor(a, canValidate, openQ, busy)}
        <p class="hint" style="margin:0">${statusHelp(a, openQ)}</p>
      </aside>
    </div>`);

  $app.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => act(() => api(`/api/applications/${id}`, { method: "PUT", body: { mode: b.dataset.mode } })).then(route));
  document.getElementById("cvSel")?.addEventListener("change", (e) => act(() => api(`/api/applications/${id}`, { method: "PUT", body: { cv_document_id: e.target.value } }), "CV mis à jour.").then(route));
  document.getElementById("qForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const questions = a.questions.map((q, i) => ({ label: q.label, answer: (f.get("q" + i) || "").toString() }));
    act(() => api(`/api/applications/${id}`, { method: "PUT", body: { questions } }), "Réponses enregistrées.").then(route);
  });
  document.getElementById("editLetter")?.addEventListener("click", (e) => {
    e.preventDefault();
    modal(`<h2>Modifier la lettre</h2><p class="hint">Le PDF est regénéré à l'enregistrement.</p>
      <textarea id="lt" style="min-height:360px;margin:12px 0">${esc(a.letter_text)}</textarea>
      <div class="row" style="justify-content:flex-end"><button class="btn btn-ghost" data-close>Annuler</button><button class="btn btn-primary" id="saveLt">Enregistrer</button></div>`,
      (el, close) => el.querySelector("#saveLt").onclick = () => act(() => api(`/api/applications/${id}`, { method: "PUT", body: { letter_text: el.querySelector("#lt").value } }), "Lettre mise à jour.").then(() => { close(); route(); }));
  });
  const on = (sel, fn) => document.getElementById(sel)?.addEventListener("click", fn);
  on("doValidate", () => act(() => api(`/api/applications/${id}/validate`, { method: "POST" }), a.mode === "assisted" ? "Le robot ouvre le formulaire : clique « Postuler maintenant » dans sa fenêtre." : "Candidature validée : envoi en cours.").then(route));
  on("doPrepare", () => act(() => api(`/api/applications/${id}/prepare`, { method: "POST" }), "Préparation lancée.").then(route));
  on("doIgnore", () => act(() => api(`/api/applications/${id}/ignore`, { method: "POST" }), "Offre ignorée.").then(() => go("#/a-valider")));
  on("doRestore", () => act(() => api(`/api/applications/${id}/restore`, { method: "POST" }), "Offre restaurée.").then(route));
  on("doRetry", () => act(() => api(`/api/applications/${id}/retry`, { method: "POST" })).then(route));
  on("doVerify", () => act(() => api(`/api/applications/${id}/verify`, { method: "POST" }), "Vérification lancée.").then(route));
  on("doMarkSent", () => { if (confirm("Confirmes-tu avoir envoyé cette candidature toi-même ?")) act(() => api(`/api/applications/${id}/mark-sent`, { method: "POST" }), "Candidature marquée comme envoyée.").then(route); });
  on("doModify", () => document.querySelector("#qForm textarea, #cvSel, [data-mode]")?.focus());
  schedulePoll(["preparation", "soumission"].includes(a.statut) || busy ? 3000 : 0);
}

function actionsFor(a, canValidate, openQ, busy) {
  const s = a.statut;
  if (["decouverte", "qualifiee"].includes(s)) return `<button class="btn btn-ai" id="doPrepare">Préparer le dossier</button><button class="btn btn-ghost" id="doIgnore">Ignorer</button>`;
  if (s === "preparation" || busy) return `<button class="btn btn-ai" disabled><span class="spin"></span> Robot au travail…</button>`;
  if (s === "a_valider" && a.mode === "manual") return `
    <a class="btn btn-ghost" href="${esc(a.url)}" target="_blank" rel="noopener">Ouvrir le site de l'offre</a>
    ${a.letter_path ? `<a class="btn btn-ghost" href="/api/applications/${a.id}/letter" target="_blank" rel="noopener">Télécharger la lettre</a>` : ""}
    ${a.cv ? `<a class="btn btn-ghost" href="/api/documents/${a.cv.id}/file" target="_blank" rel="noopener">Télécharger le CV</a>` : ""}
    <button class="btn btn-primary" id="doMarkSent">Marquer comme envoyée</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  if (s === "a_valider") return `<button class="btn btn-ai" id="doValidate" ${canValidate ? "" : "disabled"}>Valider la candidature</button>
    <div class="row"><button class="btn btn-ghost" style="flex:1" id="doModify">Modifier</button><button class="btn btn-ghost" style="flex:1" id="doIgnore">Ignorer</button></div>
    <button class="btn btn-quiet btn-sm" id="doPrepare">Relire le formulaire</button>`;
  if (s === "echec") return `<button class="btn btn-primary" id="doRetry">Réessayer</button>${a.apply_type === "jobteaser" ? '<button class="btn btn-ghost" id="doVerify">Vérifier sur JobTeaser</button>' : ""}<button class="btn btn-ghost" id="doMarkSent">Je l'ai envoyée moi-même</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  if (s === "incertaine") return `<button class="btn btn-primary" id="doVerify">Vérifier sur JobTeaser</button><button class="btn btn-ghost" id="doMarkSent">Marquer comme envoyée</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  if (s === "ignoree") return `<button class="btn btn-ghost" id="doRestore">Restaurer l'offre</button>`;
  return "";
}

function statusHelp(a, openQ) {
  if (a.statut === "a_valider" && a.mode === "auto" && openQ.length) return "Réponds à la question ouverte pour pouvoir valider en envoi automatique, ou passe en envoi assisté.";
  if (a.statut === "a_valider" && a.mode === "assisted") return "Le robot remplit le formulaire dans sa fenêtre ; tu cliques toi-même « Postuler maintenant ». Il détecte ensuite la confirmation.";
  if (a.statut === "a_valider" && a.mode === "auto") return "Le robot envoie lui-même la candidature, puis attend la preuve de réception de JobTeaser.";
  if (a.statut === "confirmee") return "Une candidature n'est « Envoyée » qu'avec une preuve vérifiable.";
  return "";
}

// ---------- candidatures (kanban / liste) ----------
let boardMode = localStorage.getItem("cp_board") || "kanban";
async function applicationsView() {
  const apps = await api("/api/applications");
  const attention = apps.filter((a) => ["a_valider", "echec", "incertaine"].includes(a.statut)).length;
  const lanes = [
    ["À préparer", ["qualifiee", "decouverte"]], ["En préparation", ["preparation"]], ["En attente de validation", ["a_valider"]],
    ["En cours d'envoi", ["soumission", "incertaine"]], ["Envoyées", ["confirmee"]], ["Échec / ignorées", ["echec", "ignoree"]],
  ];
  const sub = (a) => a.statut === "qualifiee" || a.statut === "decouverte" ? `Trouvée ${ago(a.created_at)} · ${a.score} %`
    : a.statut === "a_valider" ? (a.mode === "manual" ? "Envoi manuel" : a.open_questions ? `${a.open_questions} question(s) ouverte(s)` : "CV et lettre prêts")
    : a.statut === "soumission" ? `Envoi lancé ${ago(a.updated_at)}` : a.statut === "confirmee" ? `Confirmée ${ago(a.date_envoi)}` : esc(STATUS[a.statut][0]);
  const card = (a) => `<div class="kcard" data-open="${a.id}" tabindex="0"><div class="row" style="align-items:flex-start">${mono(a.entreprise, 34)}<div style="min-width:0"><div style="font-weight:600;font-size:14px;line-height:1.3">${esc(a.poste)}</div><div class="k">${esc(a.entreprise)}</div></div></div><div class="k">${sub(a)}</div></div>`;
  shell("apps", `
    <header class="head"><div class="titles"><h1>Mes candidatures</h1><p class="sub" style="margin:4px 0 0">${apps.length} candidature${apps.length > 1 ? "s" : ""} · ${attention} demande${attention > 1 ? "nt" : ""} ton attention</p></div>
      <div class="seg" role="group" aria-label="Affichage"><button data-b="kanban" aria-pressed="${boardMode === "kanban"}">Kanban</button><button data-b="list" aria-pressed="${boardMode === "list"}">Liste</button></div>
      <a class="btn btn-primary" href="#/a-valider">Valider ${navBadge} dossier${navBadge > 1 ? "s" : ""}</a></header>
    <div class="banner"><span style="color:var(--violet)">ⓘ</span>Une candidature passe en « Envoyée » uniquement quand le site de l'employeur a confirmé la réception. Tant que ce n'est pas le cas, elle reste « En cours d'envoi ».</div>
    ${boardMode === "kanban" ? `<div class="kanban">${lanes.map(([t, ss]) => { const items = apps.filter((a) => ss.includes(a.statut)); return `<div class="lane"><div class="lh"><span>${t}</span><span>${items.length}</span></div>${items.map(card).join("") || '<div class="k" style="padding:6px;font-size:13px;color:var(--muted)">—</div>'}</div>`; }).join("")}</div>`
    : `<div class="card tablewrap"><table><thead><tr><th>Poste</th><th>Entreprise</th><th>Match</th><th>Mode</th><th>Statut</th><th>Mis à jour</th><th></th></tr></thead><tbody>
      ${apps.map((a) => `<tr><td><a href="#/candidature/${a.id}" style="font-weight:600;color:var(--ink);text-decoration:none">${esc(a.poste)}</a></td><td>${esc(a.entreprise)}</td><td>${a.score} %</td><td>${esc(MODES[a.mode] || "")}</td><td>${statusBadge(a.statut)}</td><td class="sub">${ago(a.updated_at)}</td>
        <td>${["soumission", "confirmee"].includes(a.statut) ? "" : `<button class="btn btn-quiet btn-sm" data-del="${a.id}" aria-label="Supprimer">Supprimer</button>`}</td></tr>`).join("") || '<tr><td colspan="7" class="empty">Aucune candidature.</td></tr>'}
      </tbody></table></div>`}`);
  $app.querySelectorAll("[data-b]").forEach((b) => b.onclick = () => { boardMode = b.dataset.b; localStorage.setItem("cp_board", boardMode); route(); });
  $app.querySelectorAll("[data-open]").forEach((c) => { c.onclick = () => go(`#/candidature/${c.dataset.open}`); c.onkeydown = (e) => { if (e.key === "Enter") c.click(); }; });
  $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { if (confirm("Supprimer cette candidature et son offre ?")) act(() => api(`/api/applications/${b.dataset.del}`, { method: "DELETE" }), "Supprimée.").then(route); });
  schedulePoll(apps.some((a) => ["preparation", "soumission"].includes(a.statut)) ? 4000 : 0);
}

// ---------- offres (CRUD) ----------
async function offersView() {
  const q = sessionStorage.getItem("cp_q") || "";
  const offers = await api(`/api/offers?q=${encodeURIComponent(q)}`);
  shell("offers", `
    <header class="head"><div class="titles"><h1>Offres</h1><p class="sub" style="margin:4px 0 0">${offers.length} offre${offers.length > 1 ? "s" : ""}, classées par compatibilité avec ton profil.</p></div>
      <button class="btn btn-ghost" id="searchNow">Lancer une recherche</button><button class="btn btn-primary" id="addOffer">Ajouter une offre</button></header>
    <input type="text" id="q" placeholder="Filtrer par poste, entreprise ou lieu…" value="${esc(q)}" aria-label="Filtrer les offres">
    <div class="card tablewrap"><table><thead><tr><th>Poste</th><th>Entreprise</th><th>Lieu</th><th>Début</th><th>Match</th><th>Statut</th><th></th></tr></thead><tbody>
      ${offers.map((o) => `<tr><td><a href="${esc(o.url)}" target="_blank" rel="noopener" style="font-weight:600;color:var(--ink)">${esc(o.poste)}</a><div class="hint">${o.apply_type === "jobteaser" ? "Candidature simplifiée" : "Site de l'entreprise"} · ${esc(o.source)}</div></td>
        <td>${esc(o.entreprise)}</td><td class="sub">${esc(o.localisation)}</td><td class="sub">${esc(monthLabel(o.start_date))}</td>
        <td><span class="badge ${o.score >= 85 ? "b-green" : o.score >= 50 ? "b-violet" : "b-mute"}">${o.score} %</span></td><td>${o.statut ? statusBadge(o.statut) : ""}</td>
        <td style="white-space:nowrap">${o.application_id ? `<a class="btn btn-quiet btn-sm" href="#/candidature/${o.application_id}">Ouvrir</a>` : ""}<button class="btn btn-quiet btn-sm" data-edit="${o.id}">Modifier</button>
          ${["soumission", "confirmee"].includes(o.statut) ? "" : `<button class="btn btn-quiet btn-sm" data-del="${o.id}" style="color:var(--red)">Supprimer</button>`}</td></tr>`).join("") || '<tr><td colspan="7" class="empty">Aucune offre. Lance une recherche ou ajoute une offre à la main.</td></tr>'}
    </tbody></table></div>`);
  let t; document.getElementById("q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { sessionStorage.setItem("cp_q", e.target.value); route().then(() => { const i = document.getElementById("q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  document.getElementById("addOffer").onclick = () => offerForm();
  document.getElementById("searchNow").onclick = () => act(() => api("/api/search", { method: "POST" }), "Recherche lancée : une fenêtre du robot peut s'ouvrir.");
  $app.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => offerForm(offers.find((o) => o.id === b.dataset.edit)));
  $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { if (confirm("Supprimer cette offre ?")) act(() => api(`/api/offers/${b.dataset.del}`, { method: "DELETE" }), "Offre supprimée.").then(route); });
}

function offerForm(o = null) {
  modal(`<h2>${o ? "Modifier l'offre" : "Ajouter une offre"}</h2>
    <form class="form" id="of" style="margin-top:16px">
      <label class="f">Lien de l'offre<input type="url" name="url" required value="${esc(o?.url)}" placeholder="https://…"></label>
      <div class="form two"><label class="f">Poste<input type="text" name="poste" required value="${esc(o?.poste)}"></label>
        <label class="f">Entreprise<input type="text" name="entreprise" required value="${esc(o?.entreprise)}"></label>
        <label class="f">Lieu<input type="text" name="localisation" value="${esc(o?.localisation)}"></label>
        <label class="f">Contrat<input type="text" name="contrat" value="${esc(o?.contrat)}" placeholder="Stage 6 mois"></label>
        <label class="f">Domaine<input type="text" name="domaine" value="${esc(o?.domaine)}" placeholder="Private Debt, M&A…"></label>
        <label class="f">Date de début<input type="date" name="start_date" value="${esc(o?.start_date)}"></label></div>
      <label class="f">Type de candidature<select name="apply_type"><option value="jobteaser" ${o?.apply_type !== "external" ? "selected" : ""}>JobTeaser — candidature simplifiée</option><option value="external" ${o?.apply_type === "external" ? "selected" : ""}>Site de l'entreprise (envoi manuel)</option></select></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn btn-ghost" data-close>Annuler</button><button class="btn btn-primary" type="submit">${o ? "Enregistrer" : "Ajouter"}</button></div>
    </form>`, (el, close) => el.querySelector("#of").onsubmit = (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(e.target));
      act(() => api(o ? `/api/offers/${o.id}` : "/api/offers", { method: o ? "PUT" : "POST", body }), o ? "Offre mise à jour." : "Offre ajoutée.").then(() => { close(); route(); });
    });
}

// ---------- critères (préférences) ----------
async function prefsView() {
  const p = await api("/api/preferences");
  const lists = { metiers: [...p.metiers], zones: [...p.zones], contrats: [...p.contrats] };
  shell("prefs", `
    <header class="head"><div class="titles"><h1>Critères de recherche</h1><p class="sub" style="margin:4px 0 0">Métiers, zones et contrats que ton copilote recherche pour toi.</p></div></header>
    <form class="card pad form" id="pf">
      ${[["metiers", "Métiers ciblés (mots-clés JobTeaser)", "ex. private debt, M&A, corporate finance"], ["zones", "Zones géographiques", "ex. Paris, France — vide = partout"], ["contrats", "Types de contrat", "ex. stage, alternance"]].map(([k, l, ph]) => `
        <div class="f" style="display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600">${l}
          <div class="chips" id="c_${k}"></div>
          <input type="text" data-list="${k}" placeholder="${ph} — Entrée pour ajouter" aria-label="${l}"></div>`).join("")}
      <div class="form two">
        <label class="f">Date de début minimale<input type="date" name="start_from" value="${esc(p.start_from)}"><span class="hint">Les offres qui commencent avant sont écartées.</span></label>
        <label class="f">Score minimal pour retenir une offre<input type="number" name="min_score" min="0" max="100" value="${p.min_score}"></label>
        <label class="f">Pages de résultats lues par mot-clé<input type="number" name="max_pages" min="1" max="10" value="${p.max_pages}"></label>
        <label class="f">Recherche automatique toutes les (heures)<input type="number" name="search_every_hours" min="1" max="168" value="${p.search_every_hours}"></label>
        <label class="f">Mode d'envoi par défaut<select name="default_mode"><option value="auto" ${p.default_mode === "auto" ? "selected" : ""}>Envoi automatique</option><option value="assisted" ${p.default_mode === "assisted" ? "selected" : ""}>Envoi assisté</option><option value="manual" ${p.default_mode === "manual" ? "selected" : ""}>Envoi manuel</option></select></label>
      </div>
      <label class="check"><input type="checkbox" name="include_external" ${p.include_external ? "checked" : ""}> Inclure les offres à finir sur le site de l'entreprise (envoi manuel)</label>
      <div class="row"><button class="btn btn-primary" type="submit">Enregistrer</button><button class="btn btn-ghost" type="button" id="searchNow">Enregistrer et lancer une recherche</button></div>
    </form>`);
  const draw = (k) => { document.getElementById("c_" + k).innerHTML = lists[k].map((v, i) => `<span class="chip">${esc(v)}<button type="button" data-rm="${k}:${i}" aria-label="Retirer ${esc(v)}">×</button></span>`).join("") || '<span class="hint">Aucun</span>'; };
  Object.keys(lists).forEach(draw);
  $app.querySelectorAll("[data-list]").forEach((inp) => inp.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); const v = inp.value.trim(); if (v && !lists[inp.dataset.list].includes(v)) { lists[inp.dataset.list].push(v); draw(inp.dataset.list); } inp.value = ""; }
  });
  document.getElementById("pf").addEventListener("click", (e) => { const b = e.target.closest("[data-rm]"); if (!b) return; const [k, i] = b.dataset.rm.split(":"); lists[k].splice(+i, 1); draw(k); });
  const save = async () => {
    $app.querySelectorAll("[data-list]").forEach((inp) => { const v = inp.value.trim(); if (v && !lists[inp.dataset.list].includes(v)) lists[inp.dataset.list].push(v); inp.value = ""; });
    const f = new FormData(document.getElementById("pf"));
    const body = { ...lists, start_from: f.get("start_from") || "", min_score: +f.get("min_score"), max_pages: +f.get("max_pages"),
      search_every_hours: +f.get("search_every_hours"), default_mode: f.get("default_mode"), include_external: f.get("include_external") === "on" };
    await act(() => api("/api/preferences", { method: "PUT", body }), "Critères enregistrés. Les scores ont été recalculés.");
    Object.keys(lists).forEach(draw);
  };
  document.getElementById("pf").onsubmit = (e) => { e.preventDefault(); save(); };
  document.getElementById("searchNow").onclick = async () => { await save(); act(() => api("/api/search", { method: "POST" }), "Recherche lancée : une fenêtre du robot peut s'ouvrir."); };
}

// ---------- profil, documents, compte ----------
async function profileView() {
  const [p, docs] = await Promise.all([api("/api/profile"), api("/api/documents")]);
  const field = (k, l, type = "text", ph = "") => `<label class="f">${l}<input type="${type}" name="${k}" value="${esc(p[k])}" placeholder="${esc(ph)}"></label>`;
  shell("profile", `
    <header class="head"><div class="titles"><h1>Profil & documents</h1><p class="sub" style="margin:4px 0 0">Ces informations servent à remplir tes candidatures et à personnaliser tes lettres.</p></div>
      <button class="btn btn-ghost" id="importYaml" title="Importe config/profile.yaml du robot">Importer le profil du robot</button></header>
    <form class="card pad form" id="prf">
      <h2>Informations</h2>
      <div class="form two">${field("first_name", "Prénom")}${field("last_name", "Nom")}
        <label class="f">E-mail<input type="email" value="${esc(p.email)}" disabled><span class="hint">Identifiant de connexion.</span></label>
        ${field("phone", "Téléphone", "text", "+33…")}${field("linkedin", "LinkedIn", "url", "https://www.linkedin.com/in/…")}${field("location", "Ville", "text", "Paris, France")}
        ${field("school", "École")}${field("program", "Programme")}</div>
      <label class="f">Compétences<input type="text" name="competences" value="${esc(p.competences)}" placeholder="ex. Python, SQL, DCF, Excel, private debt"><span class="hint">Utilisées pour le calcul du % de compatibilité.</span></label>
      <h2 style="margin-top:8px">Réponses préparées</h2>
      <div class="form two">${field("availability", "Date de disponibilité", "text", "Mars 2027")}${field("duration", "Durée du stage", "text", "6 mois")}
        ${field("languages", "Langues", "text", "Français (natif), anglais (courant)")}${field("internship_agreement", "Convention de stage", "text", "Oui, fournie par l'école")}
        <label class="f">Genre (demandé par JobTeaser)<select name="gender"><option value="">—</option>${["Homme", "Femme", "Autre"].map((g) => `<option ${p.gender === g ? "selected" : ""}>${g}</option>`).join("")}</select></label></div>
      <h2 style="margin-top:8px">Modèle de lettre de motivation</h2>
      <label class="f"><span class="hint">Variables remplacées pour chaque offre : {role}, {company}, {de_company}, {domain_line}, {domain}.</span><textarea name="letter_template" style="min-height:260px">${esc(p.letter_template)}</textarea></label>
      <div><button class="btn btn-primary" type="submit">Enregistrer le profil</button></div>
    </form>
    <section class="card pad stack">
      <div class="spread"><h2>CV et lettres</h2>
        <form id="up" class="row" style="flex-wrap:wrap"><select name="type" style="width:auto"><option value="cv">CV</option><option value="lettre">Lettre</option></select>
          <input type="file" name="file" accept="application/pdf" required style="max-width:260px"><button class="btn btn-primary btn-sm" type="submit">Importer (PDF)</button></form></div>
      ${docs.length ? `<div class="tablewrap"><table><thead><tr><th>Document</th><th>Type</th><th>Version</th><th>Ajouté</th><th></th></tr></thead><tbody>
        ${docs.map((d) => `<tr><td><a href="/api/documents/${d.id}/file" target="_blank" rel="noopener" style="font-weight:600;color:var(--ink)">${esc(d.name)}</a> ${d.is_default ? '<span class="badge b-green">Par défaut</span>' : ""}</td><td>${d.type === "cv" ? "CV" : "Lettre"}</td><td>v${d.version}</td><td class="sub">${ago(d.created_at)}</td>
          <td style="white-space:nowrap">${d.is_default ? "" : `<button class="btn btn-quiet btn-sm" data-def="${d.id}">Par défaut</button>`}<button class="btn btn-quiet btn-sm" data-ren="${d.id}">Renommer</button><button class="btn btn-quiet btn-sm" data-del="${d.id}" style="color:var(--red)">Supprimer</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">Aucun document. Importe ton CV en PDF pour pouvoir préparer des candidatures.</div>`}
    </section>
    <section class="card pad stack">
      <h2>Compte</h2>
      <form id="pw" class="form two"><label class="f">Mot de passe actuel<input type="password" name="current" autocomplete="current-password" required></label>
        <label class="f">Nouveau mot de passe<input type="password" name="new" autocomplete="new-password" minlength="8" required></label>
        <div><button class="btn btn-ghost" type="submit">Changer le mot de passe</button></div></form>
      <div class="spread" style="border-top:1px solid var(--line);padding-top:16px"><span class="sub" style="font-size:14px">Supprimer ton compte efface ton profil, tes documents et ton historique.</span><button class="btn btn-danger" id="delAcc">Supprimer mon compte</button></div>
    </section>`);
  document.getElementById("prf").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/profile", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Profil enregistré.").then(async () => { ME = await api("/api/auth/me"); route(); }); };
  document.getElementById("importYaml").onclick = () => act(() => api("/api/profile/import-yaml", { method: "POST" })).then((r) => { toast("Importé : " + r.imported.join(", ") + "."); route(); });
  document.getElementById("up").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/documents", { method: "POST", body: new FormData(e.target) }), "Document importé.").then(route); };
  $app.querySelectorAll("[data-def]").forEach((b) => b.onclick = () => act(() => api(`/api/documents/${b.dataset.def}`, { method: "PATCH", body: { is_default: true } }), "Document par défaut mis à jour.").then(route));
  $app.querySelectorAll("[data-ren]").forEach((b) => b.onclick = () => { const d = docs.find((x) => x.id === b.dataset.ren); const n = prompt("Nouveau nom :", d.name); if (n) act(() => api(`/api/documents/${d.id}`, { method: "PATCH", body: { name: n } }), "Renommé.").then(route); });
  $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { if (confirm("Supprimer ce document ?")) act(() => api(`/api/documents/${b.dataset.del}`, { method: "DELETE" }), "Document supprimé.").then(route); });
  document.getElementById("pw").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/auth/password", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Mot de passe modifié.").then(() => e.target.reset()); };
  document.getElementById("delAcc").onclick = () => modal(`<h2>Supprimer mon compte</h2><p class="sub">Action définitive. Saisis ton mot de passe pour confirmer.</p>
    <form id="da" class="form"><input type="password" name="password" required autocomplete="current-password" aria-label="Mot de passe">
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn btn-ghost" data-close>Annuler</button><button class="btn btn-danger" type="submit">Supprimer définitivement</button></div></form>`,
    (el) => el.querySelector("#da").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/auth/account", { method: "DELETE", body: Object.fromEntries(new FormData(e.target)) }), "Compte supprimé.").then(() => { ME = null; el.remove(); go("#/register"); }); });
}

// ---------- routeur ----------
function schedulePoll(ms) {
  clearTimeout(pollTimer);
  if (ms) pollTimer = setTimeout(() => { if (!document.querySelector(".scrim") && !document.activeElement?.matches("input,textarea,select")) route(); else schedulePoll(ms); }, ms);
}

async function route() {
  clearTimeout(pollTimer);
  const h = location.hash || "#/";
  if (h === "#/login" || h === "#/register") return authView(h.slice(2));
  if (!ME) { try { ME = await api("/api/auth/me"); } catch { return go("#/login"); } }
  try {
    if (h !== "#/" && h !== "#/a-valider") navBadge = (await api("/api/applications?statut=a_valider")).length;
    const m = h.match(/^#\/candidature\/([\w-]+)$/);
    if (m) return await applicationView(m[1]);
    const views = { "#/": dashboardView, "#/a-valider": toValidateView, "#/candidatures": applicationsView, "#/offres": offersView, "#/criteres": prefsView, "#/profil": profileView };
    await (views[h] || dashboardView)();
  } catch (e) { if (ME) toast(e.message, true); }
}

window.addEventListener("hashchange", route);
route();
