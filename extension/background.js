// CandiPilot — robot de candidature qui travaille dans le navigateur de l'utilisateur, avec SES sessions.
// 1. Liaison automatique : link.js lui transmet un jeton dès que l'utilisateur ouvre CandiPilot connecté.
// 2. Toutes les 30 s, il demande ses tâches au serveur et les exécute dans un onglet « robot ».
// 3. Il signale sur quels sites l'utilisateur est connecté (JobTeaser, LinkedIn, Welcome to the Jungle).
import { jtLoggedIn, jtListCards, jtReadStart, jtInspect, jtApply, jtSucceeded, jtAlreadyApplied } from "./jobteaser.js";
import { applyFieldAnswers, fillForm } from "./filler.js";
import { observePage, runAction } from "./agent.js";

const VERSION = chrome.runtime.getManifest().version;
const SITES = {
  linkedin: { url: "https://www.linkedin.com/feed/", ok: (u) => u.includes("/feed") && !/login|authwall|checkpoint|signup/.test(u) },
  wttj: { url: "https://www.welcometothejungle.com/fr/me/settings/account", ok: (u) => u.includes("/me/") && !/signin|login|sign_in/.test(u) },
};
// Une seule action à la fois dans l'onglet robot (tâches et vérification des sites).
let chain = Promise.resolve(), pollQueued = false;
function exclusive(fn) { const p = chain.then(fn, fn); chain = p.catch(() => {}); return p; }

// ---------- serveur ----------
const conf = () => chrome.storage.local.get(["server", "token", "userId"]);

async function api(path, { method = "GET", body, raw = false } = {}) {
  const { server, token } = await conf();
  if (!server || !token) throw new Error("Extension non reliée : ouvre CandiPilot.");
  const r = await fetch(server + path, {
    method, headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401) { await chrome.storage.local.remove("token"); throw new Error("Jeton refusé : ouvre CandiPilot pour relier l'extension."); }
  if (raw) return r;
  if (r.status === 204) return null;
  if (!r.ok) throw new Error(`Serveur : ${r.status}`);
  return r.json();
}

async function fileB64(f) {
  if (!f) return null;
  const r = await api("/api/agent/file?ref=" + encodeURIComponent(f.ref), { raw: true });
  if (!r.ok) throw new Error("Fichier introuvable : " + f.name);
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = ""; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { name: f.name, b64: btoa(s) };
}

// ---------- onglet robot ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function robotTab(url, focus = false) {
  const { robotTabId } = await chrome.storage.session.get("robotTabId");
  let tab = robotTabId ? await chrome.tabs.get(robotTabId).catch(() => null) : null;
  if (!tab) {
    const w = await chrome.windows.create({ url, focused: focus, state: focus ? "normal" : "minimized" });
    tab = w.tabs[0];
    await chrome.storage.session.set({ robotTabId: tab.id });
  } else {
    await chrome.tabs.update(tab.id, { url });
    if (focus) { await chrome.windows.update(tab.windowId, { focused: true, state: "normal" }); await chrome.tabs.update(tab.id, { active: true }); }
  }
  await sleep(300);
  for (let i = 0; i < 100; i++) {
    const t = await chrome.tabs.get(tab.id);
    if (t.status === "complete" && t.url && !t.url.startsWith("about:")) break;
    await sleep(300);
  }
  await sleep(800);
  return tab.id;
}

async function run(tabId, func, args = []) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res ? res.result : undefined;
}

async function currentUrl(tabId) { return (await chrome.tabs.get(tabId)).url || ""; }

