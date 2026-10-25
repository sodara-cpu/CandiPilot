/* CandiPilot — interface (écrans de la maquette + CRUD), sans framework. */
"use strict";

const $app = document.getElementById("app");
let ME = null;              // utilisateur connecté
let pollTimer = null;
let pollMs = 0;
let GUIDE = null;            // parcours du candidat (/api/guide)

// Apparence : automatique (réglage de l'ordinateur), claire ou sombre — mémorisée sur cet appareil.
const getTheme = () => { try { return localStorage.getItem("cp_theme") || "auto"; } catch { return "auto"; } };
function setTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("cp_theme", t); } catch { /* navigation privée */ }
}
setTheme(getTheme());

// ---------- utilitaires ----------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, init);
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    ME = null;
    if (!/^#\/(login|register)/.test(location.hash)) sessionStorage.setItem("cp_next", location.hash || "#/");
    go("#/login"); throw new Error("Session expirée, reconnecte-toi.");
  }
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
const MODES = { auto: "Automatique", assisted: "Assisté", manual: "Semi-assisté" };

function appState(a) {
  if (a.statut === "a_valider") {
    if (a.mode === "manual") return `<span class="badge b-mute">Semi-assisté</span>`;
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
  doc: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
};
const svg = (name, size = 20, sw = 1.8) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name]}</svg>`;
const LOGO = `<span class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-8 6 8"/><circle cx="12" cy="19" r="1.2" fill="#fff"/></svg></span>`;

// ---------- squelette ----------
// Le menu suit les cinq étapes du parcours, cochées quand elles sont faites.
const STEP_NAV = [["#/profil", "profile", "user", "Profil"], ["#/documents", "documents", "doc", "CV et lettre"],
  ["#/criteres", "prefs", "sliders", "Critères et offres"], ["#/mode", "mode", "spark", "Mode de soumission"], ["#/envoi", "send", "check", "Valider l'envoi"]];
// Page arrivée en retard (l'utilisateur a déjà cliqué ailleurs) : on ne l'affiche pas.
class StalePage extends Error {}
const PAGE_OF = { "#/": "home", "#/profil": "profile", "#/documents": "documents", "#/criteres": "prefs", "#/mode": "mode",
  "#/envoi": "send", "#/a-valider": "send", "#/candidatures": "apps", "#/offres": "offers", "#/compte": "account" };
const currentPage = () => { const h = (location.hash || "#/").split("?")[0]; return h.startsWith("#/candidature/") ? "send" : PAGE_OF[h] || "home"; };

