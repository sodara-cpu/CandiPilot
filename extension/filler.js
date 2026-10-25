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
    workday: {
      match: () => /myworkday(jobs|site)\.com$/.test(location.hostname) || !!document.querySelector("[data-automation-id^='legalNameSection']"),
      fields: [["[data-automation-id='legalNameSection_firstName']", "first_name"], ["[data-automation-id='legalNameSection_lastName']", "last_name"],
        ["[data-automation-id='email']", "email"], ["[data-automation-id='phone-number']", "phone"],
        ["[data-automation-id='addressSection_city']", "city"], ["[data-automation-id='linkedinQuestion']", "linkedin"],
        ["input[type=file][data-automation-id='file-upload-input-ref']", "cv"]],
    },
    smartrecruiters: {
      match: () => /smartrecruiters\.com$/.test(location.hostname) || !!document.querySelector("#first-name-input"),
      fields: [["#first-name-input", "first_name"], ["#last-name-input", "last_name"], ["#email-input, #confirm-email-input", "email"],
        ["#phone-number-input", "phone"], ["#linkedin-input", "linkedin"], ["#location-input, #city-input", "city"]],
    },
    workable: {
      match: () => /workable\.com$/.test(location.hostname) || !!document.querySelector("form[data-ui='application-form']"),
      fields: [["input[name=firstname]", "first_name"], ["input[name=lastname]", "last_name"], ["input[name=email]", "email"],
        ["input[name=phone]", "phone"], ["input[name=address]", "city"], ["[data-ui='resume'] input[type=file], input[type=file][name=resume]", "cv"]],
    },
    ashby: {
      match: () => /ashbyhq\.com$/.test(location.hostname) || !!document.querySelector("[name='_systemfield_name']"),
      fields: [["[name='_systemfield_name']", "full_name"], ["[name='_systemfield_email']", "email"], ["[name='_systemfield_phone']", "phone"],
        ["[name='_systemfield_location']", "city"], ["input[type=file][name='_systemfield_resume'], #_systemfield_resume", "cv"]],
    },
    teamtailor: {
      match: () => /teamtailor\.com$/.test(location.hostname) || !!document.querySelector("[name='candidate[first_name]']"),
      fields: [["[name='candidate[first_name]']", "first_name"], ["[name='candidate[last_name]']", "last_name"],
        ["[name='candidate[email]']", "email"], ["[name='candidate[phone]']", "phone"], ["[name='candidate[linkedin_url]']", "linkedin"],
        ["input[type=file][name='candidate[resume]']", "cv"], ["input[type=file][name*='cover_letter']", "cover_file"]],
    },
    wttj: {        // formulaire propre à Welcome to the Jungle (sinon, il renvoie vers celui de l'entreprise)
      match: () => /welcometothejungle\.com$/.test(location.hostname),
      fields: [["input[name=firstname], input[name=first_name]", "first_name"], ["input[name=lastname], input[name=last_name]", "last_name"],
        ["input[name=email]", "email"], ["input[name=phone]", "phone"], ["input[type=file][name=resume], input[type=file][name=cv]", "cv"],
        ["input[type=file][name=cover_letter]", "cover_file"]],
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
  const roots = (root, out = [root]) => { root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) roots(el.shadowRoot, out); }); return out; };
  if (adapter) for (const root of roots(document)) for (const [sel, r] of adapter[1].fields) root.querySelectorAll(sel).forEach((el) => known.set(el, r));

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
  const nq = (t) => clean(t).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  const ANSWERS = Object.entries(profile.answers || {}).map(([q, a]) => [nq(q), a]).filter(([q, a]) => q.length >= 6 && a);
  const answerFor = (label) => { const l = nq(label); const hit = ANSWERS.find(([q]) => l.includes(q) || (l.length >= 6 && q.includes(l))); return hit ? hit[1] : ""; };
  // Champs pièges pour robots (« for robots only, do not enter ») : jamais touchés, sinon le site nous prend pour un robot.
  const HONEYPOT = /robots? only|do not (enter|fill)|leave (this )?(field )?(blank|empty)|ne pas remplir|honeypot|beecatcher|hp[_-]?field/i;
  const trap = (el) => HONEYPOT.test([labelOf(el).strong, el.name, el.id, el.getAttribute("data-automation-id") || "", el.getAttribute("autocomplete") === "off" && el.tabIndex < 0 ? "honeypot" : ""].join(" "));
  const usable = (el) => !["hidden", "submit", "button", "reset", "image", "password", "search"].includes(el.type) && !el.disabled
    && (el.offsetParent !== null || el.type === "file") && !trap(el);      // le champ du CV est souvent caché derrière un bouton

  window.__candipilotSeen = new WeakSet();     // un nouveau clic sur « Remplir » reprend tous les champs
  const report = { filled: [], check: [], todo: [], total: 0, platform: adapter ? adapter[0] : "", steps: false, watching: false, unknown: [] };
  // Champs non compris : décrits (intitulé, type, choix) pour l'agent IA. Jamais les consentements ni déclarations.
  const CONSENT = /consent|accept|j'accepte|certif|atteste|déclar|declare|terms|conditions|privacy|confidentialit|rgpd|gdpr|true and complete|exactitude|newsletter|marketing/i;
  window.__cpUnknown = [];
  const question = (el) => {
    const box = el.closest("fieldset, [role=radiogroup], [role=group], [class*=question], [class*=Question], [class*=field], [class*=Field]");
    const legend = box && (box.querySelector("legend, [class*=label], [class*=Label], label") || box);
    return clean((legend && (legend.innerText || legend.textContent)) || "").split("\n")[0].slice(0, 200);
  };
  const seenGroups = new Set();
  const describe = (el, label) => {
    if (el.type === "file" || el.type === "checkbox") return;
    let d;
    if (el.type === "radio") {
      if (!el.name || seenGroups.has(el.name)) return;
      seenGroups.add(el.name);
      const radios = [...el.getRootNode().querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)];
      d = { label: question(el) || label, type: "radio", options: radios.map((r) => clean(labelOf(r).strong || r.value)).slice(0, 20), els: radios };
    } else if (el.tagName === "SELECT") {
      d = { label, type: "select", options: [...el.options].map((o) => clean(o.text)).filter(Boolean).slice(0, 60), els: [el] };
    } else {
      // Intitulé trop pauvre (« Start typing… », « Pick date… ») : la question affichée autour du champ.
      const poor = label.length <= 3 || /^(start typing|pick|select|choose|choisir|sélectionne|tape|type here|search)/i.test(label);
      const q = poor ? question(el) : "";
      d = { label: q && q !== label ? `${q} (${label})` : label, type: el.tagName === "TEXTAREA" ? "textarea" : el.type === "date" ? "date" : "text", options: [], els: [el] };
    }
    if (!d.label || CONSENT.test(d.label)) return;
    const id = window.__cpUnknown.push(d) - 1;
    const { strong } = labelOf(el);
    report.unknown.push({ id, label: d.label, type: d.type, options: d.options, required: !!el.required || el.getAttribute("aria-required") === "true",
      // pour le réseau de neurones des champs (apprentissage/) : mêmes informations qu'à l'entraînement
      tag: el.tagName.toLowerCase(), input_type: el.type || "", name: el.name || "", html_id: el.id || "",
      placeholder: el.getAttribute("placeholder") || "", aria_label: el.getAttribute("aria-label") || "",
      autocomplete: el.getAttribute("autocomplete") || "", field_label: strong.split(" | ")[0] || "", context: question(el) });
  };
  const fillOne = (el) => {
    const [r, conf] = guess(el), { strong, weak } = labelOf(el), label = (strong || weak).split(" | ")[0] || r;
    const done = (how, name) => { mark(el, how); (how === "ok" ? report.filled : report.check).push(name || label); };
    if (el.tagName !== "SELECT" && !["file", "checkbox", "radio"].includes(el.type) && el.value) return done("ok");
    if (r === "choice") { mark(el, "todo"); describe(el, label); return report.todo.push(label + " (à cocher par toi)"); }
    if (conf < MAYBE) { mark(el, "todo"); describe(el, strong || weak); return report.todo.push(label); }
    const how = conf >= SURE ? "ok" : "check";
    if (r === "cv" && files.cv) { setFile(el, files.cv); return done(how, "CV"); }
    if (r === "cover_file" && files.letter) { setFile(el, files.letter); return done(how, "lettre de motivation"); }
    const g = GENDER[(profile.gender || "").toLowerCase()];
    if (r === "gender" && el.tagName === "SELECT" && g) {
      const opt = [...el.options].find((o) => g.test(clean(o.text)) || g.test(o.value));
      if (opt) { setValue(el, opt.value); return done(how); }
    } else if (values[r] && el.tagName !== "SELECT") { setValue(el, values[r]); return done(how); }
    // Question du recruteur à laquelle le candidat a déjà répondu dans CandiPilot : réponse reprise (à vérifier).
    const ans = answerFor(strong || weak);
    if (ans && (el.tagName === "TEXTAREA" || el.type === "text" || !el.type)) { setValue(el, ans); return done("check"); }
    mark(el, "todo"); describe(el, strong || weak); report.todo.push(label);
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


// Réponses de l'agent IA aux champs non compris (même cadre que fillForm) : valeur saisie ou choix coché, marqué
// « à vérifier ». Rien n'est envoyé.
export function applyFieldAnswers(answers) {
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim().toLowerCase();
  const setValue = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true })); el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };
  const mark = (el) => { el.style.outline = "3px dashed #6D5EFC"; el.style.outlineOffset = "2px"; };
  const labelText = (r) => clean((r.id && r.getRootNode().querySelector(`label[for="${CSS.escape(r.id)}"]`)?.innerText) || r.closest("label")?.innerText || r.value);
  const who = (a) => (String(a.source || "").startsWith("réseau") ? " (réseau de neurones)" : " (Claude)");
  const done = [];
  for (const a of answers || []) {
    const d = (window.__cpUnknown || [])[a.id];
    if (!d || !a.value) continue;
    const v = clean(a.value);
    if (d.type === "radio") {
      const r = d.els.find((x) => labelText(x) === v) || d.els.find((x) => labelText(x).includes(v) || v.includes(labelText(x)));
      if (r && !r.checked) { r.click(); mark(r.parentElement || r); done.push(d.label + who(a)); }
    } else if (d.type === "select") {
      const el = d.els[0], o = [...el.options].find((x) => clean(x.text) === v) || [...el.options].find((x) => clean(x.text).includes(v));
      if (o) { setValue(el, o.value); mark(el); done.push(d.label + who(a)); }
    } else if (!d.els[0].value) { setValue(d.els[0], a.value); mark(d.els[0]); done.push(d.label + who(a)); }
  }
  return done;
}
