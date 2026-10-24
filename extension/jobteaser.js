// Robot JobTeaser : fonctions exécutées DANS la page JobTeaser de l'utilisateur (chrome.scripting).
// Chaque fonction est autonome (elle est copiée dans la page) : pas de variable extérieure.
// Fidèle à src/adapters/jobteaser.py et src/search.py (mêmes data-testid, mêmes règles).

export function jtLoggedIn() {
  return new Promise((resolve) => {
    // Lien ou bouton de connexion VISIBLE (le menu d'un compte connecté contient « Déconnexion », caché).
    const LOGIN = /^(se connecter|connexion|me connecter|log ?in|sign ?in|s'identifier)\b/i;
    const OUT = /d[ée]connexion|log ?out|sign ?out/i;
    const visible = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
    const check = () => {
      const cards = document.querySelectorAll('[data-testid="jobad-card"]').length;
      const pwd = [...document.querySelectorAll("input[type=password]")].filter(visible).length;
      const loginLink = [...document.querySelectorAll("a, button")].some((e) => {
        const t = (e.innerText || "").trim();
        return t.length < 40 && LOGIN.test(t) && !OUT.test(t) && visible(e);
      });
      return { cards, pwd, loginLink, url: location.href };
    };
    const t0 = Date.now();
    (function loop() {
      const s = check();
      if (s.cards || s.pwd || Date.now() - t0 > 9000)
        return resolve({ connected: s.cards > 0 && !s.pwd && !s.loginLink && location.pathname.includes("/job-offers"), ...s });
      setTimeout(loop, 400);
    })();
  });
}

export function jtListCards() {
  return new Promise((resolve) => {
    const t0 = Date.now();
    (function loop() {
      const cards = [...document.querySelectorAll('[data-testid="jobad-card"]')];
      if (cards.length || Date.now() - t0 > 15000) {
        return resolve(cards.map((c) => {
          const q = (s) => c.querySelector(s);
          const a = q('h3 a[href*="/job-offers/"]');
          return {
            href: a ? new URL(a.getAttribute("href"), location.href).href : "",
            title: a ? a.innerText.trim() : "",
            company: (q('[data-testid="jobad-card-company-name"]') || {}).innerText || "",
            contract: (q('[data-testid="jobad-card-contract"]') || {}).innerText || "",
            location: (q('[data-testid="jobad-card-location"]') || {}).innerText || "",
            easy: !!q('[data-testid="jobad-card-internal"]'),
          };
        }));
      }
      setTimeout(loop, 400);
    })();
  });
}

export function jtReadStart() {
  return new Promise((resolve) => {
    const SEL = '[data-testid="jobad-DetailView__CandidacyDetails__start_date"]';
    const MONTHS = { janvier: 1, "février": 2, fevrier: 2, mars: 3, avril: 4, mai: 5, juin: 6, juillet: 7, "août": 8, aout: 8,
      septembre: 9, octobre: 10, novembre: 11, "décembre": 12, decembre: 12 };
    const t0 = Date.now();
    (function loop() {
      const el = document.querySelector(SEL);
      if (el) {
        const raw = el.innerText.replace("Date de début", "").trim();
        const words = raw.toLowerCase().split(/\s+/);
        const month = words.map((w) => MONTHS[w]).find(Boolean);
        const year = words.find((w) => /^\d{4}$/.test(w));
        return resolve({ raw, iso: month && year ? `${year}-${String(month).padStart(2, "0")}-01` : "" });
      }
      if (Date.now() - t0 > 10000) return resolve({ raw: "", iso: "" });
      setTimeout(loop, 400);
    })();
  });
}

export function jtAlreadyApplied() {
  return new Promise((resolve) => setTimeout(() => resolve(document.body.innerText.includes("Vous avez postulé à cette offre")), 2500));
}

// Lit le formulaire sans rien modifier : { kind: already|external|easy|login|unknown, letter, questions, detail }.
export async function jtInspect() {
  const T = "jobad-DetailView__";
  const APPLY_INTERNAL = `[data-testid='${T}CandidateActions__Buttons_apply_internal_candidacy']`;
  const APPLY_EXTERNAL = `[data-testid='${T}CandidateActions__Buttons_apply_external_candidacy']`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(300); } return null; };
  await waitFor(() => document.querySelector(`${APPLY_INTERNAL}, ${APPLY_EXTERNAL}`) || document.body.innerText.includes("Vous avez postulé"), 8000);
  if (document.body.innerText.includes("Vous avez postulé à cette offre")) return { kind: "already", letter: false, questions: [], detail: "Déjà postulé (indiqué par JobTeaser)." };
  if (document.querySelector(APPLY_EXTERNAL) && !document.querySelector(APPLY_INTERNAL))
    return { kind: "external", letter: false, questions: [], detail: "Candidature externe (bouton ↗) — à faire sur le site de l'entreprise." };
  const btn = document.querySelector(APPLY_INTERNAL) || [...document.querySelectorAll("button")].find((b) => b.innerText.includes("Candidature simplifiée"));
  if (!btn) {
    const login = document.querySelector("input[type=password]") || /connect\.jobteaser|login|sign_in/.test(location.href);
    return { kind: login ? "login" : "unknown", letter: false, questions: [],
      detail: login ? "Connexion JobTeaser expirée." : "Bouton « Candidature simplifiée » introuvable." };
  }
  // Le bouton est affiché avant que la page réagisse aux clics : on attend, puis on réessaie si besoin.
  await sleep(2500);
  let dialog = null;
  for (let i = 0; i < 4 && !dialog; i++) { btn.click(); dialog = await waitFor(() => document.querySelector("section[role='dialog']"), 4000); }
  if (!dialog) return { kind: "unknown", letter: false, questions: [], detail: "Le panneau « Postuler à l'offre » ne s'est pas ouvert." };
  await sleep(1000);
  const questions = [...dialog.querySelectorAll("[data-testid='ScreeningQuestions'] [data-testid^='input-wrapper-'] label")]
    .map((l) => (l.textContent || "").trim()).filter(Boolean);
  return { kind: "easy", letter: !!dialog.querySelector("input[type='file'][name='coverLetterContent']"), questions, detail: "Formulaire lu." };
}