function shell(active, content) {
  if (active !== currentPage()) throw new StalePage();
  const name = `${ME.first_name || ""} ${ME.last_name ? ME.last_name[0] + "." : ""}`.trim() || ME.email;
  const link = (hash, key, icon, label, extra = "") =>
    `<a href="${hash}" ${active === key ? 'aria-current="page"' : ""}>${svg(icon)}<span class="grow">${label}</span>${extra}</a>`;
  const steps = GUIDE?.steps || [];
  $app.innerHTML = `
  <div class="shell">
    <nav class="nav" aria-label="Navigation principale">
      <div class="brand">${LOGO}<span>CandiPilot</span></div>
      ${link("#/", "home", "home", "Accueil")}
      <div class="navlabel">Mon parcours</div>
      ${STEP_NAV.map(([h, k, ic, l], i) => link(h, k, ic, `${i + 1}. ${l}`,
        steps[i]?.done ? `<span class="navtick" title="Étape faite">${svg("tick", 14, 2.4)}</span>` : GUIDE?.current === i ? '<span class="badge b-violet">À faire</span>' : "")).join("")}
      <div class="sep"></div>
      ${link("#/candidatures", "apps", "board", "Candidatures")}
      ${link("#/offres", "offers", "search", "Toutes les offres")}
      <div class="me">
        <span class="avatar">${esc(initials(name))}</span>
        <span style="display:flex;flex-direction:column;line-height:1.3;min-width:0;flex:1">
          <b style="font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis">${esc(name)}</b>
          <a href="#/compte" style="font-size:12px;color:var(--muted)">Mon compte</a>
        </span>
        <button class="btn btn-quiet btn-sm" id="logout" title="Se déconnecter" aria-label="Se déconnecter">${svg("out", 18)}</button>
      </div>
    </nav>
    <main>${active === "home" ? "" : guideBanner()}${content}</main>
  </div>`;
  document.getElementById("logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); ME = null; GUIDE = null; go("#/login"); };
}

// ---------- parcours guidé ----------
// Bandeau « prochaine étape » en haut de chaque page, carte complète sur l'accueil.
function guideCta(step) {
  if (step.waiting) return `<span class="badge b-blue"><span class="spin"></span>En cours</span>`;
  if (location.hash === step.href) return `<span class="badge b-violet">C'est ici ↓</span>`;
  return `<a class="btn btn-primary" href="${step.href}">Y aller →</a>`;
}

const subList = (subs) => subs?.length ? `<ul class="subs">${subs.map((x) =>
  `<li class="${x.done ? "done" : ""}"><span>${x.done ? "✓" : "○"}</span>${esc(x.label)}${x.detail ? ` <span class="sub">· ${esc(x.detail)}</span>` : ""}</li>`).join("")}</ul>` : "";

function guideBanner() {
  if (!GUIDE) return "";
  const g = GUIDE, cur = g.current !== null ? g.steps[g.current] : g.next;
  if (!cur) return "";
  const head = g.current !== null ? `Étape ${g.current + 1} sur ${g.steps.length}` : "Prochaine action";
  return `<section class="guide" aria-label="Prochaine étape">
    <span class="gnum">${g.current !== null ? g.current + 1 : "→"}</span>
    <div class="grow"><div class="ghead">${head}</div><b>${esc(cur.label)}</b><div class="ghelp">${esc(cur.help)}</div>${subList(cur.subs)}</div>
    ${guideCta(cur)}</section>`;
}

function guideChecklist() {
  if (!GUIDE) return "";
  const g = GUIDE, done = g.steps.filter((s) => s.done).length;
  return `<section class="card pad stack" id="parcours">
    <div class="spread"><div><h2>${g.current === null ? "Ton parcours est terminé" : "Ton parcours en 5 étapes"}</h2><div class="sub" style="font-size:14px">Suis-les dans l'ordre : à chaque étape, CandiPilot te dit exactement quoi faire.</div></div>
      <span class="badge b-violet">${done} / ${g.steps.length} faites</span></div>
    <div class="progress"><i style="width:${(done / g.steps.length) * 100}%"></i></div>
    <ol class="steps">${g.steps.map((s, i) => `
      <li class="${s.done ? "done" : i === g.current ? "now" : ""}">
        <span class="gnum">${s.done ? svg("tick", 14, 2.4) : i + 1}</span>
        <div class="grow"><b>${esc(s.label)}</b>${i === g.current ? `<div class="ghelp">${esc(s.help)}</div>${subList(s.subs)}` : ""}</div>
        ${i === g.current ? guideCta(s) : s.done ? `<a class="btn btn-quiet btn-sm" href="${s.href}">Modifier</a>` : ""}
      </li>`).join("")}</ol>
    ${g.current === null && g.next ? guideBanner() : ""}
  </section>`;
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
const pwField = (name, label, auto, hint = "") => `<label class="f">${label}
  <span class="pw"><input type="password" name="${name}" autocomplete="${auto}" required ${auto === "new-password" ? 'minlength="8"' : ""}>
  <button type="button" class="btn btn-quiet btn-sm" data-show aria-label="Afficher le mot de passe">${svg("eye", 18)}</button></span>${hint}</label>`;
const PW_HINT = '<span class="hint">8 caractères minimum, avec au moins une lettre et un chiffre.</span>';

function authCard(title, subtitle, form, footer) {
  $app.innerHTML = `
  <div class="auth"><div class="card pop">
    <div class="row">${LOGO}<b style="font-size:19px;letter-spacing:-.03em">CandiPilot</b></div>
    <h1>${title}</h1>
    <p class="sub" style="margin:0 0 20px">${subtitle}</p>
    <form class="form" id="authForm" novalidate>${form}</form>
    <p class="sub" style="font-size:14px;margin:18px 0 0">${footer}</p>
  </div></div>`;
  $app.querySelectorAll("[data-show]").forEach((b) => b.onclick = () => {
    const i = b.previousElementSibling; const show = i.type === "password";
    i.type = show ? "text" : "password"; b.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
  });
  return document.getElementById("authForm");
}

function afterLogin() {
  const next = sessionStorage.getItem("cp_next"); sessionStorage.removeItem("cp_next");
  go(next && !/^#\/(login|register|mot-de-passe|reinitialiser)/.test(next) ? next : "#/");
}

function submitWith(form, fn) {
  form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
    try { await fn(Object.fromEntries(new FormData(form))); } catch { /* message déjà affiché */ } finally { btn.disabled = false; }
  };
}

function authView(mode) {
  const reg = mode === "register";
  const form = authCard(reg ? "Créer un compte" : "Connexion",
    reg ? "Ton copilote de candidatures, en quelques minutes." : "Content de te revoir.",
    `${reg ? `<div class="form two"><label class="f">Prénom<input type="text" name="first_name" autocomplete="given-name" required></label>
               <label class="f">Nom<input type="text" name="last_name" autocomplete="family-name" required></label></div>` : ""}
      <label class="f">E-mail<input type="email" name="email" autocomplete="email" required></label>
      ${pwField("password", "Mot de passe", reg ? "new-password" : "current-password", reg ? PW_HINT : "")}
      ${reg ? pwField("confirm", "Confirme le mot de passe", "new-password") : `<a href="#/mot-de-passe-oublie" style="font-size:14px;align-self:flex-end">Mot de passe oublié ?</a>`}
      <button class="btn btn-primary" type="submit">${reg ? "Créer mon compte" : "Se connecter"}</button>`,
    reg ? 'Déjà inscrit ? <a href="#/login">Se connecter</a>' : 'Pas encore de compte ? <a href="#/register">Créer un compte</a>');
  submitWith(form, async (body) => {
    if (reg && body.password !== body.confirm) { toast("Les deux mots de passe ne sont pas identiques.", true); throw new Error(); }
    delete body.confirm;
    await act(() => api(`/api/auth/${reg ? "register" : "login"}`, { method: "POST", body }));
    ME = await api("/api/auth/me");
    if (reg) go("#/"); else afterLogin();
  });
}

function forgotView() {
  const form = authCard("Mot de passe oublié", "Indique ton adresse : tu recevras un lien pour choisir un nouveau mot de passe.",
    `<label class="f">E-mail<input type="email" name="email" autocomplete="email" required></label>
     <button class="btn btn-primary" type="submit">Recevoir le lien</button>`,
    '<a href="#/login">Retour à la connexion</a>');
  submitWith(form, async (body) => {
    await act(() => api("/api/auth/forgot", { method: "POST", body }));
    const email = body.email.trim().toLowerCase();
    document.querySelector(".auth h1").textContent = "Vérifie ta boîte mail";
    document.querySelector(".auth h1 + .sub").remove();
    form.outerHTML = `<div class="sent" role="status">
      <span class="sent-icon" aria-hidden="true">✉</span>
      <p>Si un compte existe pour <b>${esc(email)}</b>, tu vas recevoir un lien pour choisir un nouveau mot de passe.</p>
      <p class="hint">Le lien est valable 1 heure. Rien reçu ? Regarde dans tes courriers indésirables.</p>
      <button class="btn btn-ghost" type="button" id="resend">Renvoyer le lien</button>
    </div>`;
    document.getElementById("resend").onclick = (e) => act(() => api("/api/auth/forgot", { method: "POST", body: { email } }), "Lien renvoyé.")
      .then(() => { e.target.disabled = true; e.target.textContent = "Lien renvoyé"; });
  });
}

function resetView() {
  const token = new URLSearchParams(location.hash.split("?")[1] || "").get("token") || "";
  const form = authCard("Nouveau mot de passe", "Choisis ton nouveau mot de passe. Tes autres appareils seront déconnectés.",
    `${pwField("password", "Nouveau mot de passe", "new-password", PW_HINT)}${pwField("confirm", "Confirme le mot de passe", "new-password")}
     <button class="btn btn-primary" type="submit">Enregistrer et me connecter</button>`,
    '<a href="#/mot-de-passe-oublie">Demander un nouveau lien</a>');
  submitWith(form, async (body) => {
    if (body.password !== body.confirm) { toast("Les deux mots de passe ne sont pas identiques.", true); throw new Error(); }
    await act(() => api("/api/auth/reset", { method: "POST", body: { token, password: body.password } }), "Mot de passe modifié.");
    ME = await api("/api/auth/me"); history.replaceState(null, "", "#/"); route();
  });
}

// ---------- accueil ----------
async function dashboardView() {
  const d = await api("/api/dashboard");
  const cp = d.copilot, n = d.stats.counts || {};
  const running = cp.jobs.find((j) => j.statut === "en_cours" || j.statut === "en_attente");
  const JOBN = { search: "Recherche des offres", prepare: "Préparation d'un dossier", submit: "Envoi d'une candidature", verify: "Vérification d'un envoi", check_sites: "Vérification de tes sites" };
  const sent = n.confirmee || 0, inProgress = (n.preparation || 0) + (n.soumission || 0) + (n.incertaine || 0),
    action = (n.a_valider || 0) + (n.echec || 0);
  const tile = (href, label, value, hint, cls = "") => `<a class="card pad tile ${cls}" href="${href}">
      <span class="label">${label}</span><span class="value">${value}</span><span class="note">${hint}</span></a>`;
  // « À faire » : uniquement ce qui attend une action de l'utilisateur.
  const todo = [];
  d.to_validate.slice(0, 4).forEach((a) => todo.push(`<a class="item" href="#/candidature/${a.id}">${mono(a.entreprise)}<span class="grow"><div class="t">${esc(a.poste)}</div><div class="s">${esc(a.entreprise)} · dossier prêt</div></span>${appState(a)}${svg("chev", 18)}</a>`));
  if (n.echec) todo.push(`<a class="item" href="#/envoi"><span class="dot-ic err">!</span><span class="grow"><div class="t">${n.echec} candidature${n.echec > 1 ? "s n'ont" : " n'a"} pas pu partir</div><div class="s">Voir la raison et reprendre</div></span>${svg("chev", 18)}</a>`);
  if (n.incertaine) todo.push(`<a class="item" href="#/envoi"><span class="dot-ic warn">?</span><span class="grow"><div class="t">${n.incertaine} envoi${n.incertaine > 1 ? "s" : ""} à vérifier</div><div class="s">La confirmation n'a pas été reçue</div></span>${svg("chev", 18)}</a>`);
  if (GUIDE?.current === null && GUIDE.next && !todo.length) todo.push(`<a class="item" href="${GUIDE.next.href}"><span class="dot-ic">→</span><span class="grow"><div class="t">${esc(GUIDE.next.label)}</div><div class="s">${esc(GUIDE.next.help)}</div></span>${svg("chev", 18)}</a>`);
  shell("home", `
    <header class="head">
      <div class="titles"><h1>Bonjour ${esc(ME.first_name || "")} 👋</h1>
        <p class="sub" style="margin:4px 0 0">${GUIDE?.current === null ? "Ton copilote continue de chercher les meilleures opportunités pour toi." : "Encore quelques étapes et ton copilote se met au travail."}</p></div>
      <button class="bell" id="bell" aria-label="Notifications">${svg("bell", 20)}${d.unread ? '<span class="pip"></span>' : ""}</button>
    </header>
    ${GUIDE?.current !== null ? guideChecklist() : ""}
    ${cp.fake ? `<div class="banner"><span class="badge b-amber">Robot simulé</span> Mode démonstration : aucune candidature réelle n'est envoyée.</div>` : ""}
    <section class="grid g3" aria-label="Mes candidatures">
      ${tile("#/candidatures", "Envoyées", sent, "avec preuve de réception")}
      ${tile("#/envoi", "En cours", inProgress, inProgress ? "le robot travaille" : "rien en cours")}
      ${tile("#/envoi", "Action requise", action, action ? "à toi de jouer →" : "rien à faire", action ? "hero" : "")}
    </section>
    <section class="grid g2">
      <div class="card pad">
        <div class="spread" style="margin-bottom:8px"><h2>À faire</h2>${todo.length ? `<span class="badge b-violet">${todo.length}</span>` : ""}</div>
        ${todo.length ? todo.join("") : `<div class="empty">Rien à faire pour l'instant : ton copilote s'occupe du reste.</div>`}
      </div>
      <div class="card pad copilot stack">
        <div class="spread"><div><h2>Ton copilote</h2><div class="sub" style="font-size:13px">${cp.last_search_at ? "Dernière recherche " + ago(cp.last_search_at) : "Pas encore de recherche"}</div></div>
          <span class="badge" style="background:${cp.paused ? "rgba(255,255,255,.12)" : "rgba(18,184,154,.18)"};color:${cp.paused ? "#fff" : "#5EE6C8"}"><span class="dot" style="background:${cp.paused ? "#aaa" : "#12B89A"}"></span>${cp.paused ? "En pause" : "Actif"}</span></div>
        <div>
          ${running ? `<div class="task"><span class="tick"><span class="spin"></span></span>${esc(JOBN[running.type] || running.type)}<span class="meta">${esc(running.progress || (running.statut === "en_attente" ? "En file" : "En cours"))}</span></div>` : ""}
          <div class="task"><span class="tick">${svg("tick", 14, 2.2)}</span>Recherche automatique<span class="meta">${cp.paused ? "Suspendue" : "Toutes les " + cp.every_hours + " h"}</span></div>
        </div>
        <div class="row" style="flex-wrap:wrap;margin-top:auto">
          <button class="btn btn-ghost" id="pause">${cp.paused ? "Reprendre" : "Mettre en pause"}</button>
          <button class="btn btn-ghost" id="searchNow">Lancer une recherche</button>
          <a class="btn btn-ghost" href="#/criteres">Modifier les critères</a>
        </div>
      </div>
    </section>
    <section class="stack">
      <div class="spread"><h2 style="font-size:19px">Offres pertinentes</h2><a href="#/offres" style="font-weight:600;font-size:14px">Toutes les offres</a></div>
      ${d.recommended.length ? `<div class="grid g3">${d.recommended.map((a) => `
          <div class="card pad stack">
            <div class="spread">${mono(a.entreprise, 44)}<span class="badge b-violet">${a.score} %</span></div>
            <div><div style="font-weight:600;font-size:17px">${esc(a.poste)}</div><div class="sub">${esc(a.entreprise)}</div></div>
            <div class="chips">${a.localisation ? `<span class="badge b-mute">${esc(a.localisation)}</span>` : ""}${a.contrat ? `<span class="badge b-mute">${esc(a.contrat)}</span>` : ""}${a.start_date ? `<span class="badge b-mute">${esc(monthLabel(a.start_date))}</span>` : ""}</div>
            <button class="btn btn-ghost" data-select="${a.id}">Sélectionner cette offre</button>
          </div>`).join("")}</div>` : `<div class="card empty">Aucune offre pour l'instant. Renseigne tes critères puis lance une recherche.</div>`}
    </section>
    <section class="card pad">
      <div class="spread" style="margin-bottom:8px"><h2>Activité récente</h2><button class="btn btn-quiet btn-sm" id="allEvents">Tout voir</button></div>
      ${d.events.length ? d.events.slice(0, 5).map(eventRow).join("") : `<div class="empty">Rien pour le moment.</div>`}
    </section>`);
  document.getElementById("pause").onclick = () => act(() => api(`/api/copilot/${cp.paused ? "resume" : "pause"}`, { method: "POST" })).then(route);
  document.getElementById("searchNow").onclick = () => act(() => api("/api/search", { method: "POST" }), "Recherche lancée.").then(route);
  document.getElementById("bell").onclick = document.getElementById("allEvents").onclick = showEvents;
  $app.querySelectorAll("[data-select]").forEach((b) => b.onclick = () => selectOffer(b.dataset.select, true));
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

// Sélectionner une offre lance sa préparation technique (lettre, lecture du formulaire) : aucun envoi.
const selectOffer = (id, on) => act(() => api(`/api/applications/${id}/select`, { method: "POST", body: { selected: on } }),
  on ? "Offre sélectionnée : le robot prépare le dossier (rien n'est envoyé)." : "Offre retirée de la sélection.").then(route);

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

  let banner = "";
  if (a.statut === "confirmee") banner = `<section class="card pad pop" style="background:var(--green-bg);border-color:var(--line-2)"><b style="color:var(--green)">Candidature envoyée</b><div style="font-size:14px;color:var(--green)">Preuve : ${esc(a.preuve)} · ${a.date_envoi ? new Date(a.date_envoi).toLocaleString("fr-FR") : ""}</div></section>`;
  else if (a.statut === "soumission") banner = `<section class="card pad pop" style="background:var(--blue-bg);border-color:var(--line-2)"><b style="color:var(--blue)"><span class="spin"></span> Envoi en cours</b><div style="font-size:14px;color:var(--blue)">${a.mode === "assisted" ? "Le robot a ouvert le formulaire : vérifie-le et clique « Postuler maintenant » dans sa fenêtre." : "Le robot remplit et envoie le formulaire."} Elle passera en « Envoyée » seulement quand JobTeaser confirmera la réception.</div></section>`;
  else if (a.statut === "preparation") banner = `<section class="card pad" style="background:var(--blue-bg);border-color:var(--line-2)"><b style="color:var(--blue)"><span class="spin"></span> Préparation en cours</b><div style="font-size:14px;color:var(--blue)">${esc(a.job?.progress || "Le robot rédige la lettre et lit le formulaire de candidature.")}</div></section>`;
  else if (a.statut === "echec") banner = `<section class="card pad" style="background:var(--red-bg);border-color:var(--line-2)"><b style="color:var(--red)">Échec</b><div style="font-size:14px;color:var(--red)">${esc(a.detail)}</div></section>`;
  else if (a.statut === "incertaine") banner = `<section class="card pad" style="background:var(--amber-bg);border-color:var(--line-2)"><b style="color:var(--amber)">Envoi incertain</b><div style="font-size:14px;color:var(--amber)">${esc(a.detail)} — lance une vérification ou marque-la comme envoyée si tu as la confirmation.</div></section>`;

  shell("send", `
    <a href="#/envoi" class="btn btn-quiet" style="align-self:flex-start;padding:0 8px">${svg("back", 16, 2)}Récapitulatif d'envoi</a>
    <header class="head">
      ${mono(a.entreprise, 60)}
      <div class="titles"><h1 style="font-size:30px">${esc(a.poste)}</h1>
        <span class="sub">${esc(a.entreprise)}${a.localisation ? " · " + esc(a.localisation) : ""}${a.start_date ? " · " + esc(monthLabel(a.start_date)) : ""} · ${isJT ? "JobTeaser" : "Site carrière"} · <a href="${esc(a.url)}" target="_blank" rel="noopener">Voir l'offre</a></span></div>
      <span class="badge b-violet" style="font-size:14px;padding:6px 14px">${a.score} % de compatibilité</span>
    </header>
    ${a.match_reason ? `<p class="why why-lg">${svg("spark", 15, 2)}${esc(a.match_reason)}</p>` : ""}
    ${banner}
    <section class="modes" aria-label="Mode d'envoi">
      ${[["auto", "Automatique", isJT ? "Le robot remplit et envoie, puis vérifie la preuve de réception." : "Indisponible : candidature sur le site de l'entreprise."],
         ["assisted", "Assisté", isJT ? "Le robot pré-remplit, tu cliques sur « Postuler maintenant »." : "Indisponible : candidature sur le site de l'entreprise."],
         ["manual", "Semi-assisté", "Dossier prêt à télécharger, tu postules toi-même."]].map(([k, t, d]) =>
        `<button class="mode" data-mode="${k}" aria-pressed="${a.mode === k}" ${!editable || a.launched || (k !== "manual" && !isJT) ? "disabled" : ""}><b>${a.mode === k ? "● " : ""}${t}</b><span>${d}</span></button>`).join("")}
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
              <span class="sub" style="font-size:13px">${a.letter_kind === "document" ? "Ta lettre importée" : a.letter_kind === "generated" ? "Lettre adaptée à l'offre" : ""}</span>
              <span class="row" style="gap:12px;flex-wrap:wrap">${a.letter_path ? `<a href="/api/applications/${a.id}/letter" target="_blank" rel="noopener">Prévisualiser</a>` : ""}${editable && a.letter_text ? `<a href="#" id="editLetter">Modifier le texte</a>` : ""}
                ${editable && !a.launched && a.letter_kind === "document" ? `<a href="#" data-letter="generated">Générer une lettre adaptée</a>` : ""}
                ${editable && !a.launched && a.letter_kind === "generated" && a.imported_letter ? `<a href="#" data-letter="document">Utiliser ma lettre importée</a>` : ""}</span>
            </div></div>
        </section>
        <section class="card pad">
          <div class="spread" style="margin-bottom:6px"><h2>Réponses préparées</h2><span class="badge b-green">${prepared.filter((p) => p[1]).length} prêtes</span></div>
          <div class="answers">${prepared.map(([k, v]) => `<div class="a"><span style="color:${v ? "#12B89A" : "#C3C8D4"}">${svg("tick", 18, 2.2)}</span><div><div class="k">${k}</div><div class="v">${v ? esc(v) : '<span class="sub" style="font-weight:400">Non renseigné — <a href="#/profil">compléter le profil</a></span>'}</div></div></div>`).join("")}</div>
        </section>
        ${a.questions.length ? `
        <section class="card pad warn stack">
          <div class="spread"><h2>Ton intervention est nécessaire</h2><span class="badge b-amber">${openQ.length} question${openQ.length > 1 ? "s" : ""} ouverte${openQ.length > 1 ? "s" : ""}</span></div>
          <p style="margin:0;font-size:14px">Le recruteur pose ${a.questions.length > 1 ? "ces questions" : "cette question"}. CandiPilot ne répond pas à ta place aux questions personnelles ou de motivation.</p>
          <form id="qForm" class="stack">${a.questions.map((q, i) => `<label class="f">${esc(q.label)}<textarea name="q${i}" maxlength="1500" placeholder="Ta réponse, en quelques phrases…" ${editable ? "" : "disabled"}>${esc(q.answer || "")}</textarea></label>`).join("")}
            ${editable ? `<button class="btn btn-ghost" type="submit" style="align-self:flex-start">Enregistrer mes réponses</button>` : ""}</form>
        </section>` : ""}
        ${a.detail && !["echec", "incertaine"].includes(a.statut) ? `<p class="hint" style="margin:0">Robot : ${esc(a.detail)}</p>` : ""}
        <details class="adv" id="hist"><summary>Historique de cette candidature</summary><div id="histBody" class="hint" style="margin-top:8px">Chargement…</div></details>
      </div>
      <aside class="card pad stack" style="position:sticky;top:20px">
        <h2>Ta décision</h2>
        <div class="spread" style="font-size:14px"><span class="sub">Dossier prêt</span><b>${ready} / ${a.readiness.length}</b></div>
        <div class="progress"><i style="width:${(ready / a.readiness.length) * 100}%"></i></div>
        <ul style="list-style:none;margin:0;padding:0;font-size:13px;display:grid;gap:4px">${a.readiness.map((r) => `<li style="color:${r.ok ? "var(--green)" : "var(--muted)"}">${r.ok ? "✓" : "○"} ${esc(r.label)}</li>`).join("")}</ul>
        ${a.selected && !a.launched && a.blockers.length ? `<div class="stack" style="gap:6px">${a.blockers.map((b) => `<div class="blk${b.wait ? " wait" : ""}">${b.wait ? '<span class="spin"></span>' : "!"} ${esc(b.label)}</div>`).join("")}</div>` : ""}
        ${a.warnings.map((w) => `<div class="hint">ⓘ ${esc(w)}</div>`).join("")}
        ${actionsFor(a, busy)}
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
  const put = (body, msg) => act(() => api(`/api/applications/${id}`, { method: "PUT", body }), msg).then(route);
  document.getElementById("hist").addEventListener("toggle", async (e) => {
    if (!e.target.open) return;
    const h = await api(`/api/applications/${id}/history`).catch(() => []);
    document.getElementById("histBody").innerHTML = h.length ? `<ol class="timeline">${h.map((x) => `<li><span class="sub">${new Date(x.created_at).toLocaleString("fr-FR")}</span>
      ${x.action === "statut" ? `${statusBadge(x.from_status)} → ${statusBadge(x.to_status)}` : `<b>${esc(x.action)}</b>`} ${x.detail ? `<span class="sub">— ${esc(x.detail)}</span>` : ""}</li>`).join("")}</ol>` : "Aucun événement pour l'instant.";
  });
  on("doSelect", () => selectOffer(id, true));
  on("doUnselect", () => selectOffer(id, false));
  on("doCustomized", () => put({ customized: true }, "Dossier vérifié."));
  on("doReviewed", () => put({ reviewed: true }, "Envoi autorisé : il partira au lancement final."));
  on("doUnreview", () => put({ reviewed: false }, "Autorisation retirée."));
  on("doPrepare", () => act(() => api(`/api/applications/${id}/prepare`, { method: "POST" }), "Préparation lancée.").then(route));
  $app.querySelectorAll("[data-letter]").forEach((l) => l.onclick = (e) => { e.preventDefault();
    put({ letter_kind: l.dataset.letter }, l.dataset.letter === "generated" ? "Lettre adaptée générée." : "Ta lettre importée sera utilisée."); });
  on("doIgnore", () => act(() => api(`/api/applications/${id}/ignore`, { method: "POST" }), "Offre ignorée.").then(() => go("#/envoi")));
  on("doRestore", () => act(() => api(`/api/applications/${id}/restore`, { method: "POST" }), "Offre restaurée.").then(route));
  on("doRetry", () => act(() => api(`/api/applications/${id}/retry`, { method: "POST" }), "Dossier remis dans le récapitulatif d'envoi.").then(route));
  on("doVerify", () => act(() => api(`/api/applications/${id}/verify`, { method: "POST" }), "Vérification lancée.").then(route));
  on("doMarkSent", () => { if (confirm("Confirmes-tu avoir envoyé cette candidature toi-même ?")) act(() => api(`/api/applications/${id}/mark-sent`, { method: "POST" }), "Candidature marquée comme envoyée.").then(route); });
  on("doModify", () => document.querySelector("#qForm textarea, #cvSel, [data-mode]")?.focus());
  schedulePoll(["preparation", "soumission"].includes(a.statut) || busy ? 3000 : 0);
}

function actionsFor(a, busy) {
  const s = a.statut, f = a.flow;
  const toRecap = `<a class="btn btn-ghost" href="#/envoi">Aller au récapitulatif d'envoi</a>`;
  if (s === "ignoree") return `<button class="btn btn-ghost" id="doRestore">Restaurer l'offre</button>`;
  if (s === "preparation" || busy) return `<button class="btn btn-ai" disabled><span class="spin"></span> Robot au travail…</button>`;
  if (["decouverte", "qualifiee", "a_valider"].includes(s) && !a.selected)
    return `<button class="btn btn-ai" id="doSelect">Sélectionner pour l'envoi</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  if (["decouverte", "qualifiee"].includes(s)) return `<button class="btn btn-ai" id="doPrepare">Préparer le dossier</button><button class="btn btn-quiet" id="doUnselect">Retirer de la sélection</button>`;
  if (s === "a_valider" && a.launched && a.mode === "manual") return `
    <a class="btn btn-ghost" href="${esc(a.url)}" target="_blank" rel="noopener">Ouvrir le site de l'offre</a>
    ${a.letter_path ? `<a class="btn btn-ghost" href="/api/applications/${a.id}/letter" target="_blank" rel="noopener">Télécharger la lettre</a>` : ""}
    ${a.cv ? `<a class="btn btn-ghost" href="/api/documents/${a.cv.id}/file" target="_blank" rel="noopener">Télécharger le CV</a>` : ""}
    <button class="btn btn-primary" id="doMarkSent">Je l'ai envoyée</button>`;
  if (s === "a_valider") return `
    ${f.prepare ? (a.customized ? '<div class="blk ok">✓ Dossier vérifié</div>' : `<button class="btn btn-ai" id="doCustomized">J'ai vérifié ce dossier</button>`) : ""}
    ${f.review ? (a.reviewed ? `<div class="blk ok">✓ Envoi autorisé</div><button class="btn btn-quiet btn-sm" id="doUnreview">Retirer l'autorisation</button>` : `<button class="btn btn-primary" id="doReviewed">Autoriser l'envoi</button>`) : ""}
    ${toRecap}
    <div class="row"><button class="btn btn-quiet" style="flex:1" id="doUnselect">Retirer de la sélection</button><button class="btn btn-quiet" style="flex:1" id="doPrepare">Relire le formulaire</button></div>`;
  if (s === "echec") return `<button class="btn btn-primary" id="doRetry">Reprendre</button>${a.apply_type === "jobteaser" ? '<button class="btn btn-ghost" id="doVerify">Vérifier sur JobTeaser</button>' : ""}<a class="btn btn-ghost" href="${esc(a.url)}" target="_blank" rel="noopener">Ouvrir l'offre</a><button class="btn btn-ghost" id="doMarkSent">Je l'ai envoyée moi-même</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  if (s === "incertaine") return `<button class="btn btn-primary" id="doVerify">Vérifier sur JobTeaser</button><button class="btn btn-ghost" id="doMarkSent">Je l'ai envoyée</button><button class="btn btn-quiet" id="doIgnore">Ignorer</button>`;
  return "";
}

function statusHelp(a, openQ) {
  if (a.statut === "a_valider" && !a.launched) {
    if (!a.selected) return "Sélectionne cette offre pour l'inclure dans ton prochain lancement.";
    return "Rien ne part d'ici : l'envoi se déclenche uniquement avec « Valider et démarrer le robot » dans le récapitulatif.";
  }
  if (a.statut === "a_valider" && a.mode === "manual") return "Postule sur le site avec ce CV et cette lettre, puis clique « Je l'ai envoyée ».";
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
      <a class="btn btn-primary" href="#/envoi">Valider l'envoi</a></header>
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

// ---------- liste d'offres : recherche, filtres, tri, pages de 10 ----------
const PAGE_SIZE = 10;
const OB_FILTERS = [["all", "Toutes"], ["selected", "Sélectionnées"], ["todo", "À sélectionner"], ["easy", "Candidature simplifiée"], ["top", "Score ≥ 70 %"]];
const OB_SORTS = [["score", "Meilleur score"], ["recent", "Plus récentes"], ["start", "Début le plus proche"], ["company", "Entreprise A → Z"]];
const scoreBadge = (o) => `<span class="badge ${o.score >= 85 ? "b-green" : o.score >= 50 ? "b-violet" : "b-mute"}"${o.match_reason ? ` title="${esc(o.match_reason)}"` : ""}>${o.score} %</span>`;
// Explication de la note par l'IA (lot 3), sous l'intitulé de l'offre.
const reasonLine = (o) => o.match_reason ? `<div class="why">${svg("spark", 13, 2)}${esc(o.match_reason)}</div>` : "";

function obState(key) {
  const base = { q: "", f: "all", sort: "score", page: 1 };
  try { return { ...base, ...JSON.parse(sessionStorage.getItem("ob_" + key) || "{}") }; } catch { return base; }
}

function offerBrowser(key) {
  return `<div class="ob" id="ob_${key}">
    <div class="ob-tools">
      <input type="search" class="ob-q" placeholder="Rechercher un poste, une entreprise, un lieu…" aria-label="Rechercher dans les offres">
      <select class="ob-sort" aria-label="Trier les offres">${OB_SORTS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>
    </div>
    <div class="ob-f" role="group" aria-label="Filtrer les offres">${OB_FILTERS.map(([k, l]) => `<button type="button" class="pill" data-f="${k}">${l}</button>`).join("")}</div>
    <div class="ob-body"></div><nav class="ob-pager" aria-label="Pages d'offres"></nav></div>`;
}

function bindOfferBrowser(key, offers, { head, row, empty, after }) {
  const root = document.getElementById("ob_" + key);
  if (!root) return;
  const st = obState(key), q = root.querySelector(".ob-q"), sort = root.querySelector(".ob-sort");
  q.value = st.q; sort.value = st.sort;
  const by = { score: (a, b) => b.score - a.score, recent: (a, b) => (b.created_at || "").localeCompare(a.created_at || ""),
    start: (a, b) => (a.start_date || "9999").localeCompare(b.start_date || "9999"), company: (a, b) => a.entreprise.localeCompare(b.entreprise, "fr") };
  const keep = { selected: (o) => o.selected, todo: (o) => !o.selected, easy: (o) => o.apply_type === "jobteaser", top: (o) => o.score >= 70 };
  const draw = () => {
    try { sessionStorage.setItem("ob_" + key, JSON.stringify(st)); } catch { /* navigation privée */ }
    root.querySelectorAll("[data-f]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === st.f)));
    const words = st.q.toLowerCase().split(/\s+/).filter(Boolean);
    const list = offers.filter((o) => words.every((w) => `${o.poste} ${o.entreprise} ${o.localisation} ${o.contrat}`.toLowerCase().includes(w)))
      .filter(keep[st.f] || (() => true)).sort(by[st.sort] || by.score);
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    st.page = Math.min(Math.max(1, st.page), pages);
    const shown = list.slice((st.page - 1) * PAGE_SIZE, st.page * PAGE_SIZE);
    root.querySelector(".ob-body").innerHTML = shown.length
      ? `<div class="hint ob-count">${list.length} offre${list.length > 1 ? "s" : ""}${list.length !== offers.length ? ` sur ${offers.length}` : ""}</div>
         <div class="card tablewrap"><table><thead><tr>${head}</tr></thead><tbody>${shown.map(row).join("")}</tbody></table></div>`
      : `<div class="empty">${offers.length ? "Aucune offre ne correspond à ces filtres." : empty}</div>`;
    root.querySelector(".ob-pager").innerHTML = pages > 1 ? `<button class="btn btn-ghost btn-sm" data-p="${st.page - 1}" ${st.page === 1 ? "disabled" : ""}>← Précédentes</button>
      <span class="hint">Page ${st.page} sur ${pages}</span><button class="btn btn-ghost btn-sm" data-p="${st.page + 1}" ${st.page === pages ? "disabled" : ""}>Suivantes →</button>` : "";
    after?.(root);
  };
  let t;
  q.oninput = () => { clearTimeout(t); t = setTimeout(() => { st.q = q.value; st.page = 1; draw(); }, 200); };
  sort.onchange = () => { st.sort = sort.value; st.page = 1; draw(); };
  root.querySelector(".ob-f").onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) { st.f = b.dataset.f; st.page = 1; draw(); } };
  root.querySelector(".ob-pager").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b && !b.disabled) { st.page = +b.dataset.p; draw(); root.scrollIntoView({ block: "start", behavior: "smooth" }); } };
  draw();
}

// ---------- offres (CRUD) ----------
async function offersView() {
  const offers = await api("/api/offers");
  shell("offers", `
    <header class="head"><div class="titles"><h1>Offres</h1><p class="sub" style="margin:4px 0 0">Toutes les offres trouvées pour toi, classées par compatibilité avec ton profil.</p></div>
      <button class="btn btn-ghost" id="searchNow">Lancer une recherche</button><button class="btn btn-primary" id="addOffer">Ajouter une offre</button></header>
    ${offerBrowser("all")}`);
  bindOfferBrowser("all", offers, {
    head: "<th>Poste</th><th>Entreprise</th><th>Lieu</th><th>Début</th><th>Match</th><th>Statut</th><th></th>",
    empty: "Aucune offre. Lance une recherche ou ajoute une offre à la main.",
    row: (o) => `<tr><td><a href="${esc(o.url)}" target="_blank" rel="noopener" style="font-weight:600;color:var(--ink)">${esc(o.poste)}</a><div class="hint">${o.apply_type === "jobteaser" ? "Candidature simplifiée" : "Site de l'entreprise"} · ${esc(o.source)}</div>${reasonLine(o)}</td>
        <td>${esc(o.entreprise)}</td><td class="sub">${esc(o.localisation)}</td><td class="sub">${esc(monthLabel(o.start_date))}</td>
        <td>${scoreBadge(o)}</td><td>${o.statut ? statusBadge(o.statut) : ""}</td>
        <td style="white-space:nowrap">${selectBtn(o)}${o.application_id ? `<a class="btn btn-quiet btn-sm" href="#/candidature/${o.application_id}">Ouvrir</a>` : ""}<button class="btn btn-quiet btn-sm" data-edit="${o.id}">Modifier</button>
          ${["soumission", "confirmee"].includes(o.statut) ? "" : `<button class="btn btn-quiet btn-sm" data-del="${o.id}" style="color:var(--red)">Supprimer</button>`}</td></tr>`,
    after: () => {
      bindSelect();
      $app.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => offerForm(offers.find((o) => o.id === b.dataset.edit)));
      $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { if (confirm("Supprimer cette offre ?")) act(() => api(`/api/offers/${b.dataset.del}`, { method: "DELETE" }), "Offre supprimée.").then(route); });
    },
  });
  document.getElementById("addOffer").onclick = () => offerForm();
  document.getElementById("searchNow").onclick = () => act(() => api("/api/search", { method: "POST" }), "Recherche lancée : les offres arrivent dans quelques minutes.").then(route);
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