// ---------- sites ----------
async function detectPortal(saved) {
  // Portail de l'école déduit des cookies JobTeaser du navigateur (ex. skema.jobteaser.com).
  if (saved && !/\/\/www\.jobteaser\.com/.test(saved)) return saved;
  const cookies = await chrome.cookies.getAll({ domain: "jobteaser.com" }).catch(() => []);
  const count = {};
  for (const c of cookies) {
    const host = c.domain.replace(/^\./, "");
    if (/^[a-z0-9-]+\.jobteaser\.com$/.test(host) && !/^(www|connect|static|api|cdn|assets)\./.test(host)) count[host] = (count[host] || 0) + 1;
  }
  const best = Object.entries(count).sort((a, b) => b[1] - a[1])[0];
  return best ? "https://" + best[0] : saved || "https://www.jobteaser.com";
}

async function checkSites(portal, keys = ["jobteaser", ...Object.keys(SITES)]) {
  const sites = {};
  const p = await detectPortal(portal);
  const tab = await robotTab(p + "/fr/job-offers");
  const jt = await run(tab, jtLoggedIn);
  sites.jobteaser = { connected: !!(jt && jt.connected), portal: p,
    detail: jt && jt.connected ? "" : "Connecte-toi à JobTeaser dans ce navigateur (" + p.replace("https://", "") + ")." };
  for (const [key, s] of Object.entries(SITES).filter(([k]) => keys.includes(k))) {
    try {
      const t = await robotTab(s.url);
      await sleep(1500);
      sites[key] = { connected: s.ok(await currentUrl(t)) };
    } catch (e) { sites[key] = { connected: false, detail: String(e.message || e) }; }
  }
  return sites;
}

// ---------- tâches ----------
async function doSearch(job) {
  const stats = { external: 0, date: 0, login: true }, found = [], cands = [];
  const known = new Set(job.known || []);
  const has = (txt, list) => !list.length || list.some((w) => txt.toLowerCase().includes(w.toLowerCase()));
  let tab = await robotTab(`${job.portal}/fr/job-offers?q=${encodeURIComponent(job.keywords[0] || "")}`);
  if (!(await run(tab, jtLoggedIn)).connected) { stats.login = false; return { found, stats }; }
  for (const kw of job.keywords) {
    await api(`/api/agent/jobs/${job.id}/progress`, { method: "POST", body: { text: `Recherche « ${kw} »` } });
    for (let n = 1; n <= job.max_pages; n++) {
      tab = await robotTab(`${job.portal}/fr/job-offers?q=${encodeURIComponent(kw)}&page=${n}`);
      const cards = await run(tab, jtListCards);
      if (!cards || !cards.length) break;
      for (const c of cards) {
        if (!c.href || known.has(c.href) || !has(c.contract, job.contracts) || !has(c.location, job.locations)) continue;
        if (!c.easy && !job.include_external) { stats.external++; continue; }
        known.add(c.href);
        cands.push({ url: c.href, company: c.company, role: c.title, location: c.location, contract: c.contract,
          keyword: kw, apply_type: c.easy ? "jobteaser" : "external" });
      }
      await sleep(1200);
    }
  }
  // Dates de début : lues sur la page de l'offre (lent) seulement si nécessaire, et mémorisées 14 jours.
  const { jtStart = {} } = await chrome.storage.local.get("jtStart");
  const fresh = (u) => jtStart[u] && Date.now() - jtStart[u].at < 14 * 864e5;
  const toRead = job.start_from ? cands.filter((o) => !fresh(o.url)).length : 0;
  await api(`/api/agent/jobs/${job.id}/progress`, { method: "POST", body: { text: toRead
    ? `Lecture des dates de début (${toRead} offre${toRead > 1 ? "s" : ""} à ouvrir sur ${cands.length})` : `${cands.length} offres trouvées` } });
  let batch = [], i = 0;
  const flush = async () => {        // offres envoyées par paquets : visibles et notées par l'IA sans attendre la fin
    if (!batch.length) return;
    await api(`/api/agent/jobs/${job.id}/partial`, { method: "POST", body: { found: batch } }).catch(() => {});
    batch = [];
  };
  for (const o of cands) {
    let iso = "";
    if (job.start_from) {
      if (fresh(o.url)) iso = jtStart[o.url].iso;
      else {
        tab = await robotTab(o.url);
        iso = (await run(tab, jtReadStart)).iso;
        jtStart[o.url] = { iso, at: Date.now() };
        if (++i % 5 === 0) {           // entre deux offres : tes actions (préparer, envoyer) passent avant la suite
          await chrome.storage.local.set({ jtStart });
          await runUrgent();
        }
      }
      if (!iso || iso < job.start_from) { stats.date++; continue; }
    }
    found.push({ ...o, start_date: iso });
    batch.push({ ...o, start_date: iso });
    if (batch.length >= 10) await flush();
  }
  await chrome.storage.local.set({ jtStart });
  await flush();
  return { found, stats };
}

