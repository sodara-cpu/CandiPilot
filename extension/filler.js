// Remplissage de N'IMPORTE QUEL formulaire de candidature (Workday, Greenhouse, Lever, site d'entreprise…).
// Exécuté dans la page (chrome.scripting) : autonome. Reconnaît le rôle de chaque champ (mêmes règles que
// apprentissage/etiquettes.py), remplit ce qu'il connaît, surligne le reste. N'envoie JAMAIS le formulaire.

export function fillForm(profile, files) {
  const RULES = [
    ["linkedin", /linkedin/], ["email", /e-?mail|courriel/], ["phone", /phone|t[ée]l[ée]phone|mobile|portable/],
    ["first_name", /first.?name|given.?name|pr[ée]nom|fname/],
    ["last_name", /last.?name|family.?name|surname|nom de famille|lname|\bnom\b/],
    ["full_name", /full.?name|nom complet|\bname\b/],
    ["cv", /\bcv\b|resume|résumé|curriculum/], ["cover_text", /cover|motivation|lettre|why|pourquoi/],
    ["gender", /gender|genre|civilit|sexe|salutation/], ["availability", /availab|disponib|start.?date|date de d[ée]but/],
    ["school", /school|[ée]cole|universit|dipl[oô]me|degree|education/], ["city", /city|ville|address|adresse|location/],
  ];
  const COVER_FILE = /(cover|motivation|lettre)/;
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const labelOf = (el) => {
    const parts = [];
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) parts.push(l.innerText); }
    const wrap = el.closest("label"); if (wrap) parts.push(wrap.innerText);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const e = document.getElementById(id); if (e) parts.push(e.innerText); });
    parts.push(el.getAttribute("aria-label"), el.getAttribute("placeholder"), el.name, el.id, el.getAttribute("autocomplete"));
    return clean(parts.filter(Boolean).join(" | "));
  };
  const role = (el) => {
    const text = labelOf(el).toLowerCase();
    if (el.type === "file") return COVER_FILE.test(text) ? "cover_file" : "cv";
    if (el.type === "checkbox" || el.type === "radio") return "choice";
    for (const [r, re] of RULES) if (re.test(text)) return r === "cover_text" && el.tagName !== "TEXTAREA" ? "other" : r;
    return "other";
  };
  const setValue = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };
  const setFile = (el, f) => {
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer(); dt.items.add(new File([bytes], f.name, { type: "application/pdf" }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const mark = (el, ok) => { el.style.outline = ok ? "3px solid #12B89A" : "3px solid #F5A524"; el.style.outlineOffset = "2px"; };
  const values = { first_name: profile.first_name, last_name: profile.last_name, full_name: profile.full_name, email: profile.email,
    phone: profile.phone, linkedin: profile.linkedin, city: profile.city, school: profile.school, availability: profile.availability };
  const GENDER = { homme: /^(m|mr|m\.|monsieur|homme|male|man)$/i, femme: /^(mme|mrs|ms|madame|femme|female|woman)$/i, autre: /autre|other|non/i };

  const fields = [...document.querySelectorAll("input, select, textarea")].filter((el) =>
    !["hidden", "submit", "button", "reset", "image", "password", "search"].includes(el.type) && !el.disabled && el.offsetParent !== null);
  const filled = [], todo = [];
  for (const el of fields) {
    const r = role(el), label = labelOf(el).split(" | ")[0] || r;
    if (el.tagName !== "SELECT" && el.type !== "file" && el.type !== "checkbox" && el.type !== "radio" && el.value) { filled.push(label); mark(el, true); continue; }
    if (r === "cv" && files.cv) { setFile(el, files.cv); filled.push("CV"); mark(el, true); }
    else if (r === "cover_file" && files.letter) { setFile(el, files.letter); filled.push("lettre de motivation"); mark(el, true); }
    else if (r === "gender" && el.tagName === "SELECT" && GENDER[(profile.gender || "").toLowerCase()]) {
      const opt = [...el.options].find((o) => GENDER[(profile.gender || "").toLowerCase()].test(clean(o.text)) || GENDER[(profile.gender || "").toLowerCase()].test(o.value));
      if (opt) { setValue(el, opt.value); filled.push(label); mark(el, true); } else { todo.push(label); mark(el, false); }
    } else if (values[r]) { setValue(el, values[r]); filled.push(label); mark(el, true); }
    else if (r === "choice") { todo.push(label + " (à cocher par toi)"); mark(el, false); }
    else { todo.push(label); mark(el, false); }
  }
  return { filled, todo, total: fields.length };
}