// ---------- sélection d'offres (étape 3) ----------
const selectBtn = (o) => !o.application_id || o.launched || ["soumission", "confirmee"].includes(o.statut) ? ""
  : o.selected ? `<button class="btn btn-ghost btn-sm" data-sel="${o.application_id}" data-on="0" aria-pressed="true">✓ Sélectionnée</button>`
  : `<button class="btn btn-ai btn-sm" data-sel="${o.application_id}" data-on="1">Sélectionner</button>`;
const bindSelect = () => $app.querySelectorAll("[data-sel]").forEach((b) => b.onclick = () => selectOffer(b.dataset.sel, b.dataset.on === "1"));

// ---------- étape 3 : critères et offres ----------
const CONTRACTS = [["stage", "Stage"], ["alternance", "Alternance"], ["cdd", "CDD"], ["cdi", "CDI"], ["vie", "VIE"], ["graduate", "Graduate programme"]];
const SUGGEST = {
  metiers: ["M&A", "Private equity", "Private debt", "Corporate finance", "Analyste financier", "Asset management", "Transaction services",
    "Contrôle de gestion", "Audit", "Risk management", "Trading", "Structured finance", "Leveraged finance", "Equity research",
    "Credit analyst", "Compliance", "Data analyst", "Marketing", "Consulting", "Business developer", "Ressources humaines"],
  zones: ["Paris", "Île-de-France", "La Défense", "Lyon", "Marseille", "Lille", "Bordeaux", "Toulouse", "Nantes", "Nice",
    "Strasbourg", "Luxembourg", "Londres", "Genève", "Bruxelles", "Télétravail"],
};
async function prefsView() {
  const [p, offers, ag, src] = await Promise.all([api("/api/preferences"), api("/api/offers"), api("/api/agent/status"), api("/api/sources")]);
  const lists = { metiers: [...p.metiers], zones: [...p.zones], contrats: [...p.contrats] };
  const open = offers.filter((o) => !["ignoree", "confirmee", "soumission"].includes(o.statut) && !o.launched);
  const nSel = open.filter((o) => o.selected).length;
  const searching = GUIDE?.searching;
  shell("prefs", `
    <header class="head"><div class="titles"><h1>3. Critères et offres</h1><p class="sub" style="margin:4px 0 0">Indique ce que tu cherches, lance la recherche, puis coche les offres auxquelles postuler. Rien n'est envoyé à cette étape.</p></div></header>
    ${sitesCard(ag)}
    ${sourcesCard(src)}
    <form class="card pad form" id="pf">
      <h2>Mes critères</h2>
      ${[["metiers", "Métiers ciblés *", "ex. M&A, private debt, contrôle de gestion"], ["zones", "Localisations", "ex. Paris — laisse vide pour partout"]].map(([k, l, ph]) => `
        <div class="f" style="display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600">${l}
          <div class="chips" id="c_${k}"></div>
          <input type="text" data-list="${k}" list="dl_${k}" placeholder="${ph} — Entrée pour ajouter" aria-label="${l}" aria-describedby="err_${k}">
          <span class="field-err" id="err_${k}" role="alert"></span></div>`).join("")}
      <datalist id="dl_metiers">${SUGGEST.metiers.map((v) => `<option value="${esc(v)}">`).join("")}</datalist>
      <datalist id="dl_zones">${SUGGEST.zones.map((v) => `<option value="${esc(v)}">`).join("")}</datalist>
      <div class="f" style="display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600">Types de contrat
        <div class="ob-f" id="contrats" role="group" aria-label="Types de contrat">${CONTRACTS.map(([v, l]) => `<button type="button" class="pill" data-c="${v}" aria-pressed="${lists.contrats.includes(v)}">${l}</button>`).join("")}</div></div>
      <label class="f" style="max-width:320px">Disponible à partir du<input type="date" name="start_from" value="${esc(p.start_from)}"><span class="hint">Les offres qui commencent avant sont écartées.</span></label>
      <details class="adv"><summary>Réglages avancés</summary>
        <div class="form two" style="margin-top:12px">
          <label class="f">Score minimal pour retenir une offre<input type="number" name="min_score" min="0" max="100" value="${p.min_score}"></label>
          <label class="f">Pages de résultats lues par mot-clé<input type="number" name="max_pages" min="1" max="10" value="${p.max_pages}"></label>
          <label class="f">Recherche automatique toutes les (heures)<input type="number" name="search_every_hours" min="1" max="168" value="${p.search_every_hours}"></label>
        </div>
        <label class="check" style="margin-top:8px"><input type="checkbox" name="include_external" ${p.include_external ? "checked" : ""}> Inclure les offres à finir sur le site de l'entreprise (semi-assisté)</label>
      </details>
      <div class="row" style="flex-wrap:wrap"><button class="btn btn-ghost" type="submit">Enregistrer</button>
        <button class="btn btn-primary" type="button" id="searchNow" ${searching ? "disabled" : ""}>${searching ? '<span class="spin"></span> Recherche en cours…' : "Rechercher des offres"}</button></div>
    </form>
    <section class="card pad stack">
      <div class="spread"><div><h2>Offres trouvées</h2><div class="sub" style="font-size:14px">${open.length} offre${open.length > 1 ? "s" : ""} · <b>${nSel} sélectionnée${nSel > 1 ? "s" : ""}</b></div></div>
        ${nSel ? `<a class="btn btn-primary" href="#/mode">Continuer : étape 4 →</a>` : ""}</div>
      ${offerBrowser("crit")}
    </section>`);
  const draw = (k) => { if (k === "contrats") return; document.getElementById("c_" + k).innerHTML = lists[k].map((v, i) => `<span class="chip">${esc(v)}<button type="button" data-rm="${k}:${i}" aria-label="Retirer ${esc(v)}">×</button></span>`).join("") || '<span class="hint">Aucun</span>'; };
  Object.keys(lists).forEach(draw);
  $app.querySelectorAll("[data-list]").forEach((inp) => inp.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); const v = inp.value.trim(); if (v && !lists[inp.dataset.list].includes(v)) { lists[inp.dataset.list].push(v); draw(inp.dataset.list); } inp.value = ""; }
  });
  document.getElementById("pf").addEventListener("click", (e) => { const b = e.target.closest("[data-rm]"); if (!b) return; const [k, i] = b.dataset.rm.split(":"); lists[k].splice(+i, 1); draw(k); });
  document.getElementById("contrats").onclick = (e) => {
    const b = e.target.closest("[data-c]"); if (!b) return;
    const v = b.dataset.c, i = lists.contrats.indexOf(v);
    if (i >= 0) lists.contrats.splice(i, 1); else lists.contrats.push(v);
    b.setAttribute("aria-pressed", String(i < 0));
  };
  const save = async () => {
    $app.querySelectorAll("[data-list]").forEach((inp) => { const v = inp.value.trim(); if (v && !lists[inp.dataset.list].includes(v)) lists[inp.dataset.list].push(v); inp.value = ""; });
    const err = document.getElementById("err_metiers");
    if (!lists.metiers.length) { err.textContent = "Ajoute au moins un métier : c'est ce que le robot va chercher."; document.querySelector("[data-list=metiers]").focus(); throw new Error(); }
    err.textContent = "";
    const f = new FormData(document.getElementById("pf"));
    const body = { ...lists, start_from: f.get("start_from") || "", min_score: +f.get("min_score"), max_pages: +f.get("max_pages"),
      search_every_hours: +f.get("search_every_hours"), include_external: f.get("include_external") === "on" };
    await act(() => api("/api/preferences", { method: "PUT", body }), "Critères enregistrés.");
  };
  document.getElementById("pf").onsubmit = (e) => { e.preventDefault(); save().then(route).catch(() => {}); };
  document.getElementById("searchNow").onclick = async () => { try { await save(); } catch { return; } await act(() => api("/api/search", { method: "POST" }), "Recherche lancée : les offres arrivent dans quelques minutes. Aucun envoi."); route(); };
  bindOfferBrowser("crit", open, {
    head: "<th>Poste</th><th>Entreprise</th><th>Début</th><th>Match</th><th>Préparation</th><th></th>",
    empty: searching ? "Recherche en cours : les offres arrivent dans quelques minutes." : "Aucune offre pour l'instant. Renseigne tes critères puis clique « Rechercher des offres ».",
    row: (o) => `<tr class="${o.selected ? "selrow" : ""}"><td><a href="${esc(o.url)}" target="_blank" rel="noopener" style="font-weight:600;color:var(--ink)">${esc(o.poste)}</a><div class="hint">${o.apply_type === "jobteaser" ? "Candidature simplifiée JobTeaser" : "Site de l'entreprise"}${o.localisation ? " · " + esc(o.localisation) : ""}</div>${reasonLine(o)}</td>
          <td>${esc(o.entreprise)}</td><td class="sub">${esc(monthLabel(o.start_date))}</td><td>${scoreBadge(o)}</td>
          <td>${!o.selected ? "" : o.statut === "preparation" ? '<span class="badge b-blue"><span class="spin"></span>En cours</span>' : o.statut === "a_valider" ? '<span class="badge b-green">Prêt</span>' : statusBadge(o.statut)}</td>
          <td style="white-space:nowrap">${selectBtn(o)}</td></tr>`,
    after: bindSelect,
  });
  bindSites(ag);
  bindSources();
  if (searching || open.some((o) => o.selected && o.statut === "preparation") || (ag.robot === "extension" && !ag.linked)) schedulePoll(4000);
}

