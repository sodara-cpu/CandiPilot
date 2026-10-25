// CandiPilot — robot de candidature qui travaille dans le navigateur de l'utilisateur, avec SES sessions.
// 1. Liaison automatique : link.js lui transmet un jeton dès que l'utilisateur ouvre CandiPilot connecté.
// 2. Toutes les 30 s, il demande ses tâches au serveur et les exécute dans un onglet « robot ».
// 3. Il signale sur quels sites l'utilisateur est connecté (JobTeaser, LinkedIn, Welcome to the Jungle).
import { jtLoggedIn, jtListCards, jtReadStart, jtInspect, jtApply, jtSucceeded, jtAlreadyApplied } from "./jobteaser.js";
import { fillForm } from "./filler.js";

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
  await api(`/api/agent/jobs/${job.id}/progress`, { method: "POST", body: { text: `Lecture des dates de début (${cands.length} offres)` } });
  for (const o of cands) {
    tab = await robotTab(o.url);
    const { iso } = await run(tab, jtReadStart);
    if (job.start_from && (!iso || iso < job.start_from)) { stats.date++; continue; }
    found.push({ ...o, start_date: iso });
  }
  return { found, stats };
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
  // Dans chaque cadre de la page : les formulaires Greenhouse ou Lever sont souvent intégrés dans un cadre (iframe).
  const frames = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: fillForm, args: [profile, files] });
  const out = { filled: [], check: [], todo: [], total: 0, platform: "", steps: false, watching: false };
  for (const { result: r } of frames) {
    if (!r) continue;
    for (const k of ["filled", "check", "todo"]) out[k].push(...r[k]);
    out.total += r.total; out.platform ||= r.platform; out.steps ||= r.steps; out.watching ||= r.watching;
  }
  return out;
}
self.fillTab = fillTab;          // accessible aux tests

chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.create("sites", { periodInMinutes: 360 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "poll") poll(); if (a.name === "sites") refreshSites().catch(() => {}); });
chrome.runtime.onStartup.addListener(poll);
self.poll = poll;
