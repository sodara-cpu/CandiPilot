// Agent navigateur : fonctions injectées dans la page (chrome.scripting, chaque cadre).
// observePage : éléments cliquables et champs visibles, numérotés, + texte visible → envoyés à Claude (via CandiPilot).
// runAction   : exécute UNE action décidée par Claude, avec des garde-fous indépendants de l'IA :
//               jamais l'envoi final, jamais de mot de passe, jamais un consentement, jamais un champ piège.

export function observePage() {
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const visible = (e) => { const r = e.getBoundingClientRect(); return (e.offsetParent !== null || e.type === "file") && r.width + r.height > 0; };
  const labelOf = (e) => {
    const root = e.getRootNode();
    const byFor = e.id && root.querySelector ? root.querySelector(`label[for="${CSS.escape(e.id)}"]`) : null;
    const box = e.closest("fieldset, [role=radiogroup], [class*=question], [class*=Question], [class*=field], [class*=Field]");
    const q = box && box !== e ? clean((box.querySelector("legend, label, [class*=label], [class*=Label]") || {}).innerText || "") : "";
    const own = clean((byFor && byFor.innerText) || (e.closest("label") || {}).innerText || e.getAttribute("aria-label") || e.getAttribute("placeholder")
      || (["A", "BUTTON"].includes(e.tagName) || e.getAttribute("role") === "button" ? e.innerText : "") || e.value && e.type === "submit" && e.value || e.name || "");
    return clean(q && q !== own && ["radio", "checkbox"].includes(e.type) ? `${q} → ${own}` : own).slice(0, 160);
  };
  const els = [], list = [];
  const walk = (root) => root.querySelectorAll("a[href], button, [role=button], [role=option], [role=radio], [role=checkbox], input, select, textarea").forEach((e) => {
    if (!visible(e) || e.disabled || ["hidden"].includes(e.type)) return;
    const i = els.push(e) - 1;
    const tag = e.tagName.toLowerCase();
    const item = { id: i, tag, type: e.type && tag === "input" ? e.type : (e.getAttribute("role") || ""), label: labelOf(e) };
    if (["input", "textarea", "select"].includes(tag)) {
      if (e.type === "file") item.value = e.files && e.files[0] ? e.files[0].name : "";
      else if (e.type === "password") item.value = e.value ? "•••" : "";
      // Liste déroulante sur son option d'attente (« Choisir… », valeur vide) : pas encore choisie.
      else if (tag === "select") item.value = e.value ? clean((e.selectedOptions[0] || {}).text).slice(0, 80) : "";
      else if (!["checkbox", "radio"].includes(e.type)) item.value = clean(e.value).slice(0, 80);
      if (["checkbox", "radio"].includes(e.type)) item.checked = e.checked;
      if (e.required || e.getAttribute("aria-required") === "true") item.required = true;
      if (tag === "select") item.options = [...e.options].map((o) => clean(o.text)).filter(Boolean).slice(0, 25);
    } else if (e.getAttribute("aria-checked")) item.checked = e.getAttribute("aria-checked") === "true";
    list.push(item);
  });
  const deep = (root) => { walk(root); root.querySelectorAll("*").forEach((x) => { if (x.shadowRoot) deep(x.shadowRoot); }); };
  deep(document);
  window.__cpEls = els;
  // Messages d'erreur de validation affichés par le site (champ obligatoire, format…) : renvoyés à l'agent.
  const errors = [];
  const errWalk = (root) => root.querySelectorAll("[aria-invalid=true], [role=alert], [class*=error], [class*=Error], [class*=invalid], .field-error, .help-block").forEach((x) => {
    if (x.offsetParent === null) return;
    const t = clean(x.matches("input, select, textarea") ? `champ invalide : ${labelOf(x)}` : x.innerText);
    if (t && t.length < 200 && !errors.includes(t)) errors.push(t);
  });
  const deepErr = (root) => { errWalk(root); root.querySelectorAll("*").forEach((x) => { if (x.shadowRoot) deepErr(x.shadowRoot); }); };
  deepErr(document);
  return { url: location.href, title: document.title, elements: list.slice(0, 150), errors: errors.slice(0, 15),
           text: clean(document.body ? document.body.innerText : "").slice(0, 2500) };
}