// ---------- sources d'offres : entreprises lues via les API publiques des plateformes de recrutement ----------
function sourcesCard(src) {
  const st = (x) => x.last_status.startsWith("erreur") ? `<span class="koc">${esc(x.last_status)}</span>` : `<span class="sub">${esc(x.last_status || "en attente")}</span>`;
  return `<section class="card pad stack" id="srcCard">
    <div><h2>Mes sources d'offres</h2>
      <div class="sub" style="font-size:14px">Ajoute les entreprises qui t'intéressent : CandiPilot lit leurs offres directement sur leur plateforme de recrutement (Greenhouse, Lever, Ashby, SmartRecruiters, Workable), sans compte ni robot.</div></div>
    <form id="srcForm" class="row" style="flex-wrap:wrap">
      <input type="url" name="url" placeholder="Adresse de la page carrières, ex. https://boards.greenhouse.io/adyen" aria-label="Adresse de la page carrières" style="flex:1 1 300px" required>
      <button class="btn btn-primary" type="submit">Ajouter</button></form>
    ${src.suggestions.length ? `<div class="ob-f" aria-label="Entreprises suggérées"><span class="hint" style="align-self:center">Suggestions :</span>${src.suggestions.map((x) =>
      `<button type="button" class="pill" data-sugg="${esc(JSON.stringify(x))}">+ ${esc(x.company)}</button>`).join("")}</div>` : ""}
    ${src.sources.length ? `<ul class="sitelist">${src.sources.map((x) => `<li><b>${esc(x.company)}</b><span class="badge b-mute">${esc(x.platform_label)}</span>${st(x)}
      <button class="btn btn-quiet btn-sm" data-delsrc="${x.id}" aria-label="Retirer ${esc(x.company)}">Retirer</button></li>`).join("")}</ul>` : ""}
  </section>`;
}

