// Remplissage de N'IMPORTE QUEL formulaire de candidature (Workday, Greenhouse, Lever, site d'entreprise…).
// Exécuté dans la page (chrome.scripting, dans chaque cadre) : autonome. N'envoie JAMAIS le formulaire et ne clique
// sur rien. Lot 4 :
// - adaptateurs Greenhouse et Lever (champs connus avec certitude) ;
// - champs dans les composants (shadow DOM ouvert) ;
// - confiance par champ : sûr (vert), rempli mais à vérifier (orange pointillé), laissé au candidat (orange) ;
// - formulaires en plusieurs étapes : les champs qui apparaissent ensuite (après « Suivant », cliqué par le
//   candidat) sont remplis automatiquement pendant 15 minutes.

export function fillForm(profile, files) {
  const SURE = 0.85, MAYBE = 0.6;     // seuils de confiance : remplir sans réserve / remplir mais faire vérifier
  const RULES = [
    ["linkedin", /linkedin/], ["email", /e-?mail|courriel/], ["phone", /phone|t[ée]l[ée]phone|mobile|portable/],
    ["first_name", /first.?name|given.?name|pr[ée]nom|fname/],
    ["last_name", /last.?name|family.?name|surname|nom de famille|lname|\bnom\b/],
    ["full_name", /full.?name|nom complet|\bname\b/],
    ["cv", /\bcv\b|resume|résumé|curriculum/], ["cover_text", /cover|motivation|lettre|why|pourquoi/],
    ["gender", /gender|genre|civilit|sexe|salutation/], ["availability", /availab|disponib|start.?date|date de d[ée]but/],
    ["school", /school|[ée]cole|universit|dipl[oô]me|degree|education/], ["city", /city|ville|address|adresse|location/],
  ];
  const AUTOCOMPLETE = { "given-name": "first_name", "family-name": "last_name", name: "full_name", email: "email", tel: "phone",
    "tel-national": "phone", "address-level2": "city", url: "" };
  // Adaptateurs : sélecteur → rôle, pour les plateformes dont le formulaire est connu.
  const ADAPTERS = {
    greenhouse: {
      match: () => /greenhouse\.io$/.test(location.hostname) || !!document.querySelector("#first_name") && !!document.querySelector("#resume, [name^='job_application']"),
      fields: [["#first_name, [name='job_application[first_name]']", "first_name"], ["#last_name, [name='job_application[last_name]']", "last_name"],
        ["#email, [name='job_application[email]']", "email"], ["#phone, [name='job_application[phone]']", "phone"],
        ["input[type=file]#resume, input[type=file][name*='resume']", "cv"], ["input[type=file]#cover_letter, input[type=file][name*='cover_letter']", "cover_file"],
        ["#candidate-location, [name='job_application[location]']", "city"]],
    },
    lever: {
      match: () => /lever\.co$/.test(location.hostname) || !!document.querySelector("input[name='urls[LinkedIn]']") && !!document.querySelector("input[name=org]"),
      fields: [["input[name=name]", "full_name"], ["input[name=email]", "email"], ["input[name=phone]", "phone"],
        ["input[name=location]", "city"], ["input[name='urls[LinkedIn]']", "linkedin"], ["input[type=file][name=resume]", "cv"]],
    },
  };
  const COVER_FILE = /(cover|motivation|lettre)/;
  const NEXT = /^(suivant|continuer|next|continue|étape suivante|save and continue)\b/i;
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();

  // Tous les champs, y compris ceux des composants (shadow DOM ouvert).
  const allFields = (root, out = []) => {
    root.querySelectorAll("input, select, textarea").forEach((el) => out.push(el));
    root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) allFields(el.shadowRoot, out); });
    return out;
  };
  const labelOf = (el) => {
    const root = el.getRootNode(), strong = [], weak = [];
    if (el.id && root.querySelector) { const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) strong.push(l.innerText || l.textContent); }
    const wrap = el.closest("label"); if (wrap) strong.push(wrap.innerText || wrap.textContent);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const e = (root.getElementById ? root : document).getElementById(id); if (e) strong.push(e.innerText || e.textContent); });
    strong.push(el.getAttribute("aria-label"));
    weak.push(el.getAttribute("placeholder"), el.name, el.id);
    return { strong: clean(strong.filter(Boolean).join(" | ")), weak: clean(weak.filter(Boolean).join(" | ")) };
  };
  const adapter = Object.entries(ADAPTERS).find(([, a]) => a.match());
  const known = new Map();       // champ → rôle sûr (adaptateur)
  if (adapter) for (const [sel, r] of adapter[1].fields) document.querySelectorAll(sel).forEach((el) => known.set(el, r));

  // Rôle du champ et confiance (0 à 1).
  const guess = (el) => {
    if (known.has(el)) return [known.get(el), 1];
    const { strong, weak } = labelOf(el), s = strong.toLowerCase(), w = weak.toLowerCase();
    if (el.type === "file") return [COVER_FILE.test(s + " " + w) ? "cover_file" : "cv", strong ? 0.9 : 0.75];
    if (el.type === "checkbox" || el.type === "radio") return ["choice", 1];
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).pop();
    if (AUTOCOMPLETE[ac]) return [AUTOCOMPLETE[ac], 0.95];
    if (el.type === "email" || el.type === "tel") return [el.type === "email" ? "email" : "phone", 0.95];
    for (const [text, conf] of [[s, 0.9], [w, 0.7]]) {
      if (!text) continue;
      for (const [r, re] of RULES) if (re.test(text)) return [r === "cover_text" && el.tagName !== "TEXTAREA" ? "other" : r, conf];
    }
    return ["other", 0];
  };
  const setValue = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true })); el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };
  const setFile = (el, f) => {
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer(); dt.items.add(new File([bytes], f.name, { type: "application/pdf" }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true })); el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  };
  // Vert : sûr. Orange pointillé : rempli, à vérifier. Orange plein : à compléter par le candidat.
  const mark = (el, how) => {
    const target = el.type === "file" && el.offsetParent === null ? el.parentElement || el : el;
    target.style.outline = how === "ok" ? "3px solid #12B89A" : how === "check" ? "3px dashed #F5A524" : "3px solid #F5A524";
    target.style.outlineOffset = "2px";
  };
  const values = { first_name: profile.first_name, last_name: profile.last_name, full_name: profile.full_name, email: profile.email,
    phone: profile.phone, linkedin: profile.linkedin, city: profile.city, school: profile.school, availability: profile.availability };
  const GENDER = { homme: /^(m|mr|m\.|monsieur|homme|male|man)$/i, femme: /^(mme|mrs|ms|madame|femme|female|woman)$/i, autre: /autre|other|non/i };
  const usable = (el) => !["hidden", "submit", "button", "reset", "image", "password", "search"].includes(el.type) && !el.disabled
    && (el.offsetParent !== null || el.type === "file");      // les plateformes cachent souvent le champ du CV derrière un bouton

  window.__candipilotSeen = new WeakSet();     // un nouveau clic sur « Remplir » reprend tous les champs
  const report = { filled: [], check: [], todo: [], total: 0, platform: adapter ? adapter[0] : "", steps: false, watching: false };
  const fillOne = (el) => {
    const [r, conf] = guess(el), { strong, weak } = labelOf(el), label = (strong || weak).split(" | ")[0] || r;
    const done = (how, name) => { mark(el, how); (how === "ok" ? report.filled : report.check).push(name || label); };
    if (el.tagName !== "SELECT" && !["file", "checkbox", "radio"].includes(el.type) && el.value) return done("ok");
    if (r === "choice") { mark(el, "todo"); return report.todo.push(label + " (à cocher par toi)"); }
    if (conf < MAYBE) { mark(el, "todo"); return report.todo.push(label); }
    const how = conf >= SURE ? "ok" : "check";
    if (r === "cv" && files.cv) { setFile(el, files.cv); return done(how, "CV"); }
    if (r === "cover_file" && files.letter) { setFile(el, files.letter); return done(how, "lettre de motivation"); }
    const g = GENDER[(profile.gender || "").toLowerCase()];
    if (r === "gender" && el.tagName === "SELECT" && g) {
      const opt = [...el.options].find((o) => g.test(clean(o.text)) || g.test(o.value));
      if (opt) { setValue(el, opt.value); return done(how); }
    } else if (values[r] && el.tagName !== "SELECT") { setValue(el, values[r]); return done(how); }
    mark(el, "todo"); report.todo.push(label);
  };
  const pass = () => {
    let n = 0;
    for (const el of allFields(document)) {
      const seen = window.__candipilotSeen;
      if (seen.has(el) || !usable(el)) continue;
      seen.add(el); n++; fillOne(el);
    }
    return n;
  };
  report.total = pass();
  report.steps = [...document.querySelectorAll("button, [role=button], input[type=button]")].some((b) => NEXT.test(clean(b.innerText || b.value)));

  // Étapes suivantes : remplir les nouveaux champs dès qu'ils apparaissent (le candidat clique lui-même sur « Suivant »).
  if (!window.__candipilotObserver && document.body) {
    let t;
    const obs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(pass, 400); });
    obs.observe(document.body, { childList: true, subtree: true });
    window.__candipilotObserver = obs;
    setTimeout(() => { obs.disconnect(); window.__candipilotObserver = null; }, 15 * 60 * 1000);
  }
  report.watching = true;
  return report;
}