// Remplit et (en mode auto) envoie : { status: submitted|skipped|filled_dry|uncertain|failed|login, detail }.
export async function jtApply(job) {
  const T = "jobad-DetailView__";
  const APPLY_INTERNAL = `[data-testid='${T}CandidateActions__Buttons_apply_internal_candidacy']`;
  const APPLY_EXTERNAL = `[data-testid='${T}CandidateActions__Buttons_apply_external_candidacy']`;
  const USE_OTHER_CV = `[data-testid='${T}ApplicationFlow__Buttons__use_another_resume']`;
  const APPLY_BUTTON = `[data-testid='${T}ApplicationFlow__Buttons__apply_button']`;
  const CONTINUE = `[data-testid='${T}ApplicationFlow__Buttons__continue_button']`;
  const SCREENING = "[data-testid='ScreeningQuestions'] [data-testid^='input-wrapper-']";
  const GENDERS = { homme: "GENDER_MALE", masculin: "GENDER_MALE", femme: "GENDER_FEMALE", "féminin": "GENDER_FEMALE", autre: "GENDER_OTHER" };
  const SUCCESS = ["vous avez postulé à cette offre", "candidature a été envoyée", "candidature envoyée", "merci pour votre candidature", "application has been sent"];
  const norm = (t) => (t || "").replace(/\s+/g, " ").trim().toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(300); } return null; };
  const press = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true })); el.click(); };
  const setValue = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const setFile = (input, f) => {
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer(); dt.items.add(new File([bytes], f.name, { type: "application/pdf" }));
    input.files = dt.files;
    input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const succeeded = () => SUCCESS.some((s) => norm(document.body.innerText).includes(s));
  const filled = [];

  await waitFor(() => document.querySelector(`${APPLY_INTERNAL}, ${APPLY_EXTERNAL}`) || succeeded(), 8000);
  if (document.body.innerText.includes("Vous avez postulé à cette offre")) return { status: "skipped", detail: "Déjà postulé (indiqué par JobTeaser)." };
  if (document.querySelector(APPLY_EXTERNAL) && !document.querySelector(APPLY_INTERNAL)) return { status: "failed", detail: "Candidature externe (bouton ↗) — à faire par toi." };
  const btn = document.querySelector(APPLY_INTERNAL) || [...document.querySelectorAll("button")].find((b) => b.innerText.includes("Candidature simplifiée"));
  if (!btn) {
    const login = document.querySelector("input[type=password]") || /connect\.jobteaser|login|sign_in/.test(location.href);
    return { status: login ? "login" : "failed", detail: login ? "Connexion JobTeaser expirée : reconnecte-toi puis reprends." : "Bouton « Candidature simplifiée » introuvable." };
  }
  await sleep(2500);       // la page doit être prête à réagir au clic
  let dialog = null;
  for (let i = 0; i < 4 && !dialog; i++) { press(btn); dialog = await waitFor(() => document.querySelector("section[role='dialog']"), 4000); }
  if (!dialog) return { status: "failed", detail: "Le panneau « Postuler à l'offre » ne s'est pas ouvert." };
  await sleep(1500);

  // 1) Genre (JobTeaser le redemande) : valeur du profil, jamais devinée.
  const gender = dialog.querySelector("[data-testid='hiddenInput-gender']");
  if (gender && !(gender.value || "").trim()) {
    const value = GENDERS[norm(job.gender)];
    if (!value) return { status: "failed", detail: "Genre non renseigné : choisis-le dans ton profil (Homme / Femme / Autre)." };
    const combo = dialog.querySelector("#gender[role='combobox']");
    if (!combo) return { status: "failed", detail: "Champ « genre » introuvable." };
    press(combo);
    const opt = await waitFor(() => document.querySelector(`#gender-dropdown [data-value='${value}']`), 5000);
    if (!opt) return { status: "failed", detail: "Impossible de choisir le genre." };
    press(opt);
    await sleep(500);
    const save = [...dialog.querySelectorAll("button")].find((b) => b.innerText.includes("Sauvegarder les informations"));
    if (save) press(save);
    await sleep(2500);
    const g = dialog.querySelector("[data-testid='hiddenInput-gender']");
    if (g && !(g.value || "").trim()) return { status: "failed", detail: "Genre non pris en compte par JobTeaser." };
    filled.push("genre");
  }

  // 2) Questions du recruteur : seulement avec les réponses données par le candidat.
  const labels = [...dialog.querySelectorAll(`${SCREENING} label`)].map((l) => (l.textContent || "").trim()).filter(Boolean);
  const answers = Object.fromEntries(Object.entries(job.answers || {}).filter(([, v]) => String(v).trim()).map(([k, v]) => [norm(k), String(v)]));
  const missing = labels.filter((q) => !(norm(q) in answers));
  if (missing.length && job.mode === "auto") return { status: "failed", detail: "Questions du recruteur sans réponse : " + missing.join(" / ") };

  // 3) CV du candidat à la place de celui de JobTeaser.
  if (job.cv) {
    const other = dialog.querySelector(USE_OTHER_CV);
    if (other) press(other);
    const input = await waitFor(() => dialog.querySelector("input[type='file'][name='resume']"), 6000);
    if (!input) return { status: "failed", detail: "Champ du CV introuvable." };
    setFile(input, job.cv);
    await sleep(3000);
    if (!dialog.innerText.includes(job.cv.name.slice(0, 20))) return { status: "failed", detail: `CV envoyé mais « ${job.cv.name} » n'apparaît pas dans le panneau.` };
    filled.push("CV");
  }

  // 4) Lettre de motivation (PDF) si le recruteur la demande.
  const cover = dialog.querySelector("input[type='file'][name='coverLetterContent']");
  if (cover) {
    if (!job.letter) return { status: "failed", detail: "Le recruteur demande une lettre de motivation : aucune lettre préparée." };
    setFile(cover, job.letter);
    await sleep(3000);
    filled.push(`lettre (${job.letter.name})`);
  }

  // 5) Étape des questions.
  if (labels.length) {
    const cont = dialog.querySelector(CONTINUE);
    if (!cont || cont.disabled) return { status: "failed", detail: "Bouton « Continuer » inactif (champ obligatoire manquant ?)" };
    press(cont);
    await sleep(2500);
    const unanswered = [];
    for (const w of dialog.querySelectorAll(SCREENING)) {
      const label = ((w.querySelector("label") || {}).textContent || "").trim();
      const answer = answers[norm(label)];
      if (!answer) { unanswered.push(label); continue; }
      const ta = w.querySelector("textarea"), combo = w.querySelector("[role='combobox']"),
        inp = w.querySelector("input:not([type='hidden']):not([tabindex='-1'])");
      if (ta) setValue(ta, answer);
      else if (combo) {
        press(combo);
        const o = await waitFor(() => [...document.querySelectorAll("[role='option']")].find((x) => norm(x.innerText).includes(norm(answer))), 4000);
        if (o) press(o); else unanswered.push(label);
      } else if (inp) setValue(inp, answer);
      else unanswered.push(label);
    }
    filled.push(`${labels.length - unanswered.length} réponse(s)`);
    if (unanswered.length && job.mode === "auto") return { status: "failed", detail: "Réponses non saisies : " + unanswered.join(" / ") };
  }

  const detail = "rempli : " + (filled.join(", ") || "rien à remplir");
  if (job.mode === "assisted") return { status: "filled_dry", detail: detail + " ; à toi de cliquer « Postuler maintenant »" };
  const final = dialog.querySelector(APPLY_BUTTON);
  if (!final || final.offsetParent === null) return { status: "failed", detail: detail + " ; bouton « Postuler maintenant » absent (étape supplémentaire ?)" };
  if (final.disabled) return { status: "failed", detail: detail + " ; « Postuler maintenant » inactif (champ obligatoire manquant ?)" };
  press(final);
  const ok = await waitFor(succeeded, 8000);
  return ok ? { status: "submitted", detail: detail + " ; envoyé" }
    : { status: "uncertain", detail: detail + " ; envoi non confirmé par JobTeaser — vérifie dans « Mes candidatures »." };
}

export function jtSucceeded() {
  const SUCCESS = ["vous avez postulé à cette offre", "candidature a été envoyée", "candidature envoyée", "merci pour votre candidature", "application has been sent"];
  const text = (document.body.innerText || "").toLowerCase();
  return SUCCESS.some((s) => text.includes(s));
}