function bindSources() {
  const add = (body, msg) => act(() => api("/api/sources", { method: "POST", body }), msg).then(route).catch(() => {});
  document.getElementById("srcForm").onsubmit = (e) => { e.preventDefault(); const url = e.target.url.value.trim(); if (url) add({ url }, "Entreprise ajoutée : ses offres arrivent dans quelques instants."); };
  $app.querySelectorAll("[data-sugg]").forEach((b) => b.onclick = () => { b.disabled = true; add(JSON.parse(b.dataset.sugg), "Entreprise ajoutée."); });
  $app.querySelectorAll("[data-delsrc]").forEach((b) => b.onclick = () => act(() => api(`/api/sources/${b.dataset.delsrc}`, { method: "DELETE" }), "Source retirée.").then(route));
}

// ---------- sites connectés (extension du navigateur) ----------
const SITE_LOGIN = { jobteaser: "https://www.jobteaser.com/fr/users/sign_in", linkedin: "https://www.linkedin.com/login",
  wttj: "https://www.welcometothejungle.com/fr/signin" };
const extensionInstalled = () => !!document.documentElement.dataset.candipilotExtension;
// L'extension vient d'être installée (« Ajouter à Chrome ») : la page se met à jour sans recharger.
new MutationObserver(() => { if (document.getElementById("sitesCard") && extensionInstalled()) route(true); })
  .observe(document.documentElement, { attributes: true, attributeFilter: ["data-candipilot-extension"] });

