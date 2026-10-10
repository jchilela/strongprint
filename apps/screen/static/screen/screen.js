/* StrongPrint — ecrã do kiosk (Raspberry Pi, Chromium em modo kiosk).
 *
 * Fluxo: inativo (QR / número) → identificado → escolher ficheiro → opções e preço
 * → (carregar carteira) → imprimir → resultado → volta ao início.
 *
 * O token do Pi chega no fragmento do URL (#token=...) e fica em localStorage.
 * A sessão do cliente só vive em memória e termina por inatividade.
 */
(function () {
  "use strict";

  var CFG = window.SP_CONFIG;
  var $app = document.getElementById("app");
  var $overlay = document.getElementById("overlay");

  // ---------------------------------------------------------------- textos
  var T = {
    pt: {
      tagline: "Imprima aqui com a sua carteira StrongPrint",
      step1: "Leia o código com a câmara do telemóvel",
      step2: "Confirme no telemóvel",
      step3: "Escolha o ficheiro e imprima",
      scan: "Leia com o telemóvel",
      orPhone: "Entrar com o número de telefone",
      priceFrom: "P&B {bw} · cor desde {color} por página",
      phoneTitle: "O seu número de telefone",
      phoneHint: "Vamos enviar um código por SMS.",
      send: "Enviar código",
      back: "Voltar",
      codeTitle: "Código recebido por SMS",
      sentTo: "Enviado para {phone}",
      confirm: "Confirmar",
      wrongCode: "Código errado. Tente de novo.",
      sendFailed: "Não foi possível enviar o código.",
      wait: "Aguarde um minuto e tente de novo.",
      nameTitle: "Como se chama?",
      save: "Guardar",
      hello: "Olá, {name}",
      balance: "Saldo",
      whatToDo: "O que quer fazer?",
      printTile: "Imprimir",
      printTileSub: "Um ficheiro seu: telemóvel, pen USB ou Meus ficheiros",
      copyTile: "Copiar",
      copyTileSub: "Um documento em papel",
      copyTitle: "Fazer cópia",
      adfLoaded: "Documento detetado no alimentador",
      adfEmpty: "Coloque o documento no alimentador (texto para cima) ou no vidro (texto para baixo)",
      scannerOff: "O digitalizador não está disponível.",
      original: "Original",
      oneSided: "Só frente",
      twoSided: "Frente e verso",
      scan: "Digitalizar",
      scanning: "A digitalizar…",
      scannedPages: "{n} páginas digitalizadas",
      scanMore: "Digitalizar mais folhas",
      scanMoreHint: "Ponha as folhas seguintes no alimentador ou no vidro.",
      continue: "Continuar",
      cancel: "Cancelar",
      tryAgain: "Tentar de novo",
      errAdfEmpty: "Não há papel no alimentador. Ponha o documento e tente de novo.",
      errBusy: "O digitalizador está ocupado. Aguarde um pouco.",
      errScan: "A digitalização falhou. Tente de novo.",
      blankWarning: "Parece que a cópia está em branco. Confirme o documento.",
      chooseTitle: "O que quer imprimir?",
      myFiles: "Meus ficheiros",
      myFilesSub: "Enviados no portal ou WhatsApp",
      usb: "Pen USB",
      usbSub: "Ligue a pen na entrada USB",
      usbNone: "Nenhuma pen detetada",
      phoneUpload: "Enviar agora",
      phoneUploadSub: "Do telemóvel, com o QR",
      finish: "Terminar",
      noFiles: "Ainda não tem ficheiros. Use «Enviar agora».",
      checking: "A verificar…",
      pages: "{n} pág.",
      colorPages: "{n} a cores",
      uploadTitle: "Envie o ficheiro do telemóvel",
      uploadHint: "Leia o código, escolha «Meus ficheiros» e envie. O ficheiro aparece aqui.",
      preparing: "A preparar o documento…",
      rejected: "Este ficheiro não pode ser impresso.",
      color: "Cor",
      bw: "P&B",
      colorMode: "Cores",
      duplex: "Frente e verso",
      no: "Não",
      yes: "Sim",
      copies: "Cópias",
      pagesOpt: "Páginas",
      all: "Todas",
      from: "De",
      to: "a",
      total: "Total",
      available: "Disponível",
      sheets: "{n} folhas",
      faces: "{n} faces",
      print: "Imprimir · {total}",
      topupNeeded: "Faltam {missing}",
      topup: "Carregar carteira",
      topupTitle: "Carregar a carteira",
      topupHint: "Escolha o valor. Vai receber uma referência Multicaixa.",
      payTitle: "Pague esta referência",
      entity: "Entidade",
      reference: "Referência",
      amount: "Valor",
      waitingPayment: "A aguardar o pagamento… continua sozinho quando for pago.",
      queued: "Na fila de impressão…",
      printing: "A imprimir…",
      done: "Impresso! Recolha as suas folhas.",
      partial: "A impressão parou a meio.",
      failed: "Não foi possível imprimir.",
      charged: "Debitado: {kz}",
      notCharged: "Nada foi debitado.",
      partialCharged: "Saíram {n} faces · debitado {kz} · o resto voltou à carteira.",
      printAnother: "Imprimir outro",
      idleTitle: "Ainda está aí?",
      idleHint: "A sessão termina em {s} s.",
      stay: "Continuar",
      unavailable: "Kiosk temporariamente indisponível",
      unMaintenance: "Em manutenção. Volte daqui a pouco.",
      unPaper: "A impressora está sem papel. A equipa já foi avisada.",
      unJam: "Há papel encravado na impressora. A equipa já foi avisada.",
      unDoor: "Uma porta da impressora está aberta.",
      unToner: "O toner acabou. A equipa já foi avisada.",
      unOffline: "A impressora está desligada ou sem ligação.",
      unPrinter: "A impressora tem um problema. A equipa já foi avisada.",
      unSession: "A sua carteira não foi cobrada. Pode voltar quando o kiosk estiver disponível.",
      printPaused: "Impressão parada: {msg} Continua sozinha quando for resolvido.",
      noToken: "Este ecrã ainda não está configurado.",
      noTokenHint: "Abra-o com o endereço que contém o token do dispositivo.",
      error: "Ocorreu um erro. Tente de novo.",
      printerBusy: "A impressora não está pronta. Tente daqui a pouco.",
    },
    en: {
      tagline: "Print here with your StrongPrint wallet",
      step1: "Scan the code with your phone camera",
      step2: "Confirm on your phone",
      step3: "Choose your file and print",
      scan: "Scan with your phone",
      orPhone: "Sign in with phone number",
      priceFrom: "B&W {bw} · colour from {color} per page",
      phoneTitle: "Your phone number",
      phoneHint: "We will send you a code by SMS.",
      send: "Send code",
      back: "Back",
      codeTitle: "Code received by SMS",
      sentTo: "Sent to {phone}",
      confirm: "Confirm",
      wrongCode: "Wrong code. Try again.",
      sendFailed: "We could not send the code.",
      wait: "Wait a minute and try again.",
      nameTitle: "What is your name?",
      save: "Save",
      hello: "Hello, {name}",
      balance: "Balance",
      whatToDo: "What would you like to do?",
      printTile: "Print",
      printTileSub: "A file of yours: phone, USB pen drive or My files",
      copyTile: "Copy",
      copyTileSub: "A paper document",
      copyTitle: "Make a copy",
      adfLoaded: "Document detected in the feeder",
      adfEmpty: "Put the document in the feeder (face up) or on the glass (face down)",
      scannerOff: "The scanner is not available.",
      original: "Original",
      oneSided: "Single-sided",
      twoSided: "Double-sided",
      scan: "Scan",
      scanning: "Scanning…",
      scannedPages: "{n} pages scanned",
      scanMore: "Scan more pages",
      scanMoreHint: "Put the next pages in the feeder or on the glass.",
      continue: "Continue",
      cancel: "Cancel",
      tryAgain: "Try again",
      errAdfEmpty: "The feeder is empty. Put the document in and try again.",
      errBusy: "The scanner is busy. Please wait a moment.",
      errScan: "Scanning failed. Please try again.",
      blankWarning: "The copy looks blank. Check the document.",
      chooseTitle: "What do you want to print?",
      myFiles: "My files",
      myFilesSub: "Sent from the portal or WhatsApp",
      usb: "USB pen drive",
      usbSub: "Plug it into the USB port",
      usbNone: "No pen drive detected",
      phoneUpload: "Send now",
      phoneUploadSub: "From your phone, with the QR",
      finish: "Finish",
      noFiles: "No files yet. Use “Send now”.",
      checking: "Checking…",
      pages: "{n} p.",
      colorPages: "{n} in colour",
      uploadTitle: "Send the file from your phone",
      uploadHint: "Scan the code, open “My files” and upload. It will show up here.",
      preparing: "Preparing the document…",
      rejected: "This file cannot be printed.",
      color: "Colour",
      bw: "B&W",
      colorMode: "Colours",
      duplex: "Double-sided",
      no: "No",
      yes: "Yes",
      copies: "Copies",
      pagesOpt: "Pages",
      all: "All",
      from: "From",
      to: "to",
      total: "Total",
      available: "Available",
      sheets: "{n} sheets",
      faces: "{n} sides",
      print: "Print · {total}",
      topupNeeded: "{missing} missing",
      topup: "Top up wallet",
      topupTitle: "Top up your wallet",
      topupHint: "Choose the amount. You will get a Multicaixa reference.",
      payTitle: "Pay this reference",
      entity: "Entity",
      reference: "Reference",
      amount: "Amount",
      waitingPayment: "Waiting for payment… it continues by itself once paid.",
      queued: "Waiting for the printer…",
      printing: "Printing…",
      done: "Printed! Collect your pages.",
      partial: "Printing stopped half-way.",
      failed: "We could not print.",
      charged: "Charged: {kz}",
      notCharged: "Nothing was charged.",
      partialCharged: "{n} sides came out · charged {kz} · the rest went back to your wallet.",
      printAnother: "Print another",
      idleTitle: "Are you still there?",
      idleHint: "The session ends in {s} s.",
      stay: "Continue",
      unavailable: "Kiosk temporarily unavailable",
      unMaintenance: "Under maintenance. Please come back soon.",
      unPaper: "The printer is out of paper. The team has been notified.",
      unJam: "There is a paper jam. The team has been notified.",
      unDoor: "A printer door is open.",
      unToner: "The toner is empty. The team has been notified.",
      unOffline: "The printer is off or not connected.",
      unPrinter: "The printer has a problem. The team has been notified.",
      unSession: "Your wallet was not charged. Come back when the kiosk is available.",
      printPaused: "Printing paused: {msg} It resumes by itself once fixed.",
      noToken: "This screen is not configured yet.",
      noTokenHint: "Open it with the address that contains the device token.",
      error: "Something went wrong. Please try again.",
      printerBusy: "The printer is not ready. Try again shortly.",
    },
  };

  var S = {
    lang: "pt",
    device: null,
    kiosk: null,
    available: true,
    reason: "",
    prices: null,
    session: null, // {token, user}
    pairing: null,
    job: null,
    agent: false,
    timers: {},
    lastTouch: Date.now(),
    screen: "boot",
  };

  function t(key, vars) {
    var s = (T[S.lang] && T[S.lang][key]) || T.pt[key] || key;
    Object.keys(vars || {}).forEach(function (k) {
      s = s.split("{" + k + "}").join(vars[k]);
    });
    return s;
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function kz(n) {
    var v = Math.round(Number(n) || 0);
    return (v < 0 ? "−" : "") + String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " Kz";
  }

  // ---------------------------------------------------------------- API
  function api(path, opts) {
    opts = opts || {};
    var headers = { Accept: "application/json" };
    if (opts.auth === "session" && S.session) headers.Authorization = "Kiosk " + S.session.token;
    else headers.Authorization = "Device " + S.device;
    var init = { method: opts.method || "GET", headers: headers };
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(CFG.api + path, init).then(function (res) {
      return res
        .json()
        .catch(function () { return {}; })
        .then(function (data) {
          if (res.status === 401 && opts.auth === "session") {
            logout();
          }
          if (!res.ok) {
            var err = new Error(data.error || "http_" + res.status);
            err.status = res.status;
            err.data = data;
            throw err;
          }
          return data;
        });
    });
  }

  function clearTimer(name) {
    if (S.timers[name]) {
      clearTimeout(S.timers[name]);
      clearInterval(S.timers[name]);
      delete S.timers[name];
    }
  }

  function every(name, ms, fn) {
    clearTimer(name);
    fn();
    S.timers[name] = setInterval(fn, ms);
  }

  // ---------------------------------------------------------------- moldura
  function frame(inner, opts) {
    opts = opts || {};
    var right = "";
    if (S.session) {
      right =
        '<div class="who">' + esc(t("hello", { name: S.session.user.name || "" })) +
        "<br>" + esc(t("balance")) + ' <strong class="num">' + kz(S.session.user.available_kz) +
        "</strong></div>";
    } else {
      right = langToggle();
    }
    return (
      '<header class="bar"><div class="brand">Strong<span>Print</span>' +
      (S.kiosk ? ' <small class="muted" style="font-size:16px;font-weight:500">' + esc(S.kiosk.code) + "</small>" : "") +
      "</div>" + right + "</header>" +
      '<section class="body' + (opts.center ? " center" : "") + '">' + inner + "</section>"
    );
  }

  function langToggle() {
    return (
      '<div class="lang">' +
      '<button data-lang="pt" aria-pressed="' + (S.lang === "pt") + '">PT</button>' +
      '<button data-lang="en" aria-pressed="' + (S.lang === "en") + '">EN</button></div>'
    );
  }

  function render(name, html) {
    S.screen = name;
    $app.innerHTML = html;
  }

  // ---------------------------------------------------------------- início
  function boot() {
    var m = location.hash.match(/token=([^&]+)/);
    if (m) {
      try { localStorage.setItem("sp_device_token", decodeURIComponent(m[1])); } catch (e) {}
      history.replaceState(null, "", location.pathname);
    }
    try { S.device = localStorage.getItem("sp_device_token"); } catch (e) { S.device = null; }
    if (!S.device) {
      render("noToken", frame("<h1>" + esc(t("noToken")) + '</h1><p class="muted">' + esc(t("noTokenHint")) + "</p>", { center: true }));
      return;
    }
    every("state", 10000, refreshState);
    every("agent", 10000, detectAgent);
    showIdle();
  }

  function refreshState() {
    return api("/kiosk/state")
      .then(function (d) {
        S.kiosk = d.kiosk;
        S.prices = d.prices;
        S.printer = d.printer;
        S.available = d.available;
        S.reason = d.reason;
        updateUnavailable();
        if (S.screen === "idle") paintIdle();
      })
      .catch(function () {});
  }

  var REASON_TEXT = {
    maintenance: "unMaintenance", paper_out: "unPaper", paper_jam: "unJam", door_open: "unDoor",
    toner_empty: "unToner", offline: "unOffline",
  };
  var REASON_ICON = { maintenance: "🛠️", paper_out: "📄", paper_jam: "📄", door_open: "🚪", toner_empty: "🖨️", offline: "🔌" };

  function reasonText() {
    return t(REASON_TEXT[S.reason] || "unPrinter");
  }

  function updateUnavailable() {
    if (S.available) {
      if ($overlay.dataset.kind === "blocked") {
        $overlay.hidden = true;
        $overlay.dataset.kind = "";
      }
      if (S.screen === "printing") paintPrinting();
      return;
    }
    // A meio de uma impressão não bloqueamos: o trabalho continua quando o problema se resolver.
    if (S.screen === "printing") {
      paintPrinting();
      return;
    }
    if ($overlay.dataset.kind === "idle") return;
    $overlay.dataset.kind = "blocked";
    $overlay.innerHTML =
      '<div class="box"><div class="big-icon">' + (REASON_ICON[S.reason] || "⏸") + "</div><h1>" + esc(t("unavailable")) +
      '</h1><p class="big muted">' + esc(reasonText()) + "</p>" +
      (S.session ? '<p class="muted">' + esc(t("unSession")) + '</p><button class="btn" data-act="blockedLogout">' + esc(t("finish")) + "</button>" : "") +
      "</div>";
    $overlay.hidden = false;
  }

  function detectAgent() {
    if (!CFG.agentUrl) return;
    var ctrl = new AbortController();
    setTimeout(function () { ctrl.abort(); }, 1200);
    fetch(CFG.agentUrl + "/health", { signal: ctrl.signal })
      .then(function (r) { S.agent = r.ok; })
      .catch(function () { S.agent = false; });
  }

  // ---------------------------------------------------------------- inativo (QR)
  function showIdle() {
    S.session = null;
    S.job = null;
    ["pairPoll", "pairRenew", "job", "docs", "wallet", "idleCountdown"].forEach(clearTimer);
    S.screen = "idle";
    newPairing();
    every("pairRenew", 4 * 60 * 1000, newPairing);
  }

  function newPairing() {
    api("/kiosk/pairings", { method: "POST" })
      .then(function (p) {
        S.pairing = p;
        paintIdle();
        every("pairPoll", 2000, pollPairing);
      })
      .catch(function () {
        S.pairing = null;
        paintIdle();
      });
  }

  function paintIdle() {
    if (S.screen !== "idle") return;
    var prices = S.prices
      ? '<p class="muted">' + esc(t("priceFrom", { bw: kz(S.prices.bw_page_kz), color: kz(S.prices.color_from_kz) })) + "</p>"
      : "";
    var qr = S.pairing ? '<div class="qr">' + S.pairing.qr_svg + "</div>" : '<div class="spinner"></div>';
    render(
      "idle",
      frame(
        '<div class="split"><div><h1>' + esc(t("tagline")) + "</h1>" + prices +
          '<ol class="steps"><li>' + esc(t("step1")) + "</li><li>" + esc(t("step2")) + "</li><li>" + esc(t("step3")) +
          '</li></ol></div><div style="text-align:center">' + qr + '<p class="big" style="margin-top:14px">' + esc(t("scan")) +
          '</p><button class="btn ghost" data-act="phone">' + esc(t("orPhone")) + "</button></div></div>"
      )
    );
  }

  function pollPairing() {
    if (!S.pairing || S.screen !== "idle") return;
    api("/kiosk/pairings/" + S.pairing.id)
      .then(function (d) {
        if (d.status === "CLAIMED") {
          startSession(d);
        } else if (d.status === "EXPIRED") {
          newPairing();
        }
      })
      .catch(function () {});
  }

  function startSession(data) {
    ["pairPoll", "pairRenew"].forEach(clearTimer);
    S.session = { token: data.token, user: data.user };
    if (data.user.language && T[data.user.language]) S.lang = data.user.language;
    touch();
    if (data.user.needs_name) showName();
    else showHome();
  }

  function logout() {
    S.session = null;
    $overlay.hidden = true;
    $overlay.dataset.kind = "";
    showIdle();
    updateUnavailable();
  }

  // ---------------------------------------------------------------- número + SMS
  var entry = { phone: "", code: "", name: "", error: "" };

  function showPhone() {
    ["pairPoll", "pairRenew"].forEach(clearTimer);
    entry = { phone: "", code: "", name: "", error: "" };
    paintPhone();
  }

  function keypad() {
    var keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓"];
    return '<div class="keypad">' + keys.map(function (k) {
      return '<button data-key="' + k + '">' + k + "</button>";
    }).join("") + "</div>";
  }

  function paintPhone() {
    var shown = entry.phone || '<span class="ph">9__ ___ ___</span>';
    render(
      "phone",
      frame(
        "<h1>" + esc(t("phoneTitle")) + '</h1><p class="muted">' + esc(t("phoneHint")) +
          '</p><div class="display">+244 ' + (entry.phone ? esc(groupPhone(entry.phone)) : shown) + "</div>" +
          '<div class="error">' + esc(entry.error) + "</div>" + keypad() +
          '<div class="row" style="width:min(440px,100%);margin:14px auto 0"><button class="btn ghost" data-act="cancel">' + esc(t("back")) +
          '</button><button class="btn" data-act="sendCode"' + (entry.phone.length === 9 ? "" : " disabled") + ">" + esc(t("send")) + "</button></div>",
        { center: true }
      )
    );
  }

  function groupPhone(p) {
    return p.replace(/(\d{3})(?=\d)/g, "$1 ");
  }

  function sendCode() {
    entry.error = "";
    api("/kiosk/login/code", { method: "POST", body: { phone: "+244" + entry.phone, language: S.lang } })
      .then(function (d) {
        entry.code = "";
        entry.devCode = d.dev_code || "";
        paintCode();
      })
      .catch(function (e) {
        entry.error = e.status === 429 ? t("wait") : t("sendFailed");
        paintPhone();
      });
  }

  function paintCode() {
    var dots = "";
    for (var i = 0; i < 6; i++) dots += entry.code[i] ? entry.code[i] : '<span class="ph">•</span>';
    render(
      "code",
      frame(
        "<h1>" + esc(t("codeTitle")) + '</h1><p class="muted">' + esc(t("sentTo", { phone: "+244 " + groupPhone(entry.phone) })) +
          (entry.devCode ? " · <strong>" + esc(entry.devCode) + "</strong>" : "") +
          '</p><div class="display">' + dots + '</div><div class="error">' + esc(entry.error) + "</div>" + keypad() +
          '<div class="row" style="width:min(440px,100%);margin:14px auto 0"><button class="btn ghost" data-act="phone">' + esc(t("back")) + "</button></div>",
        { center: true }
      )
    );
  }

  function verifyCode() {
    api("/kiosk/login/verify", { method: "POST", body: { phone: "+244" + entry.phone, code: entry.code, language: S.lang } })
      .then(startSession)
      .catch(function () {
        entry.code = "";
        entry.error = t("wrongCode");
        paintCode();
      });
  }

  // ---------------------------------------------------------------- nome
  function showName() {
    entry.name = "";
    entry.error = "";
    paintName();
  }

  function paintName() {
    var rows = ["QWERTYUIOP", "ASDFGHJKLÇ", "ZXCVBNM"];
    var kb = '<div class="kbd">' + rows.map(function (r) {
      return '<div class="r">' + r.split("").map(function (c) {
        return '<button data-char="' + c + '">' + c + "</button>";
      }).join("") + "</div>";
    }).join("") +
      '<div class="r"><button class="wide" data-char=" ">␣</button><button data-char="⌫">⌫</button></div></div>';
    render(
      "name",
      frame(
        "<h1>" + esc(t("nameTitle")) + '</h1><div class="input-line">' + esc(entry.name) + '&nbsp;</div><div class="error">' + esc(entry.error) +
          "</div>" + kb + '<div class="row" style="width:min(440px,100%);margin:14px auto 0"><button class="btn" data-act="saveName"' +
          (entry.name.trim().length >= 2 ? "" : " disabled") + ">" + esc(t("save")) + "</button></div>",
        { center: true }
      )
    );
  }

  function saveName() {
    var name = entry.name.trim().toLowerCase().replace(/(^|\s)\S/g, function (s) { return s.toUpperCase(); });
    api("/me", { method: "POST", auth: "session", body: { full_name: name } })
      .then(function (u) {
        S.session.user = u;
        showHome();
      })
      .catch(function () {
        entry.error = t("error");
        paintName();
      });
  }

  // ---------------------------------------------------------------- menu
  function refreshMe() {
    if (!S.session) return Promise.resolve();
    return api("/me", { auth: "session" }).then(function (u) { S.session.user = u; });
  }

  function showHome() {
    ["docs", "job", "wallet", "scanner"].forEach(clearTimer);
    S.job = null;
    refreshMe().catch(function () {});
    render(
      "home",
      frame(
        "<h1>" + esc(t("whatToDo")) + '</h1><div class="tiles" style="grid-template-columns:1fr 1fr;flex:1">' +
          '<button class="tile" data-act="printMenu" style="min-height:240px"><div class="icon">🖨️</div><div><div class="t" style="font-size:34px">' + esc(t("printTile")) + '</div><div class="s">' + esc(t("printTileSub")) + "</div></div></button>" +
          '<button class="tile" data-act="copy" style="min-height:240px"><div class="icon">📠</div><div><div class="t" style="font-size:34px">' + esc(t("copyTile")) + '</div><div class="s">' + esc(t("copyTileSub")) + "</div></div></button>" +
          '</div><div class="row" style="margin-top:18px"><button class="btn danger" data-act="logout">' + esc(t("finish")) + "</button></div>"
      )
    );
  }

  function showPrintMenu() {
    ["docs", "job", "wallet", "scanner"].forEach(clearTimer);
    S.job = null;
    render(
      "printMenu",
      frame(
        "<h1>" + esc(t("chooseTitle")) + '</h1><div class="tiles">' +
          '<button class="tile" data-act="docs"><div class="icon">📄</div><div><div class="t">' + esc(t("myFiles")) + '</div><div class="s">' + esc(t("myFilesSub")) + "</div></div></button>" +
          '<button class="tile" data-act="usb"' + (S.agent ? "" : " disabled") + '><div class="icon">🔌</div><div><div class="t">' + esc(t("usb")) + '</div><div class="s">' + esc(S.agent ? t("usbSub") : t("usbNone")) + "</div></div></button>" +
          '<button class="tile" data-act="upload"><div class="icon">📱</div><div><div class="t">' + esc(t("phoneUpload")) + '</div><div class="s">' + esc(t("phoneUploadSub")) + "</div></div></button>" +
          '</div><div class="row" style="margin-top:auto"><button class="btn ghost" data-act="home">' + esc(t("back")) + "</button></div>"
      )
    );
  }

  // ---------------------------------------------------------------- ficheiros
  var docs = [];

  function showDocs() {
    S.screen = "docs";
    every("docs", 4000, loadDocs);
    paintDocs();
  }

  function loadDocs() {
    api("/documents", { auth: "session" })
      .then(function (list) {
        docs = list;
        if (S.screen === "docs" || S.screen === "upload") paintDocs();
      })
      .catch(function () {});
  }

  function docItems() {
    if (!docs.length) return '<p class="muted big" style="margin:20px 0">' + esc(t("noFiles")) + "</p>";
    return docs.map(function (d) {
      var meta =
        d.state === "READY"
          ? esc(t("pages", { n: d.pages })) + (d.color_pages ? " · " + esc(t("colorPages", { n: d.color_pages })) : "")
          : '<span class="pill warn">' + esc(t("checking")) + "</span>";
      return (
        '<button class="item" data-doc="' + esc(d.id) + '"' + (d.state === "READY" ? "" : " disabled") + '><div><div class="t">' +
        esc(d.filename) + '</div><div class="s">' + meta + '</div></div><span class="pill">›</span></button>'
      );
    }).join("");
  }

  function paintDocs() {
    var upload = S.screen === "upload";
    var head = upload
      ? '<div class="split" style="flex:0 0 auto;align-items:start"><div><h1>' + esc(t("uploadTitle")) + '</h1><p class="muted">' + esc(t("uploadHint")) +
        '</p></div><div style="text-align:center">' + (S.uploadSvg ? '<div class="qr" style="width:min(26vh,240px)">' + S.uploadSvg + "</div>" : "") + "</div></div>"
      : "<h1>" + esc(t("myFiles")) + "</h1>";
    $app.innerHTML = frame(
      head + '<div class="list">' + docItems() + '</div><div class="row"><button class="btn ghost" data-act="printMenu">' + esc(t("back")) + "</button></div>"
    );
  }

  function showUpload() {
    S.screen = "upload";
    api("/kiosk/upload-qr").then(function (d) {
      S.uploadSvg = d.qr_svg;
      if (S.screen === "upload") paintDocs();
    }).catch(function () {});
    every("docs", 4000, loadDocs);
    paintDocs();
  }

  function pickDoc(id) {
    clearTimer("docs");
    showPreparing();
    api("/kiosks/" + encodeURIComponent(S.kiosk.code) + "/jobs", { method: "POST", auth: "session", body: { document_id: id } })
      .then(function (job) {
        S.job = job;
        waitAnalysis();
      })
      .catch(function () { showMessage("❗", t("error"), true); });
  }

  // ---------------------------------------------------------------- pen USB (agente local)
  var usbFiles = [];

  function showUsb() {
    S.screen = "usb";
    fetch(CFG.agentUrl + "/files")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        usbFiles = d.files || [];
        paintUsb();
      })
      .catch(function () {
        usbFiles = [];
        paintUsb();
      });
    paintUsb();
  }

  function paintUsb() {
    var items = usbFiles.length
      ? usbFiles.map(function (f, i) {
          return '<button class="item" data-usb="' + i + '"><div><div class="t">' + esc(f.name) + '</div><div class="s">' + esc(f.dir || "") + "</div></div><span class=\"pill\">›</span></button>";
        }).join("")
      : '<p class="muted big">' + esc(t("usbNone")) + "</p>";
    $app.innerHTML = frame("<h1>" + esc(t("usb")) + '</h1><div class="list">' + items + '</div><div class="row"><button class="btn ghost" data-act="printMenu">' + esc(t("back")) + "</button></div>");
  }

  function pickUsb(i) {
    var f = usbFiles[i];
    showPreparing();
    api("/kiosks/" + encodeURIComponent(S.kiosk.code) + "/jobs", { method: "POST", auth: "session", body: { filename: f.name, source: "USB" } })
      .then(function (job) {
        S.job = job;
        return fetch(CFG.agentUrl + "/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: f.path, url: job.upload.url, authorization: "Kiosk " + S.session.token }),
        }).then(function (r) {
          if (!r.ok) throw new Error("agent_upload");
          waitAnalysis();
        });
      })
      .catch(function () { showMessage("❗", t("error"), true); });
  }

  // ---------------------------------------------------------------- cópia
  var copy = { duplex: false, scanner: null };

  function showCopy() {
    clearTimer("job");
    copy = { duplex: false, scanner: null };
    S.screen = "copy";
    every("scanner", 2000, function () {
      api("/kiosk/scanner").then(function (st) {
        copy.scanner = st;
        if (S.screen === "copy") paintCopy();
      }).catch(function () {});
    });
    paintCopy();
  }

  function paintCopy() {
    var st = copy.scanner;
    var status = !st
      ? '<div class="spinner"></div>'
      : !st.available
        ? '<p class="error big">' + esc(t("scannerOff")) + "</p>"
        : st.adf_loaded
          ? '<p class="big" style="color:var(--ok)">✓ ' + esc(t("adfLoaded")) + "</p>"
          : '<p class="big muted">' + esc(t("adfEmpty")) + "</p>";
    var duplexRow = !st || st.adf_duplex !== false
      ? '<div class="opt"><span class="label">' + esc(t("original")) + "</span>" + seg("copyDuplex", copy.duplex, t("oneSided"), t("twoSided")) + "</div>"
      : "";
    render(
      "copy",
      frame(
        "<h1>" + esc(t("copyTitle")) + '</h1><div class="summary" style="margin:8px 0 16px">' + status + "</div>" + duplexRow +
          '<div class="row" style="margin-top:auto"><button class="btn ghost" data-act="home">' + esc(t("back")) +
          '</button><button class="btn" data-act="scanStart"' + (st && st.available ? "" : " disabled") + ">" + esc(t("scan")) + "</button></div>"
      )
    );
  }

  function startScan() {
    clearTimer("scanner");
    showScanning();
    api("/kiosks/" + encodeURIComponent(S.kiosk.code) + "/copies", { method: "POST", auth: "session", body: { duplex_original: copy.duplex } })
      .then(function (job) {
        S.job = job;
        waitScan();
      })
      .catch(function () { showScanError("scan_failed"); });
  }

  function scanMore() {
    showScanning();
    api("/jobs/" + S.job.id + "/scan", { method: "POST", auth: "session", body: {} })
      .then(function (job) { S.job = job; waitScan(); })
      .catch(function () { showScanError("scan_failed"); });
  }

  function showScanning() {
    render("scanning", frame('<div class="big-icon">📠</div><h1>' + esc(t("scanning")) + '</h1><div class="spinner"></div>', { center: true }));
  }

  function waitScan() {
    every("job", 1500, function () {
      refreshJob().then(function () {
        var sc = S.job.scan || {};
        if (sc.status === "scanning") return;
        clearTimer("job");
        if (sc.status === "error") showScanError(sc.error);
        else showScanned();
      }).catch(function () {});
    });
  }

  function showScanned() {
    var sc = S.job.scan || {};
    render(
      "scanned",
      frame(
        '<div class="big-icon">📄</div><h1>' + esc(t("scannedPages", { n: sc.pages || 0 })) + '</h1><p class="muted big">' + esc(t("scanMoreHint")) +
          '</p><div class="row" style="width:min(820px,100%);margin:24px auto 0"><button class="btn ghost" data-act="cancelJob">' + esc(t("cancel")) +
          '</button><button class="btn ghost" data-act="scanMore">' + esc(t("scanMore")) + '</button><button class="btn" data-act="scanFinish">' + esc(t("continue")) + "</button></div>",
        { center: true }
      )
    );
  }

  function showScanError(code) {
    var msg = code === "adf_empty" ? t("errAdfEmpty") : code === "scanner_busy" ? t("errBusy") : t("errScan");
    var hasPages = S.job && S.job.scan && S.job.scan.pages > 0;
    render(
      "scanError",
      frame(
        '<div class="big-icon">⚠️</div><h2>' + esc(msg) + '</h2><div class="row" style="width:min(820px,100%);margin:24px auto 0"><button class="btn ghost" data-act="' +
          (S.job ? "cancelJob" : "home") + '">' + esc(t("cancel")) + '</button><button class="btn" data-act="' + (S.job ? "scanMore" : "copy") + '">' + esc(t("tryAgain")) + "</button>" +
          (hasPages ? '<button class="btn" data-act="scanFinish">' + esc(t("continue")) + "</button>" : "") + "</div>",
        { center: true }
      )
    );
  }

  function finishScan() {
    showPreparing();
    api("/jobs/" + S.job.id + "/scan/finish", { method: "POST", auth: "session" })
      .then(function (job) { S.job = job; waitAnalysis(); })
      .catch(function () { showScanError("scan_failed"); });
  }

  // ---------------------------------------------------------------- análise
  function showPreparing() {
    render("preparing", frame('<div class="spinner"></div><h2>' + esc(t("preparing")) + "</h2>", { center: true }));
  }

  function waitAnalysis() {
    showPreparing();
    every("job", 1500, function () {
      api("/jobs/" + S.job.id, { auth: "session" }).then(function (job) {
        S.job = job;
        if (job.state === "ANALYZED") {
          clearTimer("job");
          showOptions();
        } else if (["REJECTED", "EXPIRED", "CANCELLED", "FAILED"].indexOf(job.state) !== -1) {
          clearTimer("job");
          showMessage("⚠️", t("rejected"), true);
        }
      }).catch(function () {});
    });
  }

  // ---------------------------------------------------------------- opções e preço
  var opts = {};

  function showOptions() {
    var o = S.job.options || {};
    var range = parseRange(o.page_range, S.job.pages);
    opts = { color: !!o.color, duplex: !!o.duplex, copies: o.copies || 1, from: range[0], to: range[1] };
    paintOptions();
  }

  function parseRange(r, pages) {
    if (!r) return [1, pages];
    var m = String(r).match(/^(\d+)-(\d+)$/);
    return m ? [Number(m[1]), Number(m[2])] : [1, pages];
  }

  function seg(name, value, a, b) {
    return (
      '<div class="seg"><button data-opt="' + name + '" data-val="0" aria-pressed="' + !value + '">' + esc(a) +
      '</button><button data-opt="' + name + '" data-val="1" aria-pressed="' + !!value + '">' + esc(b) + "</button></div>"
    );
  }

  function stepper(name, value) {
    return '<div class="stepper"><button data-step="' + name + '" data-d="-1">−</button><span class="v num">' + value +
      '</span><button data-step="' + name + '" data-d="1">+</button></div>';
  }

  function paintOptions() {
    var job = S.job;
    var b = job.price_breakdown || {};
    var pages = job.pages || 1;
    var printer = S.printer || { color: true, duplex: true };
    var available = (job.wallet && job.wallet.available_kz) || 0;
    var missing = (job.wallet && job.wallet.missing_kz) || 0;
    var rows =
      (printer.color ? '<div class="opt"><span class="label">' + esc(t("color")) + "</span>" + seg("color", opts.color, t("bw"), t("colorMode")) + "</div>" : "") +
      (printer.duplex ? '<div class="opt"><span class="label">' + esc(t("duplex")) + "</span>" + seg("duplex", opts.duplex, t("no"), t("yes")) + "</div>" : "") +
      '<div class="opt"><span class="label">' + esc(t("copies")) + "</span>" + stepper("copies", opts.copies) + "</div>" +
      (pages > 1
        ? '<div class="opt"><span class="label">' + esc(t("pagesOpt")) + '</span><div class="stepper">' + esc(t("from")) + " " + stepper("from", opts.from) + " " + esc(t("to")) + " " + stepper("to", opts.to) + "</div></div>"
        : "");
    var action = missing > 0
      ? '<p class="error">' + esc(t("topupNeeded", { missing: kz(missing) })) + '</p><button class="btn block" data-act="topup">' + esc(t("topup")) + "</button>"
      : '<button class="btn block" data-act="print">' + esc(t("print", { total: kz(job.total_kz) })) + "</button>";
    var blank = job.source === "COPY" && (b.pages || []).length && (b.pages || []).every(function (p) { return !p.coverage_pct; });
    render(
      "options",
      frame(
        (blank ? '<p class="error">' + esc(t("blankWarning")) + "</p>" : "") +
        '<h2 style="overflow-wrap:anywhere">' + esc(job.original_filename) + ' <span class="muted" style="font-size:20px">· ' + esc(t("pages", { n: pages })) + "</span></h2>" +
          '<div class="opts"><div>' + rows + '</div><div class="summary"><div class="muted">' + esc(t("total")) + '</div><div class="total num">' + kz(job.total_kz) +
          '</div><div class="line"><span>' + esc(t("faces", { n: b.faces || 0 })) + "</span><span>" + esc(t("sheets", { n: b.sheets || 0 })) + "</span></div>" +
          '<div class="line"><span>' + esc(t("available")) + '</span><span class="num">' + kz(available) + '</span></div><div style="margin-top:auto">' + action +
          '</div><button class="btn ghost block" data-act="cancelJob" style="margin-top:10px">' + esc(t("back")) + "</button></div></div>"
      )
    );
  }

  var optTimer = null;
  function pushOptions() {
    clearTimeout(optTimer);
    optTimer = setTimeout(function () {
      var range = opts.from === 1 && opts.to === S.job.pages ? null : opts.from + "-" + opts.to;
      api("/jobs/" + S.job.id + "/options", {
        method: "PATCH",
        auth: "session",
        body: { color: opts.color, duplex: opts.duplex, copies: opts.copies, page_range: range },
      }).then(function (job) {
        S.job = job;
        if (S.screen === "options") paintOptions();
      }).catch(function () {});
    }, 250);
  }

  // ---------------------------------------------------------------- carregar carteira
  function showTopup() {
    var missing = (S.job.wallet && S.job.wallet.missing_kz) || 0;
    var base = Math.max(100, Math.ceil(missing / 100) * 100);
    var amounts = [base].concat([500, 1000, 2000, 5000].filter(function (a) { return a > base; })).slice(0, 4);
    render(
      "topup",
      frame(
        "<h1>" + esc(t("topupTitle")) + '</h1><p class="muted">' + esc(t("topupHint")) + '</p><div class="tiles" style="grid-template-columns:repeat(' + amounts.length + ',1fr)">' +
          amounts.map(function (a) {
            return '<button class="tile" data-amount="' + a + '" style="min-height:120px;align-items:center;justify-content:center"><div class="t num" style="font-size:32px">' + kz(a) + "</div></button>";
          }).join("") +
          '</div><div class="row" style="margin-top:auto"><button class="btn ghost" data-act="backToOptions">' + esc(t("back")) + "</button></div>"
      )
    );
  }

  function createTopup(amount) {
    showPreparing();
    api("/wallet/topups", { method: "POST", auth: "session", body: { amount_kz: amount } })
      .then(function (tp) {
        render(
          "pay",
          frame(
            "<h1>" + esc(t("payTitle")) + '</h1><dl class="ref"><dt>' + esc(t("entity")) + "</dt><dd>" + esc(tp.entity) + "</dd><dt>" + esc(t("reference")) +
              "</dt><dd>" + esc(groupPhone(tp.reference || "")) + "</dd><dt>" + esc(t("amount")) + '</dt><dd class="num">' + kz(tp.amount_kz) +
              '</dd></dl><div class="spinner"></div><p class="muted">' + esc(t("waitingPayment")) + '</p><div class="row" style="width:min(440px,100%);margin:10px auto 0"><button class="btn ghost" data-act="backToOptions">' +
              esc(t("back")) + "</button></div>",
            { center: true }
          )
        );
        every("wallet", 3000, function () {
          api("/jobs/" + S.job.id, { auth: "session" }).then(function (job) {
            S.job = job;
            if (job.wallet && job.wallet.missing_kz === 0) {
              clearTimer("wallet");
              refreshMe().then(paintOptionsScreen, paintOptionsScreen);
            }
          }).catch(function () {});
        });
      })
      .catch(function () { showMessage("❗", t("error"), false, true); });
  }

  function paintOptionsScreen() {
    clearTimer("wallet");
    paintOptions();
  }

  // ---------------------------------------------------------------- imprimir
  var PROGRESS = { QUEUED: 55, PRINTING: 80 };

  function print() {
    showPreparing();
    api("/jobs/" + S.job.id + "/checkout", { method: "POST", auth: "session" })
      .then(function (job) {
        S.job = job;
        paintPrinting();
        every("job", 2000, pollPrint);
      })
      .catch(function (e) {
        if (e.status === 409 && e.data && e.data.error === "printer_not_ready") {
          S.available = false;
          S.reason = e.data.reason || "";
          showMessage(REASON_ICON[S.reason] || "⏸", reasonText(), false, true);
        }
        else if (e.status === 402) { refreshJob().then(showTopup); }
        else showMessage("❗", t("error"), false, true);
      });
  }

  function refreshJob() {
    return api("/jobs/" + S.job.id, { auth: "session" }).then(function (job) { S.job = job; });
  }

  function paintPrinting() {
    var job = S.job;
    if (!job) return;
    var label = job.state === "PRINTING" ? t("printing") : t("queued");
    var paused = S.available ? "" : '<p class="error big">' + esc(t("printPaused", { msg: reasonText() })) + "</p>";
    render(
      "printing",
      frame(
        '<div class="big-icon">🖨️</div><h1>' + esc(label) + "</h1>" + paused + '<div class="progress"><span style="width:' + (PROGRESS[job.state] || 40) +
          '%"></span></div><p class="muted big">' + esc(job.original_filename) + "</p>",
        { center: true }
      )
    );
  }

  function pollPrint() {
    refreshJob().then(function () {
      var job = S.job;
      if (job.state === "QUEUED" || job.state === "PRINTING" || job.state === "RESERVED") {
        paintPrinting();
        return;
      }
      clearTimer("job");
      var printed = job.pages_printed || 0;
      // Atualiza o saldo antes de mostrar o resultado (o topo do ecrã mostra-o).
      refreshMe().catch(function () {}).then(function () {
        if (job.state === "COMPLETED") {
          showResult("✅", t("done"), t("charged", { kz: kz(job.total_kz) }));
        } else if (job.state === "PARTIAL") {
          api("/wallet", { auth: "session" }).then(function (w) {
            var last = (w.entries || []).filter(function (e) { return String(e.job) === String(job.id); })[0];
            var charged = last ? -last.amount_kz : 0;
            showResult("⚠️", t("partial"), t("partialCharged", { n: printed, kz: kz(charged) }));
          });
        } else {
          showResult("❌", t("failed"), t("notCharged"));
        }
      });
    }).catch(function () {});
  }

  function showResult(icon, title, sub) {
    render(
      "result",
      frame(
        '<div class="big-icon">' + icon + "</div><h1>" + esc(title) + '</h1><p class="big muted">' + esc(sub) +
          '</p><div class="row" style="width:min(720px,100%);margin:24px auto 0"><button class="btn ghost" data-act="logout">' + esc(t("finish")) +
          '</button><button class="btn" data-act="home">' + esc(t("printAnother")) + "</button></div>",
        { center: true }
      )
    );
  }

  function showMessage(icon, text, toHome, toOptions) {
    render(
      "message",
      frame(
        '<div class="big-icon">' + icon + '</div><h2>' + esc(text) + '</h2><div class="row" style="width:min(440px,100%);margin:20px auto 0"><button class="btn" data-act="' +
          (toOptions ? "backToOptions" : "home") + '">' + esc(t("back")) + "</button></div>",
        { center: true }
      )
    );
  }

  function cancelJob() {
    var job = S.job;
    S.job = null;
    if (job) api("/jobs/" + job.id + "/cancel", { method: "POST", auth: "session" }).catch(function () {});
    showHome();
  }

  // ---------------------------------------------------------------- inatividade
  function touch() {
    S.lastTouch = Date.now();
    if ($overlay.dataset.kind === "idle") {
      $overlay.hidden = true;
      $overlay.dataset.kind = "";
      clearTimer("idleCountdown");
    }
  }

  setInterval(function () {
    if (!S.session || ["printing", "pay", "scanning"].indexOf(S.screen) !== -1) return;
    var idle = (Date.now() - S.lastTouch) / 1000;
    if (idle > CFG.idleSeconds && $overlay.dataset.kind !== "idle") {
      var left = 15;
      $overlay.dataset.kind = "idle";
      $overlay.hidden = false;
      var paint = function () {
        $overlay.innerHTML = '<div class="box"><h1>' + esc(t("idleTitle")) + '</h1><p class="big muted">' + esc(t("idleHint", { s: left })) +
          '</p><button class="btn" data-act="stay">' + esc(t("stay")) + "</button></div>";
      };
      paint();
      every("idleCountdown", 1000, function () {
        left -= 1;
        if (left <= 0) {
          clearTimer("idleCountdown");
          if (S.job && S.job.state === "ANALYZED") cancelJob();
          logout();
        } else paint();
      });
    }
  }, 1000);

  // ---------------------------------------------------------------- eventos
  document.addEventListener("pointerdown", touch, true);

  document.addEventListener("click", function (ev) {
    var el = ev.target.closest("button");
    if (!el || el.disabled) return;
    var d = el.dataset;

    if (d.lang) {
      S.lang = d.lang;
      if (S.screen === "idle") paintIdle();
      return;
    }
    if (d.key !== undefined && S.screen === "phone") {
      if (d.key === "⌫") entry.phone = entry.phone.slice(0, -1);
      else if (d.key === "✓") { if (entry.phone.length === 9) sendCode(); return; }
      else if (entry.phone.length < 9) entry.phone += d.key;
      entry.error = "";
      paintPhone();
      return;
    }
    if (d.key !== undefined && S.screen === "code") {
      if (d.key === "⌫") entry.code = entry.code.slice(0, -1);
      else if (d.key === "✓") { if (entry.code.length === 6) verifyCode(); return; }
      else if (entry.code.length < 6) entry.code += d.key;
      entry.error = "";
      paintCode();
      if (entry.code.length === 6) verifyCode();
      return;
    }
    if (d.char !== undefined) {
      if (d.char === "⌫") entry.name = entry.name.slice(0, -1);
      else if (entry.name.length < 60) entry.name += d.char;
      paintName();
      return;
    }
    if (d.doc) return pickDoc(d.doc);
    if (d.usb !== undefined) return pickUsb(Number(d.usb));
    if (d.amount) return createTopup(Number(d.amount));
    if (d.opt === "copyDuplex") {
      copy.duplex = d.val === "1";
      paintCopy();
      return;
    }
    if (d.opt) {
      opts[d.opt] = d.val === "1";
      paintOptions();
      pushOptions();
      return;
    }
    if (d.step) {
      var n = Number(d.d);
      var pages = S.job.pages || 1;
      if (d.step === "copies") opts.copies = Math.min(50, Math.max(1, opts.copies + n));
      if (d.step === "from") opts.from = Math.min(opts.to, Math.max(1, opts.from + n));
      if (d.step === "to") opts.to = Math.min(pages, Math.max(opts.from, opts.to + n));
      paintOptions();
      pushOptions();
      return;
    }
    switch (d.act) {
      case "phone": return showPhone();
      case "cancel": return showIdle();
      case "sendCode": return sendCode();
      case "saveName": return saveName();
      case "home": return showHome();
      case "printMenu": return showPrintMenu();
      case "copy": return showCopy();
      case "scanStart": return startScan();
      case "scanMore": return scanMore();
      case "scanFinish": return finishScan();
      case "docs": return showDocs();
      case "upload": return showUpload();
      case "usb": return showUsb();
      case "logout": return logout();
      case "print": return print();
      case "topup": return showTopup();
      case "backToOptions": return refreshJob().then(paintOptionsScreen, paintOptionsScreen);
      case "cancelJob": return cancelJob();
      case "stay": return touch();
      case "blockedLogout":
        if (S.job && ["CREATED", "ANALYZED"].indexOf(S.job.state) !== -1) cancelJob();
        return logout();
    }
  });

  boot();
})();
