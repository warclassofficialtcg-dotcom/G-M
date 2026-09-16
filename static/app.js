/* Frontend single-page: gestione prenotazioni palestra / massaggi */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    me: null,
    calendar: null,
    week: null,          // YYYY-MM-DD (lunedì)
    selectedDate: null,
    bookingType: null,   // 'palestra' | 'massaggio' | null (preselezione dalla sezione di provenienza)
    bookingService: null, // chiave del massaggio scelto dal listino
    moveAppt: null,       // appuntamento che si sta spostando (scelta del nuovo orario dal calendario)
    adminTab: "pending",
  };

  // ------------------------------------------------------------ API
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* ignore */ }
    if (!res.ok) {
      const err = new Error(data.error || "Errore di rete");
      err.status = res.status;
      err.code = data.code;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ UI utils
  let toastTimer;
  function toast(msg, isErr = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
  }

  function openModal(html) {
    $("#modal-body").innerHTML = html;
    $("#modal").classList.remove("hidden");
  }
  function closeModal() { $("#modal").classList.add("hidden"); }
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

  function fmtDate(iso) {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("it-IT", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" });
  }
  function statusLabel(s) {
    return { pending: "In attesa", confirmed: "Confermato", rejected: "Rifiutato", cancelled: "Annullato" }[s] || s;
  }
  function typeLabel(t) { return t === "palestra" ? "Palestra" : "Massaggio"; }

  // ------------------------------------------------------------ Auth
  $$("[data-auth]").forEach((b) => b.addEventListener("click", () => {
    $$("[data-auth]").forEach((x) => x.classList.toggle("active", x === b));
    $("#form-login").classList.toggle("hidden", b.dataset.auth !== "login");
    $("#form-register").classList.toggle("hidden", b.dataset.auth !== "register");
    $("#auth-error").classList.add("hidden");
  }));

  async function handleAuth(form, path) {
    const body = Object.fromEntries(new FormData(form).entries());
    const errBox = $("#auth-error");
    errBox.classList.add("hidden");
    try {
      await api(path, { method: "POST", body });
      form.reset();
      await boot();
    } catch (e) {
      errBox.textContent = e.message;
      errBox.classList.remove("hidden");
    }
  }
  $("#form-login").addEventListener("submit", (e) => { e.preventDefault(); handleAuth(e.target, "/api/login"); });
  $("#form-register").addEventListener("submit", (e) => { e.preventDefault(); handleAuth(e.target, "/api/register"); });
  $("#btn-logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.reload(); });

  // ------------------------------------------------------------ Navigazione
  function showView(name) {
    $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + name));
    $$(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    if (name === "calendar") loadCalendar();
    if (name === "training") renderTraining();
    if (name === "massage") renderMassage();
    if (name === "mine") renderMine();
    if (name === "admin") renderAdmin();
    window.scrollTo({ top: 0 });
  }
  $$(".navbtn").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.view === "calendar") { state.bookingType = null; state.bookingService = null; state.moveAppt = null; }
    showView(b.dataset.view);
  }));
  $("#btn-book-training").addEventListener("click", () => {
    if (!(state.me.gym_package && state.me.gym_package.active)) {
      showNoPackage("Per prenotare gli allenamenti serve un abbonamento mensile attivo.");
      return;
    }
    state.bookingType = "palestra"; showView("calendar");
  });
  $("#btn-book-massage").addEventListener("click", () => { state.bookingType = "massaggio"; state.bookingService = null; showView("calendar"); });

  // ------------------------------------------------------------ Boot
  async function boot() {
    try {
      state.me = await api("/api/me");
    } catch (e) {
      state.me = null;
      $("#view-auth").classList.remove("hidden");
      $("#view-app").classList.add("hidden");
      return;
    }
    $("#view-auth").classList.add("hidden");
    $("#view-app").classList.remove("hidden");
    $("#user-name").textContent = state.me.user.name.split(" ")[0];
    $("#user-avatar").textContent = state.me.user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    $("#user-avatar").title = state.me.user.name;
    $("#nav-admin").classList.toggle("hidden", state.me.user.role !== "admin");
    const q = new URLSearchParams(location.search);
    if (q.has("pagamento")) {
      const esito = q.get("pagamento");
      history.replaceState(null, "", "/");
      if (esito === "ok") toast("Pagamento ricevuto: pacchetto attivato");
      else if (esito === "annullato") toast("Pagamento annullato", true);
      else toast("Pagamento non completato: " + (q.get("msg") || ""), true);
      showView("training");
      return;
    }
    showView("calendar");
  }

  async function refreshMe() { state.me = await api("/api/me"); }

  // ------------------------------------------------------------ Calendario
  async function loadCalendar(week) {
    const q = week || state.week ? `?week=${week || state.week}` : "";
    state.calendar = await api("/api/calendar" + q);
    state.week = state.calendar.week_start;
    // giorno selezionato: mantieni se nella settimana, altrimenti oggi o primo giorno aperto
    const days = state.calendar.days;
    if (!state.selectedDate || !days.some((d) => d.date === state.selectedDate)) {
      const today = days.find((d) => d.is_today && !d.closed);
      const firstOpen = days.find((d) => !d.closed && d.slots.some((s) => !s.past)) || days[0];
      state.selectedDate = (today || firstOpen).date;
    }
    renderCalendar();
  }
  $("#week-prev").addEventListener("click", () => loadCalendar(state.calendar.prev_week));
  $("#week-next").addEventListener("click", () => loadCalendar(state.calendar.next_week));

  function renderCalendar() {
    const c = state.calendar;
    const ws = new Date(c.week_start + "T00:00:00"), we = new Date(c.week_end + "T00:00:00");
    const f = (d) => d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
    $("#week-title").textContent = `${f(ws)} – ${f(we)}`;

    const mode = $("#booking-mode");
    if (state.moveAppt) {
      const m = state.moveAppt;
      mode.innerHTML = `Stai <strong>spostando</strong> ${esc(m.massage_name || typeLabel(m.type))} di ${m.date_label} alle ${m.time_label}:
        tocca il nuovo orario nel calendario. <a href="#" id="clear-mode">annulla</a>`;
      mode.classList.remove("hidden");
      $("#clear-mode").addEventListener("click", (e) => { e.preventDefault(); state.moveAppt = null; state.bookingType = null; renderCalendar(); });
    } else if (state.bookingType) {
      const svc = state.bookingService && state.me.massages[state.bookingService];
      mode.innerHTML = `Stai prenotando ${svc ? `<strong>${esc(svc.name)}</strong>` : `un <strong>${typeLabel(state.bookingType).toLowerCase()}</strong>`}: scegli giorno e orario.
        <a href="#" id="clear-mode">annulla</a>`;
      mode.classList.remove("hidden");
      $("#clear-mode").addEventListener("click", (e) => { e.preventDefault(); state.bookingType = null; state.bookingService = null; renderCalendar(); });
    } else {
      mode.classList.add("hidden");
    }

    // --- griglia settimanale: righe = orari, colonne = giorni (domenica chiusa: esclusa)
    const days = c.days.filter((d) => d.weekday !== "Domenica");
    const hours = [...new Set(days.flatMap((d) => d.slots.map((s) => s.hour)))].sort((a, b) => a - b);
    const head = `<tr><th class="th-time"></th>${days.map((d) =>
      `<th class="${d.is_today ? "today" : ""}"><span class="wd">${d.weekday.slice(0, 3)}</span><span class="dn">${d.day}</span></th>`).join("")}</tr>`;

    const rows = hours.map((h) => {
      const cells = days.map((d) => {
        const s = d.slots.find((x) => x.hour === h);
        if (!s) return `<td class="cell closed" title="Chiuso"><span class="x">Chiuso</span></td>`;
        let short = "";
        const a = s.appointments;
        const hasMassage = a.some((x) => x.type === "massaggio");
        const gym = a.filter((x) => x.type === "palestra");
        const allPending = a.length > 0 && a.every((x) => x.status === "pending");
        const notForMode = state.bookingType && !s.types.includes(state.bookingType);
        let cls = "free", main = "", sub = "";
        if (s.types.length === 1 && !a.length) sub = s.types[0] === "massaggio" ? "massaggi" : "palestra";
        if (hasMassage) {
          const m = a.find((x) => x.type === "massaggio");
          cls = m.status === "pending" ? "pending" : "massaggio";
          main = m.massage_name || "Massaggio";
          sub = m.name + (m.mine ? " (tu)" : "");
          short = "M";
        } else if (gym.length) {
          const full = gym.length >= c.max_gym;
          cls = allPending ? "pending" : "palestra";
          main = `Palestra ${gym.length}/${c.max_gym}`;
          sub = gym.map((x) => x.name + (x.mine ? " (tu)" : "")).join(", ");
          short = `${gym.length}/${c.max_gym}`;
          if (full) cls += " full";
        }
        if (s.past) cls += " past";
        else if (notForMode && !a.length) cls = "na";
        const mine = a.some((x) => x.mine);
        const title = main ? `${main} · ${sub}` : sub ? `Solo ${sub}` : "Libero";
        return `<td class="cell ${cls} ${mine ? "mine" : ""}" data-date="${d.date}" data-hour="${h}" title="${esc(title)}">
                  ${main ? `<span class="main">${esc(main)}</span>` : ""}
                  ${sub ? `<span class="sub">${esc(sub)}</span>` : ""}
                  ${short ? `<span class="short">${esc(short)}</span>` : ""}
                  ${!main && !s.past && cls !== "na" ? `<span class="plus">+</span>` : ""}
                </td>`;
      }).join("");
      return `<tr><th class="th-time">${String(h).padStart(2, "0")}:00</th>${cells}</tr>`;
    }).join("");

    $("#grid").innerHTML = `<table class="cal"><thead>${head}</thead><tbody>${rows}</tbody></table>`;
    $$("#grid td.cell:not(.closed)").forEach((el) => el.addEventListener("click", () => {
      const day = c.days.find((d) => d.date === el.dataset.date);
      const slot = day.slots.find((s) => s.hour === Number(el.dataset.hour));
      openBooking(day, slot);
    }));
  }

  // ------------------------------------------------------------ Prenotazione
  function composeMessage(type, massageKey, day, hour, joinLesson) {
    const me = state.me;
    const m = type === "massaggio" && me.massages[massageKey];
    const tipo = m ? `MASSAGGIO – ${m.name} (${m.price}€)` : type === "palestra" ? "PALESTRA" : "MASSAGGIO";
    const when = `${day.weekday} ${String(day.day).padStart(2, "0")}/${day.month}/${day.date.slice(0, 4)} alle ${String(hour).padStart(2, "0")}:00`;
    const mv = state.moveAppt;
    if (mv) return `Ciao! Sono ${me.user.name}.\nHo spostato il mio appuntamento *${tipo}*\n❌ da ${mv.date_label} alle ${mv.time_label}\n✅ a ${when}\nConferma o rifiuta qui: [link di conferma]`;
    if (joinLesson) return `Ciao! Sono ${me.user.name}.\nMi unisco alla lezione di *${tipo}* di ${when}.\nDettagli: [link]`;
    return `Ciao! Sono ${me.user.name}.\nRichiesta appuntamento *${tipo}*\n📅 ${when}\nConferma o rifiuta qui: [link di conferma]`;
  }

  function openBooking(day, slot) {
    const a = slot.appointments;
    const mine = a.find((x) => x.mine);
    const hasMassage = a.some((x) => x.type === "massaggio");
    const gym = a.filter((x) => x.type === "palestra");
    const gymFull = gym.length >= state.calendar.max_gym;
    const mv = state.moveAppt;
    const isSame = mv && mv.date === day.date && mv.hour === slot.hour;
    let canGym = !slot.past && !hasMassage && !gymFull && !mine && slot.types.includes("palestra");
    let canMassage = !slot.past && a.length === 0 && slot.types.includes("massaggio");
    if (mv) {  // spostamento: solo il tipo dell'appuntamento, e non sullo stesso orario
      canGym = canGym && mv.type === "palestra" && !isSame;
      canMassage = canMassage && mv.type === "massaggio" && !isSame;
    }
    const massages = state.me.massages, cats = state.me.massage_categories;

    let listHtml = "";
    if (a.length) {
      listHtml = `<div class="alert info small">In questo orario: ${a.map((x) =>
        `${esc(x.name)}${x.mine ? " (tu)" : ""} · ${x.massage_name || typeLabel(x.type)}${x.status === "pending" ? " (in attesa)" : ""}`).join("<br>")}</div>`;
    }
    if (!slot.past && !a.length && slot.types.length === 1) {
      listHtml += `<p class="small muted">In questo orario si può prenotare solo ${slot.types[0] === "massaggio" ? "un massaggio" : "la palestra"}.</p>`;
    }

    let msg = "";
    if (slot.past) msg = `<p class="alert warn">Orario già passato.</p>`;
    else if (isSame) msg = `<p class="alert info">Questo è l'orario attuale del tuo appuntamento: scegline un altro.</p>`;
    else if (mv && !slot.types.includes(mv.type)) msg = `<p class="alert warn">In questo orario non si può prenotare ${mv.type === "palestra" ? "la palestra" : "un massaggio"}.</p>`;
    else if (mine) msg = `<p class="alert info">Hai già un appuntamento in questo orario.</p>`;
    else if (hasMassage) msg = `<p class="alert warn">Orario occupato da un massaggio. Scegli un altro orario.</p>`;
    else if (gymFull) msg = `<p class="alert warn">Lezione al completo. Scegli un altro orario.</p>`;
    else if (gym.length) msg = `<p class="alert info">C'è già chi si allena a quest'ora: puoi <strong>unirti alla lezione</strong>. Il massaggio non è disponibile.</p>`;

    if (mv && !state.bookingService) state.bookingService = mv.massage_type;
    let sel = state.bookingType || (canGym ? "palestra" : canMassage ? "massaggio" : null);
    if (sel === "palestra" && !canGym) sel = canMassage ? "massaggio" : null;
    if (sel === "massaggio" && !canMassage) sel = canGym ? "palestra" : null;

    openModal(`
      <h2>${mv ? "Sposta a: " : ""}${day.weekday} ${day.day}/${day.month} · ${slot.label}</h2>
      ${mv ? `<p class="small muted">Attuale: ${esc(mv.massage_name || typeLabel(mv.type))} · ${mv.date_label} alle ${mv.time_label}</p>` : ""}
      ${listHtml}${msg}
      ${(canGym || canMassage) ? `
        <p class="small muted">Tipo di appuntamento</p>
        <div class="type-choice">
          <button class="palestra ${sel === "palestra" ? "sel" : ""}" data-t="palestra" ${canGym ? "" : "disabled"}><svg><use href="#i-dumbbell"/></svg>Palestra${gym.length ? " · unisciti" : ""}</button>
          <button class="massaggio ${sel === "massaggio" ? "sel" : ""}" data-t="massaggio" ${canMassage ? "" : "disabled"}><svg><use href="#i-spa"/></svg>Massaggio</button>
        </div>
        <div id="massage-pick" class="${sel === "massaggio" ? "" : "hidden"}">
          <label>Trattamento
            <select id="massage-select">
              ${Object.entries(cats).map(([ck, c]) => `<optgroup label="${esc(c.title)}">${Object.entries(massages).filter(([, m]) => m.cat === ck)
                .map(([k, m]) => `<option value="${k}" ${k === state.bookingService ? "selected" : ""}>${esc(m.name)} · ${m.price}€</option>`).join("")}</optgroup>`).join("")}
            </select></label>
        </div>
        <p class="small muted" style="margin-bottom:.2rem">Anteprima del messaggio WhatsApp che invierai al titolare:</p>
        <div class="wa-text wa-preview" id="wa-preview"></div>
        <p class="small muted">Dopo la conferma si apre WhatsApp con questo messaggio già scritto; il titolare riceve il link per confermare.</p>
        <button class="btn primary full lg" id="btn-confirm-booking" ${sel ? "" : "disabled"}>${mv ? "Sposta qui" : "Conferma prenotazione"}</button>
      ` : ""}
    `);

    const joinLesson = gym.some((x) => x.status === "confirmed");
    const updatePreview = () => {
      const el = $("#wa-preview");
      if (!el || !sel) return;
      const mk = sel === "massaggio" ? $("#massage-select").value : null;
      el.textContent = composeMessage(sel, mk, day, slot.hour, sel === "palestra" && joinLesson);
    };
    updatePreview();
    const msel = $("#massage-select");
    if (msel) msel.addEventListener("change", updatePreview);
    $$(".type-choice button").forEach((b) => b.addEventListener("click", () => {
      sel = b.dataset.t;
      $$(".type-choice button").forEach((x) => x.classList.toggle("sel", x === b));
      $("#massage-pick").classList.toggle("hidden", sel !== "massaggio");
      $("#btn-confirm-booking").disabled = false;
      updatePreview();
    }));
    const btn = $("#btn-confirm-booking");
    if (btn) btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const body = { type: sel, date: day.date, hour: slot.hour };
        if (sel === "massaggio") body.massage_type = $("#massage-select").value;
        const r = mv
          ? await api(`/api/appointments/${mv.id}/move`, { method: "POST", body })
          : await api("/api/appointments", { method: "POST", body });
        state.bookingType = null; state.bookingService = null; state.moveAppt = null;
        showWhatsappStep(r, !!mv);
        await refreshMe();
        loadCalendar();
      } catch (e) {
        if (e.code === "no_package") showNoPackage(e.message);
        else toast(e.message, true);
        btn.disabled = false;
      }
    });
  }

  function showNoPackage(text) {
    openModal(`
      <h2>Serve l'abbonamento mensile</h2>
      <p>${esc(text)}</p>
      <p class="small muted">L'abbonamento vale un mese esatto dal giorno del pagamento. Puoi pagare un mese alla volta oppure attivare il rinnovo automatico.</p>
      <button class="btn primary full lg" id="btn-go-training">Vai all'abbonamento</button>
      <button class="btn full" id="btn-np-close" style="margin-bottom:0">Chiudi</button>
    `);
    $("#btn-go-training").addEventListener("click", () => { closeModal(); showView("training"); });
    $("#btn-np-close").addEventListener("click", closeModal);
  }

  function showWhatsappStep(r, moved = false) {
    const a = r.appointment;
    // apri subito WhatsApp (potrebbe essere bloccato come popup: c'è comunque il pulsante)
    const win = window.open(r.whatsapp_url, "_blank");
    openModal(`
      <h2>${moved ? "Appuntamento spostato" : r.joined ? "Ti sei unito alla lezione" : "Richiesta registrata"}</h2>
      <p><span class="badge ${a.type}">${a.massage_name || typeLabel(a.type)}</span> ${a.date_label} alle ${a.time_label}</p>
      ${r.joined
        ? `<p>La lezione era già confermata: il tuo posto è nel calendario. Avvisa comunque il titolare su WhatsApp.</p>`
        : `<p>Ora <strong>invia il messaggio su WhatsApp</strong> al titolare: riceverà il link per confermare. Quando conferma, l'appuntamento comparirà nel calendario generale.</p>`}
      ${r.no_package ? `<p class="alert warn small">${a.type === "massaggio" ? "Nessun percorso mensile attivo: il massaggio si paga in studio (o attiva un percorso dalla sezione Massaggi)." : "Non risulta un pacchetto mensile attivo: concorda il pagamento con il titolare."}</p>` : ""}
      <a class="btn whatsapp full lg" href="${r.whatsapp_url}" target="_blank" rel="noopener" style="text-decoration:none">
        <svg><use href="#i-whatsapp"/></svg> ${win ? "Riapri WhatsApp" : "Invia su WhatsApp"}
      </a>
      <p class="small muted" style="margin:.8rem 0 .2rem">Messaggio:</p>
      <div class="wa-text">${esc(r.whatsapp_text)}</div>
      <button class="btn full" id="btn-done" style="margin-top:.8rem;margin-bottom:0">Chiudi</button>
    `);
    $("#btn-done").addEventListener("click", closeModal);
  }

  async function resendWhatsapp(id) {
    try {
      const r = await api(`/api/appointments/${id}/whatsapp`);
      window.open(r.whatsapp_url, "_blank");
    } catch (e) { toast(e.message, true); }
  }

  async function cancelAppointment(id, after) {
    if (!confirm("Annullare questo appuntamento?")) return;
    try {
      await api(`/api/appointments/${id}`, { method: "DELETE" });
      toast("Appuntamento annullato");
      await refreshMe();
      after && after();
    } catch (e) { toast(e.message, true); }
  }

  function appointmentItem(a, opts = {}) {
    return `<div class="item">
      <div class="main">
        <div class="title"><span class="badge ${a.type}">${typeLabel(a.type)}</span> ${a.massage_name ? esc(a.massage_name) + " · " : ""}${a.date_label} · ${a.time_label}</div>
        <div class="sub"><span class="status-${a.status}">${statusLabel(a.status)}</span>${a.joined ? " · unito alla lezione" : ""}
          ${opts.showUser ? ` · <strong>${esc(a.user_name)}</strong>${a.user_phone ? ` · <a href="https://wa.me/${esc(a.user_phone.replace(/\D/g, ""))}" target="_blank">${esc(a.user_phone)}</a>` : ""}` : ""}
        </div>
      </div>
      <div class="btn-row" style="margin:0">
        ${opts.resend ? `<button class="btn whatsapp small" data-resend="${a.id}"><svg style="width:15px;height:15px"><use href="#i-whatsapp"/></svg>WhatsApp</button>` : ""}
        ${opts.cancel ? `<button class="btn small" data-move="${a.id}">Sposta</button>
                         <button class="btn danger small" data-cancel="${a.id}">Annulla</button>` : ""}
        ${opts.adminConfirm ? `<button class="btn ok small" data-status="confirmed" data-id="${a.id}">Conferma</button>
                               <button class="btn danger small" data-status="rejected" data-id="${a.id}">Rifiuta</button>` : ""}
        ${opts.adminCancel ? `<button class="btn danger small" data-status="cancelled" data-id="${a.id}">Annulla</button>` : ""}
      </div>
    </div>`;
  }

  function bindAppointmentButtons(root, after) {
    $$("[data-resend]", root).forEach((b) => b.addEventListener("click", () => resendWhatsapp(b.dataset.resend)));
    $$("[data-cancel]", root).forEach((b) => b.addEventListener("click", () => cancelAppointment(b.dataset.cancel, after)));
    $$("[data-move]", root).forEach((b) => b.addEventListener("click", () => {
      const a = state.me.appointments.find((x) => x.id === Number(b.dataset.move));
      if (!a) return;
      state.moveAppt = a; state.bookingType = a.type; state.bookingService = a.massage_type || null;
      state.week = a.date;  // apre il calendario sulla settimana dell'appuntamento
      showView("calendar");
    }));
    $$("[data-status]", root).forEach((b) => b.addEventListener("click", async () => {
      try {
        await api(`/api/admin/appointments/${b.dataset.id}/status`, { method: "POST", body: { status: b.dataset.status } });
        toast("Aggiornato");
        after && after();
      } catch (e) { toast(e.message, true); }
    }));
  }

  // ------------------------------------------------------------ Allenamento
  function renderTraining() {
    const me = state.me;
    const box = $("#package-box");
    const gp = me.gym_package || {};
    const sub = me.subscription;
    const extras = me.packages.filter((p) => p.info && p.info.family === "extra");
    let html = "";
    if (gp.active) {
      const weekAppts = me.appointments.filter((a) => a.type === "palestra");
      const soon = gp.days_left <= 7;
      html += `<div class="hero">
        <div class="lbl">Abbonamento attivo</div>
        <div class="val">${esc(me.catalog[gp.type].label.split(" – ")[0])} · ${gp.per_week} allenamenti a settimana</div>
        <div class="meta">Scade ${fmtDate(gp.end_date)} · ${gp.days_left} giorn${gp.days_left === 1 ? "o" : "i"} rimanent${gp.days_left === 1 ? "e" : "i"}</div>
        <span class="stat">${weekAppts.length} allenament${weekAppts.length === 1 ? "o" : "i"} in programma</span>
        ${sub ? `<span class="stat">↻ Rinnovo automatico attivo</span>` : soon ? `<span class="stat warn">⚠ In scadenza: rinnova qui sotto</span>` : ""}
      </div>`;
    } else {
      html += `<div class="hero expired">
        <div class="lbl">Abbonamento</div>
        <div class="val">${gp.expired ? "Abbonamento scaduto" : "Nessun abbonamento attivo"}</div>
        <div class="meta">${gp.expired ? `Scaduto ${fmtDate(gp.expired.end_date)}. ` : ""}Per prenotare gli allenamenti serve l'abbonamento mensile: vale un mese esatto dal giorno del pagamento.</div>
      </div>`;
    }
    if (sub) {
      html += `<p class="small" style="margin:-.4rem 0 1rem">Rinnovo automatico ${sub.provider === "simulated" ? "(prova) " : ""}sul ${esc(me.catalog[sub.package_type].label.split(" – ")[0])}: a ogni scadenza viene addebitato il mese successivo.
        <a href="#" id="btn-cancel-sub">Disdici il rinnovo</a></p>`;
    }
    if (extras.length) {
      html += `<p class="small" style="margin:-.4rem 0 1rem">Servizi attivi: ${extras.map((p) => `<span class="badge palestra">${esc(p.info.label)}</span>`).join(" ")}</p>`;
    }
    box.innerHTML = html;
    const cs = $("#btn-cancel-sub");
    if (cs) cs.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm("Disdire il rinnovo automatico? Il mese già pagato resta valido fino alla scadenza.")) return;
      try { await api("/api/payments/subscription/cancel", { method: "POST", body: {} }); toast("Rinnovo automatico disdetto"); await refreshMe(); renderTraining(); }
      catch (err) { toast(err.message, true); }
    });

    $("#sheet-workout").textContent = me.sheets.workout || "La tua scheda non è ancora stata caricata dal titolare.";
    $("#sheet-diet").textContent = me.sheets.diet || "La tua dieta non è ancora stata caricata dal titolare.";
    const pay = me.payments || {};
    const canBuy = pay.live || pay.simulated;
    $("#payments-notice").innerHTML = pay.simulated
      ? `<p class="alert info small">Pagamenti in modalità prova: nessun addebito reale.</p>`
      : pay.problem && me.user.role === "admin" ? `<p class="alert warn small">${esc(pay.problem)}</p>` : "";
    $("#pricelist").innerHTML = Object.entries(me.catalog).filter(([, p]) => p.family !== "massaggio").map(([k, p]) =>
      `<li>
        <div class="main">
          <div class="title">${esc(p.label.split(" – ")[0])}</div>
          <div class="sub">${p.per_week ? `${p.per_week} allenamenti a settimana · un mese dal pagamento` : "validità un mese"}</div>
        </div>
        <strong class="price">${p.price}€${p.monthly ? "<span class='muted small'>/mese</span>" : ""}</strong>
        ${canBuy ? (p.monthly
          ? `<span class="buy-group"><button class="btn primary small" data-buy="${k}">Paga 1 mese</button>
             <button class="btn small" data-buy="${k}" data-recurring="1" ${sub ? "disabled" : ""} title="Addebito automatico ogni mese, disdici quando vuoi">↻ Rinnovo automatico</button></span>`
          : `<button class="btn primary small" data-buy="${k}">Acquista</button>`) : ""}
      </li>`).join("");
    bindBuyButtons($("#pricelist"));

    const hist = me.payment_history || [];
    $("#my-payments").innerHTML = hist.length ? hist.map((h) => paymentItem(h)).join("")
      : `<p class="empty">Nessun pagamento effettuato.</p>`;
  }

  function bindBuyButtons(root) {
    $$("[data-buy]", root).forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        if (b.dataset.recurring && !confirm("Attivare il rinnovo automatico? Ogni mese verrà addebitato l'importo dell'abbonamento; puoi disdire in qualsiasi momento dalla sezione Allenamento.")) { b.disabled = false; return; }
        const r = await api("/api/payments/start", { method: "POST", body: { type: b.dataset.buy, recurring: !!b.dataset.recurring } });
        location.href = r.url;
      } catch (e) { toast(e.message, true); b.disabled = false; }
    }));
  }

  function payStatus(s) { return { pending: "In attesa", paid: "Pagato", cancelled: "Annullato" }[s] || s; }
  function paymentItem(h, showUser = false) {
    const when = (h.paid_at || h.created_at).replace("T", " ").slice(0, 16);
    return `<div class="item">
      <div class="main">
        <div class="title">${esc(h.label)} <span class="badge ${h.status === "paid" ? "confirmed" : h.status === "pending" ? "pending" : "cancelled"}">${payStatus(h.status)}</span></div>
        <div class="sub">${when} · ${h.provider === "simulated" ? "prova" : "Stripe"}${showUser ? ` · <strong>${esc(h.user_name)}</strong> ${esc(h.user_email)}` : ""}
          <br><span class="muted">imponibile ${h.net.toFixed(2)}€ + IVA ${h.vat.toFixed(2)}€</span></div>
      </div>
      <strong>${h.amount.toFixed(2)}€</strong>
    </div>`;
  }

  // ------------------------------------------------------------ Massaggi
  function renderMassage() {
    const me = state.me;
    const pkg = me.packages.find((p) => p.info && p.info.per_month);
    const used = me.appointments.filter((a) => a.type === "massaggio").length;
    $("#massage-package-box").innerHTML = pkg
      ? `<div class="hero massaggio">
          <div class="lbl">Percorso attivo</div>
          <div class="val">${esc(pkg.info.label.split(" – ")[0])} · ${pkg.info.per_month} massaggi</div>
          <div class="meta">Valido dal ${fmtDate(pkg.start_date)} al ${fmtDate(pkg.end_date)}</div>
          <span class="stat">${used} in programma</span>
        </div>`
      : `<div class="hero massaggio">
          <h2>Prenota il tuo massaggio</h2>
          <p>Scegli il trattamento dal listino, poi giorno e ora. L'orario del massaggio è riservato esclusivamente a te.</p>
        </div>`;

    $("#massage-catalog").innerHTML = Object.entries(me.massage_categories).map(([ck, c]) => `
      <div class="card">
        <h2>${esc(c.title)}</h2>
        <p class="small muted" style="margin-top:-.4rem">${esc(c.text)}</p>
        <p class="tags">${c.for.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</p>
        <ul class="pricelist">${Object.entries(me.massages).filter(([, m]) => m.cat === ck).map(([k, m]) => `
          <li>
            <div class="main"><div class="title">${esc(m.name)}</div><div class="sub">${esc(m.tags)}</div></div>
            <strong class="price">${m.price}€</strong>
            <button class="btn small" data-svc="${k}">Prenota</button>
          </li>`).join("")}</ul>
      </div>`).join("");
    $$("[data-svc]").forEach((b) => b.addEventListener("click", () => {
      state.bookingType = "massaggio"; state.bookingService = b.dataset.svc; showView("calendar");
    }));

    const pay = me.payments || {};
    const canBuy = pay.live || pay.simulated;
    $("#massage-payments-notice").innerHTML = pay.simulated ? `<p class="alert info small">Pagamenti in modalità prova: nessun addebito reale.</p>` : "";
    $("#massage-pricelist").innerHTML = Object.entries(me.catalog).filter(([, p]) => p.family === "massaggio").map(([k, p]) => `
      <li>
        <div class="main"><div class="title">${esc(p.label.split(" – ")[0])}</div><div class="sub">${p.per_month} massaggi al mese a scelta libera</div></div>
        <strong class="price">${p.price}€</strong>
        ${canBuy ? `<button class="btn primary small" data-buy="${k}">Acquista</button>` : ""}
      </li>`).join("");
    bindBuyButtons($("#massage-pricelist"));

    const list = me.appointments.filter((a) => a.type === "massaggio");
    const box = $("#my-massages");
    box.innerHTML = list.length ? list.map((a) => appointmentItem(a, { resend: a.status === "pending", cancel: true })).join("")
      : `<p class="empty">Nessun massaggio prenotato.</p>`;
    bindAppointmentButtons(box, renderMassage);
  }

  // ------------------------------------------------------------ I miei appuntamenti
  function renderMine() {
    const list = state.me.appointments;
    const box = $("#my-appointments");
    box.innerHTML = list.length ? list.map((a) => appointmentItem(a, { resend: a.status === "pending", cancel: true })).join("")
      : `<p class="empty">Nessun appuntamento in programma. Vai al calendario per prenotare.</p>`;
    bindAppointmentButtons(box, renderMine);
  }

  // ------------------------------------------------------------ Admin
  $$("[data-admin]").forEach((b) => b.addEventListener("click", () => {
    state.adminTab = b.dataset.admin;
    renderAdmin();
  }));

  async function renderAdmin() {
    $$("[data-admin]").forEach((x) => x.classList.toggle("active", x.dataset.admin === state.adminTab));
    ["pending", "upcoming", "users", "payments"].forEach((t) => $(`#admin-${t}`).classList.toggle("hidden", t !== state.adminTab));
    $("#admin-user-detail").classList.add("hidden");

    if (state.adminTab === "pending" || state.adminTab === "upcoming") {
      const box = $(`#admin-${state.adminTab}`);
      box.innerHTML = `<p class="muted">Caricamento…</p>`;
      const r = await api(`/api/admin/appointments?status=${state.adminTab}`);
      const title = state.adminTab === "pending" ? "Richieste in attesa di conferma" : "Appuntamenti confermati";
      box.innerHTML = `<h2>${title}</h2>` + (r.appointments.length
        ? r.appointments.map((a) => appointmentItem(a, {
            showUser: true,
            adminConfirm: state.adminTab === "pending",
            adminCancel: state.adminTab === "upcoming",
          })).join("")
        : `<p class="empty">Niente da mostrare.</p>`);
      bindAppointmentButtons(box, renderAdmin);
    }

    if (state.adminTab === "payments") {
      const box = $("#admin-payments");
      box.innerHTML = `<p class="muted">Caricamento…</p>`;
      const r = await api("/api/admin/payments");
      const tot = r.payments.filter((p) => p.status === "paid").reduce((a, p) => a + p.amount, 0);
      box.innerHTML = `<h2>Pagamenti</h2>
        ${r.status.simulated ? `<p class="alert info small">Modalità prova (PAYMENTS_PROVIDER=simulated): nessun incasso reale. Configura Stripe nel file .env.</p>` : ""}
        ${r.status.problem ? `<p class="alert warn small">${esc(r.status.problem)}</p>` : ""}
        ${r.recovered ? `<p class="alert ok small">Recuperati ${r.recovered} pagamenti rimasti in sospeso.</p>` : ""}
        <p class="small muted">Incassato: <strong>${tot.toFixed(2)}€</strong> (IVA inclusa)</p>` +
        (r.payments.length ? r.payments.map((p) => paymentItem(p, true)).join("") : `<p class="empty">Nessun pagamento.</p>`);
    }

    if (state.adminTab === "users") {
      const box = $("#admin-users");
      box.innerHTML = `<p class="muted">Caricamento…</p>`;
      const r = await api("/api/admin/users");
      box.innerHTML = `<h2>Clienti (${r.users.length})</h2>` + r.users.map((u) => `
        <div class="item clickable" data-user="${u.id}">
          <div class="main">
            <div class="title">${esc(u.name)} ${u.role === "admin" ? '<span class="badge palestra">titolare</span>' : ""}</div>
            <div class="sub">${esc(u.email)}${u.phone ? " · " + esc(u.phone) : ""}</div>
            <div class="sub">${u.packages.length ? u.packages.map((p) => esc(p.info ? p.info.label : p.type)).join(" · ") : "Nessun pacchetto attivo"}</div>
          </div><svg class="arrow"><use href="#i-chevron"/></svg>
        </div>`).join("");
      $$("[data-user]", box).forEach((el) => el.addEventListener("click", () => openUserDetail(el.dataset.user)));
    }
  }

  async function openUserDetail(uid) {
    const r = await api(`/api/admin/users/${uid}`);
    const box = $("#admin-user-detail");
    $("#admin-users").classList.add("hidden");
    box.classList.remove("hidden");
    const today = new Date().toISOString().slice(0, 10);
    const in30 = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
    box.innerHTML = `
      <button class="btn ghost small" id="back-users" style="padding-left:0">← Tutti i clienti</button>
      <h2 style="margin-top:.6rem">${esc(r.user.name)}</h2>
      <p class="muted small">${esc(r.user.email)}${r.user.phone ? ` · <a href="https://wa.me/${esc(r.user.phone.replace(/\D/g, ""))}" target="_blank">${esc(r.user.phone)}</a>` : ""}</p>

      <h3>Pacchetti</h3>
      <div id="pkg-list">${r.packages.length ? r.packages.map((p) => `
        <div class="item"><div class="main">
          <div class="title">${esc(p.info ? p.info.label : p.type)}</div>
          <div class="sub">${fmtDate(p.start_date)} → ${fmtDate(p.end_date)}${p.note ? " · " + esc(p.note) : ""}</div>
        </div><button class="btn danger small" data-delpkg="${p.id}">Elimina</button></div>`).join("")
        : `<p class="empty">Nessun pacchetto.</p>`}</div>
      <form id="form-pkg" class="card" style="background:var(--surface-2);box-shadow:none;margin-top:.8rem">
        <label>Nuovo pacchetto
          <select name="type">
            ${Object.entries(state.me.catalog).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}
          </select></label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">
          <label>Dal <input type="date" name="start_date" value="${today}" required></label>
          <label>Al <input type="date" name="end_date" value="${in30}" required></label>
        </div>
        <label>Nota (facoltativa) <input type="text" name="note" placeholder="es. pagato in contanti"></label>
        <button class="btn primary" type="submit">Aggiungi pacchetto</button>
      </form>

      <h3>Scheda allenamento</h3>
      <textarea id="ta-workout" placeholder="Scrivi qui la scheda…">${esc(r.sheets.workout)}</textarea>
      <h3 style="margin-top:.8rem">Dieta</h3>
      <textarea id="ta-diet" placeholder="Scrivi qui la dieta…">${esc(r.sheets.diet)}</textarea>
      <div class="btn-row"><button class="btn primary" id="btn-save-sheets">Salva scheda e dieta</button></div>

      <h3 style="margin-top:1rem">Ultimi appuntamenti</h3>
      <div id="user-appts">${r.appointments.length ? r.appointments.map((a) => appointmentItem(a, {})).join("") : `<p class="empty">Nessun appuntamento.</p>`}</div>
    `;
    $("#back-users").addEventListener("click", renderAdmin);
    $$("[data-delpkg]", box).forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Eliminare questo pacchetto?")) return;
      await api(`/api/admin/packages/${b.dataset.delpkg}`, { method: "DELETE" });
      openUserDetail(uid);
    }));
    $("#form-pkg").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/api/admin/users/${uid}/packages`, { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) });
        toast("Pacchetto aggiunto");
        openUserDetail(uid);
      } catch (err) { toast(err.message, true); }
    });
    $("#btn-save-sheets").addEventListener("click", async () => {
      try {
        await api(`/api/admin/users/${uid}/sheets`, { method: "PUT", body: { workout: $("#ta-workout").value, diet: $("#ta-diet").value } });
        toast("Salvato");
      } catch (err) { toast(err.message, true); }
    });
  }

  // service worker: rende il sito installabile come app ("Aggiungi a schermata Home")
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/static/sw.js", { scope: "/" }).catch(() => {});
  }

  boot();
})();