function sitesCard(ag) {
  if (ag.robot !== "extension") return `<section class="card pad"><h2>Robot</h2><p class="sub" style="margin:4px 0 0;font-size:14px">${ag.robot === "simule" ? "Mode démonstration : robot simulé, aucune vraie candidature." : "Robot local : il utilise le navigateur de la machine qui héberge CandiPilot."}</p></section>`;
  const installed = extensionInstalled() || ag.linked;
  const ext = ag.linked ? `<span class="badge b-green">✓ Extension reliée${ag.online ? " · en ligne" : ""}</span>`
    : installed ? '<span class="badge b-amber">Extension installée, liaison en cours…</span>' : '<span class="badge b-mute">Extension à installer</span>';
  return `<section class="card pad stack" id="sitesCard">
    <div class="spread"><div><h2>Mes sites connectés *</h2>
      <div class="sub" style="font-size:14px">CandiPilot travaille dans <b>ton</b> navigateur, avec les sessions que tu as déjà : aucun mot de passe à nous donner.</div></div>${ext}</div>
    ${installed ? `
      <ul class="sitelist">${ag.sites.map((x) => `<li><span class="${x.connected ? "okc" : "koc"}">${x.connected ? "✓" : x.connected === false ? "✗" : "…"}</span>
        <b>${esc(x.name)}</b><span class="sub">${x.connected ? "connecté" : x.connected === false ? esc(x.detail || "non connecté") : "pas encore vérifié"}</span>
        ${x.connected ? "" : `<a class="btn btn-ghost btn-sm" href="${esc(x.key === "jobteaser" && ag.portal ? ag.portal + "/fr/job-offers" : SITE_LOGIN[x.key])}" target="_blank" rel="noopener">Se connecter</a>`}</li>`).join("")}</ul>
      <div class="row" style="flex-wrap:wrap"><button class="btn btn-ghost" id="recheck" ${ag.linked ? "" : "disabled"}>J'ai fini de me connecter : vérifier</button>
        <span class="hint">Connecte-toi normalement sur chaque site, dans un onglet de ce navigateur, puis clique « vérifier ».</span></div>`
    : ag.store_url ? `<div class="installone">
        <a class="btn btn-ai" href="${esc(ag.store_url)}" target="_blank" rel="noopener">Ajouter à Chrome — gratuit</a>
        <span class="hint">1 clic sur « Ajouter à Chrome », puis reviens ici : l'extension se relie toute seule à ton compte.</span></div>`
    : `<details class="adv"><summary>Installer l'extension (version de test)</summary><ol class="install" style="margin-top:10px">
        <li><a class="btn btn-primary btn-sm" href="/extension.zip">Télécharger l'extension</a> puis double-clique sur le fichier pour le décompresser.</li>
        <li>Dans Chrome, ouvre <code>chrome://extensions</code> et active le <b>Mode développeur</b> (en haut à droite).</li>
        <li>Clique <b>Charger l'extension non empaquetée</b> et choisis le dossier <code>candipilot-extension</code>.</li>
        <li>Reviens sur cette page : l'extension se relie toute seule à ton compte.</li></ol></details>
      <p class="hint" style="margin:0">L'installation en un clic depuis le Chrome Web Store arrive bientôt.</p>`}
    <details><summary class="hint">Mon école sur JobTeaser</summary>
      <form id="jtPortal" class="row" style="flex-wrap:wrap;margin-top:8px">
        <label class="f" style="flex:1 1 260px">Adresse JobTeaser de ton école (détectée automatiquement)<input type="text" name="portal_url" value="${esc(ag.portal)}" placeholder="https://monecole.jobteaser.com"></label>
        <button class="btn btn-ghost" type="submit" style="align-self:center">Enregistrer</button></form></details>
  </section>`;
}

function bindSites(ag) {
  document.getElementById("jtPortal")?.addEventListener("submit", (e) => { e.preventDefault(); act(() => api("/api/jobteaser", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Adresse enregistrée.").then(route); });
  document.getElementById("recheck")?.addEventListener("click", () => act(() => api("/api/agent/check", { method: "POST" }), "Vérification demandée à l'extension…").then(() => schedulePoll(4000)));
}

// ---------- import du CV : on lit le CV et on propose de remplir le profil (validation par l'utilisateur) ----------
async function uploadCv(file) {
  const fd = new FormData(); fd.append("file", file); fd.append("type", "cv");
  const d = await act(() => api("/api/documents", { method: "POST", body: fd }));
  if (!d.is_default) await api(`/api/documents/${d.id}`, { method: "PATCH", body: { is_default: true } });
  toast("CV importé : il sera utilisé pour tes candidatures.");
  const { suggestions } = await api(`/api/documents/${d.id}/extract`, { method: "POST" }).catch(() => ({ suggestions: [] }));
  if (!suggestions.length) return route();
  modal(`<h2>On a lu ton CV</h2>
    <p class="sub" style="margin:6px 0 12px">Coche ce que tu veux ajouter à ton profil. Rien n'est enregistré sans ton accord.</p>
    <form id="cvf" class="stack" style="gap:8px">${suggestions.map((x) => `<label class="check cvsug"><input type="checkbox" name="${x.field}" ${x.current ? "" : "checked"}>
      <span><b>${esc(x.label)}</b> : ${esc(x.value)}${x.current ? `<br><span class="hint">remplace « ${esc(x.current)} »</span>` : ""}</span></label>`).join("")}
      <div class="row" style="justify-content:flex-end;margin-top:8px"><button type="button" class="btn btn-ghost" data-close>Plus tard</button><button class="btn btn-primary" type="submit">Ajouter à mon profil</button></div></form>`,
    (el, close) => el.querySelector("#cvf").onsubmit = async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(suggestions.filter((x) => e.target.elements[x.field].checked).map((x) => [x.field, x.value]));
      if (Object.keys(body).length) { await act(() => api("/api/profile", { method: "PUT", body }), "Profil complété avec ton CV."); ME = await api("/api/auth/me"); }
      close(); route();
    });
}

// ---------- étape 1 : profil ----------
async function profileView() {
  const p = await api("/api/profile");
  const req = new Set(["first_name", "last_name", "phone", "availability", "gender"]);
  const field = (k, l, type = "text", ph = "") => `<label class="f">${l}${req.has(k) ? " *" : ""}<input type="${type}" name="${k}" value="${esc(p[k])}" placeholder="${esc(ph)}" ${req.has(k) ? "required" : ""}></label>`;
  shell("profile", `
    <header class="head"><div class="titles"><h1>1. Mon profil</h1><p class="sub" style="margin:4px 0 0">Ces informations remplissent tes candidatures. Les champs marqués * sont obligatoires.</p></div>
      ${ME.owner ? '<button class="btn btn-ghost" id="importYaml" title="Importe config/profile.yaml du robot">Importer le profil du robot</button>' : ""}</header>
    <section class="card pad cvquick">
      <div><h2>Gagne du temps</h2><p class="sub" style="margin:2px 0 0;font-size:14px">Importe ton CV (PDF) : on remplit ton profil pour toi, tu vérifies.</p></div>
      <label class="btn btn-primary">Importer mon CV<input type="file" id="cvQuick" accept="application/pdf" hidden></label>
    </section>
    <form class="card pad form" id="prf">
      <h2>L'essentiel</h2>
      <div class="form two">${field("first_name", "Prénom")}${field("last_name", "Nom")}
        ${field("phone", "Téléphone", "tel", "+33…")}${field("availability", "Disponible à partir de", "text", "ex. Mars 2027")}
        <label class="f">Genre (demandé par certains formulaires) *<select name="gender" required><option value="">—</option>${["Homme", "Femme", "Autre"].map((g) => `<option ${p.gender === g ? "selected" : ""}>${g}</option>`).join("")}</select></label>
        <label class="f">E-mail<input type="email" value="${esc(p.email)}" disabled><span class="hint">Modifiable dans <a href="#/compte">Mon compte</a>.</span></label></div>
      <details class="adv" ${["linkedin", "location", "school", "program", "competences", "duration", "languages", "internship_agreement"].some((k) => p[k]) ? "open" : ""}>
        <summary>Plus d'informations (facultatif) : elles améliorent le classement des offres et les réponses aux formulaires</summary>
        <div class="form two" style="margin-top:12px">
          ${field("linkedin", "LinkedIn", "url", "https://www.linkedin.com/in/…")}${field("location", "Ville", "text", "Paris")}
          ${field("school", "École")}${field("program", "Programme")}
          ${field("duration", "Durée du stage", "text", "6 mois")}${field("languages", "Langues", "text", "Français (natif), anglais (courant)")}
          ${field("internship_agreement", "Convention de stage", "text", "Oui, fournie par l'école")}
        </div>
        <label class="f" style="margin-top:12px">Compétences<input type="text" name="competences" value="${esc(p.competences)}" placeholder="ex. Python, SQL, DCF, Excel, private debt"><span class="hint">Utilisées pour le calcul du % de compatibilité.</span></label>
      </details>
      <div><button class="btn btn-primary" type="submit">Enregistrer le profil</button></div>
    </form>`);
  document.getElementById("cvQuick").onchange = (e) => e.target.files[0] && uploadCv(e.target.files[0]);
  document.getElementById("prf").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/profile", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Profil enregistré.").then(async () => { ME = await api("/api/auth/me"); route(); }); };
  if (ME.owner) document.getElementById("importYaml").onclick = () => act(() => api("/api/profile/import-yaml", { method: "POST" })).then((r) => { toast("Importé : " + r.imported.join(", ") + "."); route(); });
}

// ---------- étape 2 : CV et lettre ----------
async function documentsView() {
  const [docs, wf, p] = await Promise.all([api("/api/documents"), api("/api/workflow"), api("/api/profile")]);
  const cv = docs.find((d) => d.type === "cv" && d.is_default), lettre = docs.find((d) => d.type === "lettre" && d.is_default);
  const slot = (d, type, label) => `
    <div class="doc"><div class="thumb"><i class="d"></i><i></i><i></i><i class="v"></i><i></i></div>
      <div class="stack" style="gap:4px;min-width:0"><span class="hint">${label}</span>
        ${d ? `<b style="overflow-wrap:anywhere">${esc(d.name)}</b><span class="sub" style="font-size:13px">Version ${d.version} · ajouté ${ago(d.created_at)}</span>
               <span class="row" style="gap:12px"><a href="/api/documents/${d.id}/file" target="_blank" rel="noopener">Voir</a></span>` : `<b>Aucun fichier</b>`}
        <form class="upform" data-type="${type}"><label class="drop">
          <input type="file" name="file" accept="application/pdf" required aria-label="${label} (PDF)">
          <b>${d ? "Remplacer" : "Importer"} : glisse ton PDF ici</b><span class="hint">ou clique pour le choisir · 5 Mo maximum</span></label></form>
      </div></div>`;
  shell("documents", `
    <header class="head"><div class="titles"><h1>2. CV et lettre de motivation</h1><p class="sub" style="margin:4px 0 0">Les documents sélectionnés ici sont utilisés pour tes candidatures.</p></div></header>
    <section class="card pad stack"><h2>Mon CV *</h2>${slot(cv, "cv", "CV utilisé pour les candidatures")}</section>
    <section class="card pad stack">
      <h2>Ma lettre de motivation *</h2>
      <div class="modes" role="radiogroup" aria-label="Lettre de motivation">
        <button class="mode" data-src="document" aria-pressed="${wf.letter_source === "document"}"><b>${wf.letter_source === "document" ? "● " : ""}Ma lettre en PDF</b><span>La même lettre, importée par toi, est jointe à chaque candidature. CandiPilot ne la modifie jamais.</span></button>
        <button class="mode" data-src="template" aria-pressed="${wf.letter_source === "template"}"><b>${wf.letter_source === "template" ? "● " : ""}Une lettre adaptée à chaque offre</b><span>CandiPilot part de ton modèle et l'adapte au poste et à l'entreprise. Tu peux la relire avant l'envoi.</span></button>
      </div>
      ${wf.letter_source === "document" ? slot(lettre, "lettre", "Lettre utilisée pour les candidatures") : ""}
      ${wf.letter_source === "template" ? `<form id="tplf" class="form"><label class="f">Mon modèle de lettre<span class="hint">Variables remplacées pour chaque offre : {role}, {company}, {de_company}, {domain_line}, {domain}.</span><textarea name="letter_template" style="min-height:260px">${esc(p.letter_template)}</textarea></label>
        <div><button class="btn btn-primary" type="submit">Enregistrer le modèle</button></div></form>` : ""}
      ${!wf.letter_source ? '<div class="hint">Choisis une des deux options ci-dessus.</div>' : ""}
    </section>
    <section class="card pad stack">
      <h2>Tous mes documents</h2>
      ${docs.length ? `<div class="tablewrap"><table><thead><tr><th>Document</th><th>Type</th><th>Version</th><th>Ajouté</th><th></th></tr></thead><tbody>
        ${docs.map((d) => `<tr><td><a href="/api/documents/${d.id}/file" target="_blank" rel="noopener" style="font-weight:600;color:var(--ink)">${esc(d.name)}</a> ${d.is_default ? '<span class="badge b-green">Utilisé</span>' : ""}</td><td>${d.type === "cv" ? "CV" : "Lettre"}</td><td>v${d.version}</td><td class="sub">${ago(d.created_at)}</td>
          <td style="white-space:nowrap">${d.is_default ? "" : `<button class="btn btn-quiet btn-sm" data-def="${d.id}">Utiliser</button>`}<button class="btn btn-quiet btn-sm" data-ren="${d.id}">Renommer</button><button class="btn btn-quiet btn-sm" data-del="${d.id}" style="color:var(--red)">Supprimer</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">Aucun document pour l'instant.</div>`}
    </section>`);
  // Glisser-déposer ou choix du fichier : l'import part tout seul.
  $app.querySelectorAll(".upform").forEach((f) => {
    const input = f.querySelector("input[type=file]"), zone = f.querySelector(".drop");
    input.onchange = () => input.files[0] && f.requestSubmit();
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
    zone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) { input.files = e.dataTransfer.files; f.requestSubmit(); } });
  });
  $app.querySelectorAll(".upform").forEach((f) => f.onsubmit = async (e) => {
    e.preventDefault();
    if (f.dataset.type === "cv") return uploadCv(f.querySelector("input[type=file]").files[0]);
    const fd = new FormData(f); fd.append("type", f.dataset.type);
    const d = await act(() => api("/api/documents", { method: "POST", body: fd }));
    if (!d.is_default) await api(`/api/documents/${d.id}`, { method: "PATCH", body: { is_default: true } });
    toast(f.dataset.type === "cv" ? "CV importé : il sera utilisé pour tes candidatures." : "Lettre importée : elle sera utilisée pour tes candidatures.");
    route();
  });
  $app.querySelectorAll("[data-src]").forEach((b) => b.onclick = () => act(() => api("/api/workflow", { method: "PUT", body: { letter_source: b.dataset.src } }), "Choix de lettre enregistré.").then(route));
  document.getElementById("tplf")?.addEventListener("submit", (e) => { e.preventDefault(); act(() => api("/api/profile", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Modèle enregistré.").then(route); });
  $app.querySelectorAll("[data-def]").forEach((b) => b.onclick = () => act(() => api(`/api/documents/${b.dataset.def}`, { method: "PATCH", body: { is_default: true } }), "Document sélectionné pour les candidatures.").then(route));
  $app.querySelectorAll("[data-ren]").forEach((b) => b.onclick = () => { const d = docs.find((x) => x.id === b.dataset.ren); const n = prompt("Nouveau nom :", d.name); if (n) act(() => api(`/api/documents/${d.id}`, { method: "PATCH", body: { name: n } }), "Renommé.").then(route); });
  $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { if (confirm("Supprimer ce document ?")) act(() => api(`/api/documents/${b.dataset.del}`, { method: "DELETE" }), "Document supprimé.").then(route); });
}

// ---------- étape 4 : mode de soumission ----------
async function modeView() {
  const wf = await api("/api/workflow");
  let mode = wf.submit_mode, prep = wf.want_prepare, rev = wf.want_review;
  const yn = (name, val, q, yes, no) => `<fieldset class="yn"><legend>${q}</legend>
    <label class="check"><input type="radio" name="${name}" value="1" ${val === 1 ? "checked" : ""}> Oui — ${yes}</label>
    <label class="check"><input type="radio" name="${name}" value="0" ${val === 0 ? "checked" : ""}> Non — ${no}</label></fieldset>`;
  const draw = () => {
    shell("mode", `
      <header class="head"><div class="titles"><h1>4. Mode de soumission</h1><p class="sub" style="margin:4px 0 0">Choisis comment tes candidatures seront envoyées. Ce choix ne déclenche aucun envoi.</p></div></header>
      <form class="card pad stack" id="mf">
        <div class="modes" role="radiogroup" aria-label="Mode de soumission">
          ${wf.modes.map((m) => `<button type="button" class="mode" data-m="${m.key}" aria-pressed="${mode === m.key}"><b>${mode === m.key ? "● " : ""}${esc(m.label)}</b><span>${esc(m.help)}</span></button>`).join("")}
        </div>
        ${mode === "auto" ? `<div class="banner">Le robot envoie lui-même chaque candidature après ta validation finale. Les dossiers avec une question du recruteur sans réponse ne partent pas. Les sites qui n'autorisent pas l'envoi automatique passent en mode assisté.</div>
          <label class="check consent"><input type="checkbox" id="consent" ${wf.auto_consent ? "checked" : ""}>
            <span>J'autorise CandiPilot à envoyer des candidatures <b>en mon nom</b>, uniquement pour les offres que je sélectionne et après ma validation finale (« Valider et démarrer le robot »). Je peux retirer cette autorisation à tout moment. Au plus ${wf.daily_limit} envois par jour.</span></label>` : ""}
        ${mode === "assisted" || mode === "manual" ? `
          ${yn("prep", prep, "Souhaites-tu préparer ou personnaliser ta candidature ?", "je vérifie chaque dossier (lettre, CV, réponses) avant l'envoi", "CandiPilot utilise directement mes documents et informations")}
          ${yn("rev", rev, "Souhaites-tu relire ta candidature avant l'envoi ?", "je relis chaque candidature et j'autorise son envoi", "je passe directement au récapitulatif final")}` : ""}
        <div class="row"><button class="btn btn-primary" type="submit" ${mode ? "" : "disabled"}>Enregistrer mon choix</button>
          ${wf.submit_mode ? `<a class="btn btn-ghost" href="#/envoi">Aller à l'étape 5 →</a>` : ""}</div>
      </form>`);
    $app.querySelectorAll("[data-m]").forEach((b) => b.onclick = () => { mode = b.dataset.m; draw(); });
    const consent = document.getElementById("consent"), submitBtn = $app.querySelector("#mf button[type=submit]");
    if (consent) { const sync = () => { submitBtn.disabled = !consent.checked; }; consent.onchange = sync; sync(); }
    $app.querySelectorAll("input[name=prep]").forEach((r) => r.onchange = () => { prep = +r.value; });
    $app.querySelectorAll("input[name=rev]").forEach((r) => r.onchange = () => { rev = +r.value; });
    document.getElementById("mf").onsubmit = async (e) => {
      e.preventDefault();
      const optional = mode !== "auto";
      if (optional && (prep === -1 || rev === -1)) return toast("Réponds aux deux questions (Oui ou Non).", true);
      const body = { submit_mode: mode, ...(optional ? { want_prepare: prep === 1, want_review: rev === 1 } : { auto_consent: true }) };
      await act(() => api("/api/workflow", { method: "PUT", body }), "Mode enregistré. Aucun envoi n'a été déclenché.");
      route();
    };
  };
  draw();
}

// ---------- étape 5 : valider l'envoi + résultat ----------
async function sendView() {
  const L = await api("/api/launch");
  const R = L.results, launchedAny = R.sent.length + R.failed.length + R.progress.length + R.verify.length > 0;
  const blocked = L.items.filter((i) => !i.ready && !i.blockers.some((b) => b.wait));
  const waiting = L.items.filter((i) => i.blockers.some((b) => b.wait));
  const subs = GUIDE?.steps[4]?.subs || [];
  const global = [];
  if (!L.mode_chosen) global.push(["Mode de soumission pas encore choisi.", "#/mode", "Choisir"]);
  else if (L.mode !== "auto" && (L.want_prepare === -1 || L.want_review === -1)) global.push(["Réponds aux questions de préparation et de relecture.", "#/mode", "Répondre"]);
  if (L.missing_jobteaser) global.push(["Il manque " + L.missing_jobteaser + ".", "#/criteres", "Voir mes sites"]);
  if (L.missing_profile.length) global.push(["Profil incomplet : " + L.missing_profile.join(", ") + ".", "#/profil", "Compléter"]);
  if (L.missing_documents.length) global.push(["Il manque : " + L.missing_documents.join(", ") + ".", "#/documents", "Ajouter"]);
  const yesNo = (v) => v === 1 ? "Oui" : v === 0 ? "Non" : "Pas encore répondu";
  const blk = (b, id) => `<div class="blk${b.wait ? " wait" : ""}">${b.wait ? '<span class="spin"></span>' : "!"} ${esc(b.label)}
    ${b.href ? `<a class="btn btn-quiet btn-sm" href="${b.href}">${esc(b.cta)}</a>` : b.action ? `<button class="btn btn-quiet btn-sm" data-act="${b.action}" data-id="${id}">${esc(b.cta)}</button>` : ""}</div>`;
  const row = (i) => `<div class="litem">${mono(i.entreprise)}<div class="grow"><div class="t"><a href="#/candidature/${i.id}" style="color:var(--ink)">${esc(i.poste)}</a></div>
      <div class="s">${esc(i.entreprise)} · ${esc(MODES[i.mode])} · ${i.score} % · lettre : ${i.letter_needed === 0 ? "non demandée" : i.letter_kind === "document" ? "ta lettre importée" : i.letter_kind === "generated" ? "adaptée à l'offre" : "à préparer"}</div>
      ${i.ready ? '<div class="blk ok">✓ Prêt à partir</div>' : i.blockers.map((b) => blk(b, i.id)).join("")}
      ${i.warnings.map((w) => `<div class="hint">ⓘ ${esc(w)}</div>`).join("")}</div>
    <button class="btn btn-quiet btn-sm" data-unsel="${i.id}">Retirer</button></div>`;
  const resItem = (r) => `<div class="litem">${mono(r.entreprise, 34)}<div class="grow"><div class="t"><a href="#/candidature/${r.id}" style="color:var(--ink)">${esc(r.poste)}</a> <span class="sub">· ${esc(r.entreprise)}</span></div>
      <div class="s">${esc(r.reason)}</div>
      ${r.actions?.length ? `<div class="row" style="flex-wrap:wrap;gap:6px;margin-top:6px">${r.actions.map((a) => a.action ? `<button class="btn btn-ghost btn-sm" data-act="${a.action}" data-id="${r.id}">${esc(a.cta)}</button>`
        : `<a class="btn btn-ghost btn-sm" href="${esc(a.href)}" ${a.external ? 'target="_blank" rel="noopener"' : ""}>${esc(a.cta)}</a>`).join("")}</div>` : ""}</div></div>`;
  const notSent = [...R.failed, ...(launchedAny ? blocked.map((i) => ({ ...i, reason: i.blockers.map((b) => b.label).join(" · "), actions: i.blockers.filter((b) => b.href || b.action).map((b) => ({ href: b.href, action: b.action, cta: b.cta })) })) : [])];
  const total = R.sent.length + notSent.length + R.progress.length + R.verify.length;
  shell("send", `
    <header class="head"><div class="titles"><h1>5. Valider l'envoi</h1><p class="sub" style="margin:4px 0 0">Vérifie le récapitulatif : rien ne part avant que tu cliques « Valider et démarrer le robot ».</p></div></header>
    ${global.map(([t, h, c]) => `<div class="blk big">! ${esc(t)} <a class="btn btn-primary btn-sm" href="${h}">${c}</a></div>`).join("")}
    <section class="card pad stack">
      <h2>Récapitulatif</h2>
      <div class="recap">
        <div><span class="hint">Mode de soumission</span><b>${L.mode_chosen ? esc(L.mode_label) : "Pas choisi"}</b><a href="#/mode">Modifier</a></div>
        ${L.mode_chosen && L.mode !== "auto" ? `<div><span class="hint">Préparation / personnalisation</span><b>${yesNo(L.want_prepare)}</b><a href="#/mode">Modifier</a></div>
          <div><span class="hint">Relecture avant envoi</span><b>${yesNo(L.want_review)}</b><a href="#/mode">Modifier</a></div>` : ""}
        <div><span class="hint">CV</span><b>${L.cv ? esc(L.cv.name) : "Aucun"}</b><a href="#/documents">Modifier</a></div>
        <div><span class="hint">Lettre</span><b>${L.letter_source === "document" ? (L.letter ? esc(L.letter.name) : "Lettre PDF manquante") : L.letter_source === "template" ? "Adaptée à chaque offre" : "Pas choisie"}</b><a href="#/documents">Modifier</a></div>
      </div>
      ${subList(subs)}
    </section>
    <section class="card pad stack">
      <div class="spread"><h2>Offres sélectionnées (${L.items.length})</h2><a class="btn btn-quiet btn-sm" href="#/criteres">Ajouter des offres</a></div>
      ${L.items.length ? L.items.map(row).join("") : `<div class="empty">Aucune offre en attente d'envoi. ${launchedAny ? "" : "Sélectionne des offres à l'étape 3."}</div>`}
      ${L.items.length ? `<div class="launch">
        <div class="sub" style="font-size:14px"><b style="color:var(--ink)">${L.ready_count}</b> prêt${L.ready_count > 1 ? "s" : ""} à partir${blocked.length ? ` · ${blocked.length} bloqué${blocked.length > 1 ? "s" : ""} (ne partiront pas)` : ""}${waiting.length ? ` · ${waiting.length} en préparation` : ""}</div>
        <button class="btn btn-ai" id="doLaunch" ${L.ready_count && !global.length ? "" : "disabled"}>Valider et démarrer le robot</button></div>` : ""}
    </section>
    ${launchedAny ? `
    <section class="card pad stack" id="results">
      <div class="spread"><h2>Résultat du traitement</h2>${R.progress.length || L.busy ? '<span class="badge b-blue"><span class="spin"></span>Le robot travaille</span>' : ""}</div>
      <div class="progress"><i style="width:${total ? (R.sent.length / total) * 100 : 0}%"></i></div>
      <div class="grid g4">
        ${[["Envoyées", R.sent, "b-green"], ["En cours", R.progress, "b-blue"], ["À vérifier", R.verify, "b-amber"], ["Non envoyées", notSent, "b-red"]].map(([t, xs, c]) =>
          `<div class="card pad stat"><div class="label">${t}</div><div class="value">${xs.length}</div><span class="badge ${c}" style="align-self:flex-start">${t}</span></div>`).join("")}
      </div>
      ${[["Non envoyées", notSent], ["À vérifier", R.verify], ["En cours", R.progress], ["Envoyées", R.sent]].filter(([, xs]) => xs.length).map(([t, xs]) =>
        `<h3 style="margin:8px 0 0;font-size:15px">${t}</h3>${xs.map(resItem).join("")}`).join("")}
    </section>` : ""}`);
  const after = (p, msg) => act(p, msg).then(route);
  $app.querySelectorAll("[data-unsel]").forEach((b) => b.onclick = () => selectOffer(b.dataset.unsel, false));
  $app.querySelectorAll("[data-act]").forEach((b) => b.onclick = () => {
    const id = b.dataset.id, a = b.dataset.act;
    if (a === "mark-sent" && !confirm("Confirmes-tu avoir envoyé cette candidature toi-même ?")) return;
    after(api(`/api/applications/${id}/${a}`, { method: "POST" }), { prepare: "Préparation lancée.", retry: "Dossier remis dans le récapitulatif.", verify: "Vérification lancée.", "mark-sent": "Marquée comme envoyée." }[a]);
  });
  document.getElementById("doLaunch")?.addEventListener("click", () => {
    const robotN = L.items.filter((i) => i.ready && i.mode !== "manual").length, selfN = L.ready_count - robotN;
    modal(`<h2>Valider et démarrer le robot</h2>
      <p class="sub">${robotN ? `<b>${robotN}</b> candidature${robotN > 1 ? "s" : ""} ${L.mode === "assisted" ? "seront ouvertes et pré-remplies par le robot : tu cliqueras « Postuler maintenant »" : "seront envoyées par le robot"}.` : ""}
        ${selfN ? `<br><b>${selfN}</b> dossier${selfN > 1 ? "s" : ""} prêt${selfN > 1 ? "s" : ""} à envoyer toi-même.` : ""}
        ${blocked.length ? `<br>${blocked.length} dossier${blocked.length > 1 ? "s" : ""} bloqué${blocked.length > 1 ? "s" : ""} ne partira pas.` : ""}</p>
      <p class="hint">Une candidature passe en « Envoyée » uniquement après confirmation de réception.</p>
      <div class="row" style="justify-content:flex-end"><button class="btn btn-ghost" data-close>Annuler</button><button class="btn btn-ai" id="confirmLaunch">Confirmer le lancement</button></div>`,
      (el, close) => el.querySelector("#confirmLaunch").onclick = () => act(() => api("/api/launch", { method: "POST" }), "Lancement validé : le robot démarre.").then(() => { close(); route(); }));
  });
  schedulePoll(R.progress.length || L.busy || waiting.length ? 3000 : 0);
}

// ---------- compte ----------
async function accountView() {
  shell("account", `
    <header class="head"><div class="titles"><h1>Mon compte</h1><p class="sub" style="margin:4px 0 0">Connexion et sécurité.</p></div></header>
    <section class="card pad stack"><h2>Adresse e-mail</h2>
      <form id="em" class="form two"><label class="f">Nouvelle adresse<input type="email" name="email" value="${esc(ME.email)}" autocomplete="email" required></label>
        <label class="f">Mot de passe actuel<input type="password" name="password" autocomplete="current-password" required></label>
        <div><button class="btn btn-ghost" type="submit">Changer d'adresse</button></div></form></section>
    <section class="card pad stack"><h2>Mot de passe</h2>
      <form id="pw" class="form two"><label class="f">Mot de passe actuel<input type="password" name="current" autocomplete="current-password" required></label>
        <label class="f">Nouveau mot de passe<input type="password" name="new" autocomplete="new-password" minlength="8" required>${PW_HINT}</label>
        <div><button class="btn btn-ghost" type="submit">Changer le mot de passe</button></div></form></section>
    <section class="card pad stack"><div class="spread"><h2>Apparence</h2>
      <div class="seg" role="group" aria-label="Apparence">${[["auto", "Automatique"], ["light", "Claire"], ["dark", "Sombre"]].map(([k, l]) => `<button data-theme-btn="${k}" aria-pressed="${getTheme() === k}">${l}</button>`).join("")}</div></div></section>
    <section class="card pad stack"><h2>Appareils connectés</h2>
      <div class="spread"><span class="sub" style="font-size:14px">${ME.sessions} session${ME.sessions > 1 ? "s" : ""} ouverte${ME.sessions > 1 ? "s" : ""} (14 jours maximum chacune).</span>
        <button class="btn btn-ghost" id="outAll">Se déconnecter partout</button></div></section>
    <section class="card pad stack"><h2>Supprimer mon compte</h2>
      <div class="spread"><span class="sub" style="font-size:14px">Efface ton profil, tes documents et ton historique.</span><button class="btn btn-danger" id="delAcc">Supprimer mon compte</button></div></section>`);
  document.getElementById("em").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/auth/email", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Adresse modifiée.").then(async () => { ME = await api("/api/auth/me"); route(); }); };
  document.getElementById("pw").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/auth/password", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }), "Mot de passe modifié. Tes autres appareils sont déconnectés.").then(async () => { e.target.reset(); ME = await api("/api/auth/me"); route(); }); };
  $app.querySelectorAll("[data-theme-btn]").forEach((b) => b.onclick = () => { setTheme(b.dataset.themeBtn); route(); });
  document.getElementById("outAll").onclick = () => { if (confirm("Te déconnecter de tous les appareils, y compris celui-ci ?")) act(() => api("/api/auth/logout-all", { method: "POST" })).then(() => { ME = null; GUIDE = null; go("#/login"); }); };
  document.getElementById("delAcc").onclick = () => modal(`<h2>Supprimer mon compte</h2><p class="sub">Action définitive. Saisis ton mot de passe pour confirmer.</p>
    <form id="da" class="form"><input type="password" name="password" required autocomplete="current-password" aria-label="Mot de passe">
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn btn-ghost" data-close>Annuler</button><button class="btn btn-danger" type="submit">Supprimer définitivement</button></div></form>`,
    (el) => el.querySelector("#da").onsubmit = (e) => { e.preventDefault(); act(() => api("/api/auth/account", { method: "DELETE", body: Object.fromEntries(new FormData(e.target)) }), "Compte supprimé.").then(() => { ME = null; GUIDE = null; el.remove(); go("#/register"); }); });
}