// Pendant une longue recherche : exécute tout de suite les actions demandées par l'utilisateur.
async function runUrgent() {
  for (let n = 0; n < 5; n++) {
    const job = await api("/api/agent/next?urgent=1").catch(() => null);
    if (!job) return;
    let body;
    try { body = { ok: true, data: await execute(job) }; }
    catch (e) { body = { ok: false, error: String(e.message || e).slice(0, 300) }; }
    await api(`/api/agent/jobs/${job.id}/result`, { method: "POST", body }).catch(() => {});
  }
}

async function doSubmit(job) {
  const payload = { mode: job.mode, gender: job.gender, answers: job.answers, cv: await fileB64(job.cv), letter: await fileB64(job.letter) };
  const tab = await robotTab(job.url, job.mode === "assisted");
  const res = await run(tab, jtApply, [payload]);
  if (job.mode === "assisted" && res.status === "filled_dry") {
    // Assisté : le candidat clique lui-même « Postuler maintenant » ; on attend la preuve (15 min max).
    for (let i = 0; i < 300; i++) {
      await sleep(3000);
      const ok = await run(tab, jtSucceeded).catch(() => null);
      if (ok) return { status: "submitted", detail: res.detail + " ; envoyé par toi" };
      if (ok === null) break;
    }
    return { status: "uncertain", detail: res.detail + " ; envoi non détecté (onglet fermé ou délai dépassé)" };
  }
  return res;
}

async function execute(job) {
  switch (job.type) {
    case "search": return await doSearch(job);
    case "prepare": return { info: await run(await robotTab(job.url), jtInspect) };
    case "submit": return { ...(await doSubmit(job)), key: job.key };      // clé d'idempotence renvoyée telle quelle
    case "apply": return { ...(await doApply(job)), key: job.key };
    case "verify": return { ok: await run(await robotTab(job.url), jtAlreadyApplied) };
    case "check_sites": {
      const cfg = await api("/api/agent/config");
      const sites = await checkSites(job.portal, cfg.sites); await chrome.storage.local.set({ sites }); return { sites };
    }
    default: throw new Error("Tâche inconnue : " + job.type);
  }
}

function poll() {
  if (pollQueued) return chain;
  pollQueued = true;
  return exclusive(async () => { pollQueued = false; await pollNow(); });
}

async function pollNow() {
  try {
    for (let i = 0; i < 50; i++) {
      const job = await api("/api/agent/next");
      if (!job) break;
      let body;
      try { body = { ok: true, data: await execute(job) }; }
      catch (e) { body = { ok: false, error: String(e.message || e).slice(0, 300) }; }
      await api(`/api/agent/jobs/${job.id}/result`, { method: "POST", body });
    }
    await chrome.storage.local.set({ lastPoll: Date.now(), lastError: "" });
  } catch (e) {
    await chrome.storage.local.set({ lastError: String(e.message || e) });
  }
}