export function runAction(action, files, allowSubmit = false) {
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const e = (window.__cpEls || [])[action.input.id];
  if (action.name !== "wait" && !e) return "élément introuvable (page changée ?)";
  const text = e ? clean(e.innerText || e.value || e.getAttribute("aria-label") || "") : "";
  const lab = e ? clean(((e.id && e.getRootNode().querySelector?.(`label[for="${CSS.escape(e.id)}"]`)) || e.closest("label") || {}).innerText || e.getAttribute("aria-label") || e.name || "") : "";
  const FINAL = /^(submit( application)?|send( application)?|envoyer( ma candidature)?|soumettre( ma candidature)?|valider ma candidature|postuler maintenant|apply now|submit my application|finaliser)$/i;
  const CONSENT = /consent|j'accepte|i agree|i accept|accept the|terms|conditions|privacy|confidentialit|rgpd|gdpr|certif|atteste|déclare|declare|true and (complete|accurate)/i;
  const TRAP = /robots? only|do not (enter|fill)|leave (this )?(field )?(blank|empty)|honeypot|beecatcher/i;
  const set = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true, composed: true }));
  };
  const mark = (el) => { el.style.outline = "3px dashed #6D5EFC"; el.style.outlineOffset = "2px"; };
  if (e && TRAP.test(lab + " " + (e.name || "") + " " + (e.getAttribute("data-automation-id") || ""))) return "refusé : champ piège pour robots";
  switch (action.name) {
    case "click": {
      const filled = [...document.querySelectorAll("input, textarea, select")].filter((x) => x.offsetParent !== null && x.value).length;
      // Envoi final et consentements : seulement si le candidat a choisi l'envoi automatique (mode auto + consentement).
      if (!allowSubmit && (FINAL.test(text) || (e.type === "submit" && filled >= 3 && /submit|envoy|soumet|valid|apply|postul/i.test(text))))
        return "refusé : envoi final réservé au candidat";
      if (!allowSubmit && ["checkbox", "radio"].includes(e.type) && CONSENT.test(lab + " " + text)) return "refusé : consentement réservé au candidat";
      e.scrollIntoView({ block: "center" });
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup"]) e.dispatchEvent(new MouseEvent(t, { bubbles: true, composed: true }));
      e.click();
      if (["checkbox", "radio"].includes(e.type)) mark(e.parentElement || e);
      return "ok";
    }
    case "type_text": {
      if (e.type === "password") return "refusé : mot de passe réservé au candidat";
      if (!["INPUT", "TEXTAREA"].includes(e.tagName)) return "pas un champ de saisie";
      e.focus(); set(e, action.input.text); mark(e);
      return e.value === action.input.text ? "ok (vérifié)" : `échec : le site a modifié la valeur (« ${clean(e.value).slice(0, 40)} »)`;
    }
    case "choose": {
      if (e.tagName !== "SELECT") return "pas une liste déroulante";
      const want = clean(action.input.option).toLowerCase();
      const o = [...e.options].find((x) => clean(x.text).toLowerCase() === want) || [...e.options].find((x) => clean(x.text).toLowerCase().includes(want));
      if (!o) return "option introuvable";
      set(e, o.value); mark(e);
      return e.value === o.value ? "ok (vérifié)" : "échec : option non retenue par le site";
    }
    case "upload": {
      const f = files && files[action.input.document];
      if (e.type !== "file") return "pas un champ de fichier";
      if (!f) return "document non disponible";
      const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
      const dt = new DataTransfer(); dt.items.add(new File([bytes], f.name, { type: "application/pdf" }));
      e.files = dt.files;
      for (const t of ["input", "change"]) e.dispatchEvent(new Event(t, { bubbles: true, composed: true }));
      return e.files.length === 1 && e.files[0].name === f.name ? `ok (vérifié : ${f.name} attaché)` : "échec : fichier non attaché";
    }
    default:
      return "ok";
  }
}