// ---------- routeur ----------
function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollMs = ms;
  if (ms) pollTimer = setTimeout(() => { if (!document.querySelector(".scrim") && !document.activeElement?.matches("input,textarea,select")) route(true); else schedulePoll(ms); }, ms);
}

let routing = 0;
async function route(quiet) {
  clearTimeout(pollTimer);
  pollMs = 0;
  const mine = ++routing;
  // Barre de chargement pour un changement de page, pas pour les rafraîchissements automatiques.
  if (quiet !== true) document.body.classList.add("loading");
  try { await routeNow(); } finally { if (mine === routing) document.body.classList.remove("loading"); }
}

async function routeNow() {
  const h = (location.hash || "#/").split("?")[0];
  const pub = { "#/login": () => authView("login"), "#/register": () => authView("register"), "#/mot-de-passe-oublie": forgotView, "#/reinitialiser": resetView };
  if (pub[h]) return pub[h]();
  if (!ME) { try { ME = await api("/api/auth/me"); } catch { sessionStorage.setItem("cp_next", location.hash || "#/"); return go("#/login"); } }
  try {
    GUIDE = await api("/api/guide");
    const m = h.match(/^#\/candidature\/([\w-]+)$/);
    const views = { "#/": dashboardView, "#/envoi": sendView, "#/a-valider": sendView, "#/candidatures": applicationsView, "#/offres": offersView,
      "#/criteres": prefsView, "#/profil": profileView, "#/documents": documentsView, "#/mode": modeView, "#/compte": accountView };
    await (m ? applicationView(m[1]) : (views[h] || dashboardView)());
    if (!pollMs && (GUIDE.steps.some((s) => s.waiting) || GUIDE.next?.waiting)) schedulePoll(4000);
  } catch (e) {
    if (!ME || e instanceof StalePage) return;
    toast(e.message, true);
    const main = $app.querySelector("main");
    if (main && !main.querySelector(".errcard")) {
      main.insertAdjacentHTML("afterbegin", `<section class="card pad errcard" role="alert"><div><b>Impossible de charger cette page.</b>
        <div class="sub" style="font-size:14px">${esc(e.message)} Vérifie ta connexion puis réessaie.</div></div><button class="btn btn-ghost" id="retry">Réessayer</button></section>`);
      document.getElementById("retry").onclick = () => route();
    }
  }
}

window.addEventListener("hashchange", route);
route();