// ---------- messages (liaison, fenêtre de l'extension) ----------
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "cp-page") {
      const c = await conf();
      return reply({ linked: !!c.token && c.server === msg.origin && c.userId === msg.userId });
    }
    if (msg.type === "cp-token") {
      await chrome.storage.local.set({ server: msg.origin, token: msg.token, userId: msg.userId, sites: null });
      reply({ ok: true });
      refreshSites().catch(() => {});
      return poll();
    }
    if (msg.type === "status") return reply({ ...(await conf()), token: undefined, linked: !!(await conf()).token,
      ...(await chrome.storage.local.get(["lastPoll", "lastError", "sites"])), version: VERSION });
    if (msg.type === "check") { await refreshSites(); return reply(await chrome.storage.local.get("sites")); }
    if (msg.type === "fill") return reply(await fillTab(msg.tabId));
    if (msg.type === "frames") return reply(await framesNeedingAccess(msg.tabId));
    reply(null);
  })().catch((e) => reply({ error: String(e.message || e) }));
  return true;
});

function refreshSites() { return exclusive(refreshSitesNow); }

async function refreshSitesNow() {
  const cfg = await api("/api/agent/config");
  const sites = await checkSites(cfg.portal, cfg.sites);
  await chrome.storage.local.set({ sites });
  await api("/api/agent/sites", { method: "PUT", body: { sites } }).catch(() => {});
  return sites;
}

// Remplit le formulaire de l'onglet donné avec le profil et les documents du candidat.
function fillTab(tabId) { return exclusive(() => fillTabNow(tabId)); }

async function fillTabNow(tabId) {
  const profile = await api("/api/agent/profile");
  const files = { cv: await fileB64(profile.cv), letter: await fileB64(profile.letter) };
  const url = (await chrome.tabs.get(tabId)).url || "";
  return await fillFrames(tabId, profile, files, { url });
}

async function fillFrames(tabId, profile, files, ctx = {}) {
  // Dans chaque cadre de la page : les formulaires Greenhouse ou Lever sont souvent intégrés dans un cadre (iframe).
  const frames = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: fillForm, args: [profile, files] });
  const out = { filled: [], check: [], todo: [], total: 0, platform: "", steps: false, watching: false, ai: [] };
  for (const { result: r } of frames) {
    if (!r) continue;
    for (const k of ["filled", "check", "todo"]) out[k].push(...r[k]);
    out.total += r.total; out.platform ||= r.platform; out.steps ||= r.steps; out.watching ||= r.watching;
  }
  // Agent IA : les champs non compris (intitulés et choix seulement) sont proposés à Claude, cadre par cadre.
  for (const { frameId, result: r } of frames) {
    if (!r || !r.unknown || !r.unknown.length) continue;
    const res = await api("/api/agent/fields", { method: "POST", body: { fields: r.unknown, job_id: ctx.jobId || "", url: ctx.url || "" } }).catch(() => null);
    if (!res || !res.answers || !res.answers.length) continue;
    const [applied] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [frameId] }, func: applyFieldAnswers, args: [res.answers] });
    const names = (applied && applied.result) || [];
    out.ai.push(...names);
    out.todo = out.todo.filter((t) => !names.some((n) => t.startsWith(n.slice(0, 30))));
  }
  out.check.push(...out.ai);
  return out;
}
self.fillTab = fillTab;          // accessible aux tests

// ---------- candidature sur le site d'une entreprise ----------
async function waitLoaded(tabId) {
  for (let i = 0; i < 100; i++) {
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (!t) return false;
    if (t.status === "complete" && t.url && !t.url.startsWith("about:")) break;
    await sleep(300);
  }
  await sleep(1000);
  return true;
}

// Page d'offre : si le formulaire n'est pas affiché, le bouton « Postuler / Apply » (lien suivi dans le même onglet).
function openApplyForm() {
  const visible = (e) => e.offsetParent !== null;
  const fields = [...document.querySelectorAll("input[type=text], input[type=email], input:not([type]), input[type=file], textarea")].filter(visible);
  if (fields.length >= 3) return { form: true };
  // Choix intermédiaire (Workday : « Postuler manuellement » plutôt que l'import automatique du CV par le site).
  const manual = document.querySelector("[data-automation-id=applyManually]") ||
    [...document.querySelectorAll("a, button, [role=button]")].find((e) => visible(e) && /^(postuler manuellement|apply manually)/i.test((e.innerText || "").trim()));
  if (manual && visible(manual)) { manual.click(); return { clicked: true }; }
  const RE = /^(postuler|je postule|postuler maintenant|candidater|déposer (ma|une) candidature|je suis intéressé|intéressé|apply|apply now|apply for this (job|position)|start application|i'?m interested|candidature)/i;
  const el = [...document.querySelectorAll("a, button, [role=button]")].find((e) => visible(e) && RE.test((e.innerText || e.getAttribute("aria-label") || "").trim()));
  if (!el) return { form: false };
  const href = el.tagName === "A" && el.getAttribute("href");
  if (href && !href.startsWith("#") && !href.startsWith("javascript")) return { href: new URL(href, location.href).href };
  el.click();
  return { clicked: true };
}

// Champs visibles encore vides (y compris dans les composants) : sert à repérer une nouvelle étape du formulaire.
function countEmptyFields() {
  let n = 0;
  const walk = (root) => root.querySelectorAll("*").forEach((e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(e.tagName) && e.offsetParent !== null && !["hidden", "submit", "button", "password", "search", "checkbox", "radio"].includes(e.type) && !e.value) n++;
    if (e.shadowRoot) walk(e.shadowRoot);
  });
  walk(document);
  return n;
}

// Page de confirmation après l'envoi (preuve) : phrase repérée et adresse de la page.
function detectConfirmation() {
  const RE = /(merci (pour|de) (votre|ta) candidature|nous avons bien reçu (votre|ta) candidature|candidature (a bien été |a été |bien )?(envoyée|reçue|transmise|enregistrée)|thank you for (applying|your application|your interest)|(your )?application (has been |was )?(submitted|received|sent)|we('ve| have) received your application)/i;
  const m = (document.body.innerText || "").match(RE);
  return m ? `${m[0]} — ${location.href}` : null;
}

// ---------- agent navigateur : Claude pilote l'onglet ----------
async function observeAll(tabId) {
  const frames = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: observePage }).catch(() => []);
  const map = {}, obs = { url: "", title: "", elements: [], text: "" };
  frames.forEach((f, k) => {
    const r = f.result;
    if (!r) return;
    if (f.frameId === 0) Object.assign(obs, { url: r.url, title: r.title, text: r.text + obs.text });
    else if (r.elements.length) obs.text += "\n[cadre intégré] " + r.text.slice(0, 600);
    for (const el of r.elements) { const id = k * 1000 + el.id; map[id] = { frameId: f.frameId, local: el.id }; obs.elements.push({ ...el, id }); }
  });
  obs.elements = obs.elements.slice(0, 150);
  return { obs, map };
}

// Boucle observer → Claude décide → exécuter, jusqu'à « finish ». Renvoie { status, message, tabId }.
async function agentLoop(tabId, job, files, onNewPage) {
  const history = [];
  let seenUrl = "";
  for (let step = 0; step < 25; step++) {
    const url = (await chrome.tabs.get(tabId).catch(() => ({}))).url || "";
    if (url !== seenUrl && onNewPage) { seenUrl = url; await onNewPage(tabId).catch(() => {}); }   // passe rapide : règles, réseau, IA
    const { obs, map } = await observeAll(tabId);
    const res = await api("/api/agent/step", { method: "POST", body: { job_id: job.id, step, observation: obs, history } }).catch(() => null);
    if (!res || !res.actions) return { status: "need_user", message: "CandiPilot injoignable : termine toi-même.", tabId };
    let moved = false;
    for (const a of res.actions) {
      if (a.name === "finish") return { ...a.input, tabId };
      if (a.name === "wait") { await sleep(Math.min(10, Math.max(1, a.input.seconds || 2)) * 1000); history.push("attente"); continue; }
      const m = map[a.input.id];
      let r = "élément inconnu";
      if (m) {
        const [x] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [m.frameId] }, func: runAction,
          args: [{ ...a, input: { ...a.input, id: m.local } }, files, !!res.allow_submit] }).catch(() => []);
        r = (x && x.result) || "erreur";
      }
      history.push(`${a.name} ${JSON.stringify(a.input).slice(0, 140)} → ${r}`);
      if (a.name === "click" && r === "ok") { moved = true; break; }      // la page peut changer : on la relit
    }
    if (moved) {
      await sleep(1800);
      const opened = (await chrome.tabs.query({})).find((t) => t.openerTabId === tabId && t.url && !t.url.startsWith("chrome"));
      if (opened) tabId = opened.id;                                   // « Postuler » ouvert dans un nouvel onglet
    }
    await waitLoaded(tabId);
  }
  return { status: "need_user", message: "Trop d'étapes : termine toi-même la candidature.", tabId };
}

async function doApply(job) {
  const host = new URL(job.url).hostname;
  if (!(await chrome.permissions.contains({ origins: [new URL(job.url).origin + "/*"] })))
    return { status: "permission", detail: `Autorise CandiPilot sur ${host} : clique sur l'icône CandiPilot (en haut à droite de Chrome) puis « Autoriser les sites carrières », et clique « Reprendre l'envoi ».` };
  const profile = await api("/api/agent/profile");
  profile.answers = job.answers || {};
  const files = { cv: await fileB64(job.cv), letter: await fileB64(job.letter) };
  const beat = (text) => api(`/api/agent/jobs/${job.id}/progress`, { method: "POST", body: { text } }).catch(() => {});
  // Onglet visible : le candidat relit et envoie lui-même.
  let tab = await chrome.tabs.create({ url: job.url, active: true });
  await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  await waitLoaded(tab.id);
  // Jusqu'au formulaire : « Postuler », puis un éventuel choix intermédiaire (Workday : « Postuler manuellement »).
  for (let k = 0; k < 3; k++) {
    const step = await run(tab.id, openApplyForm).catch(() => null);
    if (!step || step.form || (!step.href && !step.clicked)) break;
    if (step.href) { await chrome.tabs.update(tab.id, { url: step.href }); await waitLoaded(tab.id); continue; }
    await sleep(2500);
    const opened = (await chrome.tabs.query({})).find((t) => t.openerTabId === tab.id);   // ouvert dans un nouvel onglet
    if (opened) tab = opened;
    await waitLoaded(tab.id);
  }
  const target = new URL((await chrome.tabs.get(tab.id)).url || job.url).origin;
  if (!(await chrome.permissions.contains({ origins: [target + "/*"] })))
    return { status: "permission", detail: `Le formulaire est sur ${new URL(target).hostname} : clique sur l'icône CandiPilot puis « Autoriser les sites carrières », et clique « Reprendre l'envoi ».` };
  const res = await fillFrames(tab.id, profile, files, { jobId: job.id });
  // L'agent IA prend la main pour tout le reste (boutons inattendus, étapes, listes, cases), jusqu'à l'envoi.
  const runAgent = async () => {
    const r = await agentLoop(tab.id, job, files, (id) => fillFrames(id, profile, files, { jobId: job.id }));
    if (r.tabId && r.tabId !== tab.id) tab = await chrome.tabs.get(r.tabId);
    if (r.status === "not_applicable") return r;
    await beat(r.status === "ready_to_submit" ? `Prêt : ${r.message} Vérifie, complète l'orange et clique « Envoyer ».`
      : `À toi : ${r.message} CandiPilot reprendra dès que la page change.`);
    return r;
  };
  const first = await runAgent();
  if (first.status === "not_applicable") return { status: "failed", detail: "Pas de formulaire de candidature trouvé : " + first.message };
  doApply.lastEmpty = (await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: countEmptyFields }).catch(() => []))
    .reduce((n, f) => n + (f.result || 0), 0);           // référence : seuls de NOUVEAUX champs relancent le remplissage
  const login = await run(tab.id, () => !![...document.querySelectorAll("input[type=password]")].find((e) => e.offsetParent !== null)).catch(() => false);
  await beat(login
    ? `${host} demande de créer un compte ou de te connecter : ton e-mail est prérempli, choisis ton mot de passe et valide. CandiPilot remplira ensuite chaque étape du formulaire.`
    : `Formulaire rempli (${res.filled.length + res.check.length} champs) : vérifie, complète l'orange et clique « Envoyer ».`);
  // Attente de la page de confirmation (20 min), avec signe de vie au serveur. Si de nouveaux champs apparaissent
  // (connexion / création de compte faite par le candidat, étape suivante), ils sont remplis à leur tour.
  let lastUrl = (await chrome.tabs.get(tab.id)).url;
  for (let i = 0; i < 400; i++) {
    await sleep(3000);
    const t = await chrome.tabs.get(tab.id).catch(() => null);
    if (!t) return { status: "uncertain", detail: "Onglet fermé avant la confirmation : si tu as envoyé la candidature, clique « Je l'ai envoyée »." };
    if (i % 3 === 2) {
      const empty = (await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: countEmptyFields }).catch(() => []))
        .reduce((n, f) => n + (f.result || 0), 0);
      if (empty >= 2 && (t.url !== lastUrl || empty > (doApply.lastEmpty || 0))) {
        lastUrl = t.url;
        await fillFrames(tab.id, profile, files, { jobId: job.id }).catch(() => null);
        await runAgent();                                  // nouvelle page / étape / après connexion : l'agent reprend
      }
      doApply.lastEmpty = empty;
    }
    const proof = await run(tab.id, detectConfirmation).catch(() => null);
    if (proof) return { status: "submitted", detail: `Envoyée par toi sur ${host} (formulaire rempli par CandiPilot)`, proof: "Page de confirmation : " + proof.slice(0, 250) };
    if (i % 20 === 19) await beat("En attente de ton clic sur « Envoyer »…");
  }
  return { status: "uncertain", detail: "Pas de page de confirmation repérée en 20 min : si tu as envoyé, clique « Je l'ai envoyée »." };
}
self.doApply = doApply;

// Formulaires intégrés (cadres) d'autres sites auxquels l'extension n'a pas encore accès (Workday, SmartRecruiters…).
// La fenêtre de l'extension demande alors l'autorisation à l'utilisateur, une fois par site, au clic sur « Remplir ».
async function framesNeedingAccess(tabId) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func: () =>
    [...document.querySelectorAll("iframe[src]")].map((f) => { try { return new URL(f.src, location.href).origin; } catch { return ""; } })
      .filter((o) => o.startsWith("https://") && o !== location.origin) });
  const out = [];
  for (const origin of new Set(res ? res.result : [])) {
    if (!(await chrome.permissions.contains({ origins: [origin + "/*"] }))) out.push(origin);
  }
  return out;
}
self.framesNeedingAccess = framesNeedingAccess;

// Installation en un clic : Chrome n'active pas une extension neuve dans les onglets déjà ouverts. On la relie
// tout de suite à l'onglet CandiPilot d'où l'utilisateur vient de cliquer « Ajouter à Chrome » (sans recharger).
async function injectLink() {
  const tabs = await chrome.tabs.query({ url: chrome.runtime.getManifest().content_scripts[0].matches });
  for (const t of tabs) await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["link.js"] }).catch(() => {});
  return tabs.length;
}
chrome.runtime.onInstalled.addListener(() => { injectLink().then(poll).catch(() => {}); });
self.injectLink = injectLink;

chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.create("sites", { periodInMinutes: 360 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "poll") poll(); if (a.name === "sites") refreshSites().catch(() => {}); });
chrome.runtime.onStartup.addListener(poll);
self.poll = poll;
