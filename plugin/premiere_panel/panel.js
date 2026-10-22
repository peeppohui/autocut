// Панель AutoCut: связь с Premiere и с сервером AutoCut.
//
// Панель сама ничего не решает. Она:
//   1) находит сервер AutoCut (http://127.0.0.1:8931-8933) и забирает
//      команду, если она есть;
//   2) выполняет команду в Premiere через ExtendScript;
//   3) отправляет результат обратно на сервер.
//
// Про запросы — через Node (--enable-nodejs), а не через fetch: панель
// живёт в file://, и браузерный fetch к http://127.0.0.1 упирается в
// CORS. Node ходит без этих заморочек. Если Node недоступен —
// откатываемся на fetch.

var PORTS = [8931, 8932, 8933];
var PORT = 0;
var POLL_MS = 1200;

var NODE_HTTP = null;
var NODE_CEP = null;
try {
  if (window.require) {
    NODE_HTTP = window.require("http");
    try { NODE_CEP = window.require("cep"); } catch (e2) { NODE_CEP = null; }
  }
} catch (e) { NODE_HTTP = null; }

function log(text, cls) {
  sendLog(text);
  var box = document.getElementById("log");
  if (!box) { return; }
  var line = document.createElement("div");
  if (cls) { line.className = cls; }
  line.textContent = text;
  box.appendChild(line);
  while (box.childNodes.length > 300) { box.removeChild(box.firstChild); }
  box.scrollTop = box.scrollHeight;
}

// --- ExtendScript внутри Premiere ---------------------------------------
//
// ГЛАВНОЕ: в CEP 9+ у __adobe_cep__.evalScript ЕСТЬ колбэк. Без него
// результат молча выбрасывается (приходит undefined), а сам вызов
// ещё и подвисает. Это ровно то, что мешало: панель замирала на
// первой же команде.
//
// Скрипт всегда оборачивается так, чтобы результат лежал в
// переменной: evalScript отдаёт значение последнего выражения, и на
// сложном скрипте (функция, try/catch) это значение теряется.
function runScript(script, cb, timeoutMs) {
  var wrapped =
    "(function(){ try { __autocut_last__ = " + script +
    "; } catch(e) { __autocut_last__ = 'ERR|' + e.toString(); } })();";
  var read = "String(__autocut_last__);";
  var done = false;

  function finish(res) {
    if (done) { return; }
    done = true;
    clearTimeout(timer);
    if (res === undefined || res === null) {
      cb("ERR|пустой ответ");
    } else {
      cb(String(res));
    }
  }

  var limit = timeoutMs || 15000;
  var timer = setTimeout(function () {
    if (done) { return; }
    done = true;
    cb("ERR|ExtendScript не ответил за " + Math.round(limit / 1000) + " с");
  }, limit);

  if (!window.__adobe_cep__ || !window.__adobe_cep__.evalScript) {
    finish("ERR|панель не в Premiere");
    return;
  }
  try {
    window.__adobe_cep__.evalScript(wrapped, function () {
      if (done) { return; }
      window.__adobe_cep__.evalScript(read, function (res) {
        finish(res);
      });
    });
  } catch (e) {
    finish("ERR|" + e);
  }
}

// Лог панели уходит на сервер: состояние панели видно из агента, а не
// только глазами на экране Premiere.
var LOG_PENDING = [];

function sendLog(text) {
  LOG_PENDING.push(String(text));
  if (LOG_PENDING.length > 40) { LOG_PENDING.shift(); }
  if (!PORT) { return; }
  var chunk = LOG_PENDING.join("\n");
  LOG_PENDING = [];
  httpCall("POST", "/log", chunk, function () {});
}

// В Premiere 26 clip.start — объект времени, а не число: toFixed у него
// нет, и разбор падал на первом же клипе.
function fmt(v) {
  try { return Number(v).toFixed(2); } catch (e) { return String(v); }
}

function winPath(p) {
  return String(p).replace(/\\/g, "/");
}

function q(s) {
  return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

// --- команды ------------------------------------------------------------
//
// Каждая команда получает аргументы и колбэк: запуск ExtendScript
// асинхронный, и ответ уходит сразу после него.

var COMMANDS = {
  // Диагностика: чем именно мы говорим с Premiere.
  diag: function (args, cb) {
    var info = "node=" + (NODE_HTTP ? "да" : "нет") +
               " cep=" + (NODE_CEP ? "да" : "нет") +
               " native=" + (window.__adobe_cep__ && window.__adobe_cep__.evalScript
                             ? "да" : "нет") +
               " порт=" + (PORT || "-");
    runScript("'версия ' + app.version", function (res) {
      cb(info + " | ExtendScript: " + res);
    });
  },

  // Импорт готовой рыбы. Главная команда: XML кладёт в проект и создаёт
  // секвенцию, и человеку не надо ничего кликать.
  //
  // В ExtendScript Premiere 26 метода importXML НЕТ — проверено списком
  // имён. Зато есть app.project.importFiles: он берёт файл и сам
  // разбирается, что это. С FCP7 XML Premiere делает из него секвенцию
  // — ровно то, что нужно.
  import_xml: function (args, cb) {
    var p = winPath(args.path || "");
    if (!p) { cb("ERR|не указан путь"); return; }
    runScript(
      "(function(){try{" +
      "var f=new File(" + q(p) + ");" +
      "if(!f.exists){return 'ERR|нет файла ' + f.fsName;}" +
      // Именно строка в массиве: с объектом File Premiere ругается

      // «Illegal Parameter type». Проверено перебором вариантов.

      "var r=app.project.importFiles([f.fsName]);" +
      "var n=0; try{n=r.length;}catch(e){}" +
      "var seqs=app.project.sequences, names='';" +
      "for(var i=0;i<seqs.length;i++){names+=' | '+seqs[i].name;}" +
      "return 'OK|секвенций в проекте: '+seqs.length+names;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb, 180000);
  },

  // Что вообще есть в API Premiere: перечисляем свойства app и app.project.
  api: function (args, cb) {
    runScript(
      "(function(){try{" +
      "var o='', k, p=app.project;" +
      "for(k in p){o+='P.'+k+' ';} o+='|| ';" +
      "for(k in app){o+='A.'+k+' ';} " +
      "return 'OK|' + o;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Есть ли методы у проекта: for..in показывает только данные.
  api_methods: function (args, cb) {
    runScript(
      "(function(){try{return 'OK|' + " +
      "typeof app.project.importXML + '|' + " +
      "typeof app.project.createSequence + '|' + " +
      "typeof app.project.importFile + '|' + " +
      "typeof app.project.executeMenuCommand + '|' + " +
      "typeof app.executeMenuCommand + '|' + " +
      "typeof app.projectManager + '|' + " +
      "typeof app.project.rootItem;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Второй веер кандидатов: у Premiere в ExtendScript имена другие.
  api_methods2: function (args, cb) {
    runScript(
      "(function(){try{var p=app.project, a=app, o='';" +
      "o+='createSequence='+(typeof p.createSequence)+';';" +
      "o+='createBin='+(typeof p.createBin)+';';" +
      "o+='createItem='+(typeof p.createItem)+';';" +
      "o+='openSequence='+(typeof p.openSequence)+';';" +
      "o+='importFiles='+(typeof p.importFiles)+';';" +
      "o+='importXML='+(typeof p.importXML)+';';" +
      "o+='insertSequence='+(typeof p.insertSequence)+';';" +
      "o+='saveAs='+(typeof p.saveAs)+';';" +
      "o+='newSequence='+(typeof a.newSequence)+';';" +
      "o+='newProject='+(typeof a.newProject)+';';" +
      "o+='openDocument='+(typeof a.openDocument)+';';" +
      "o+='pm.importFiles='+(typeof a.projectManager.importFiles)+';';" +
      "o+='pm.openDocument='+(typeof a.projectManager.openDocument)+';';" +
      "o+='pm.createProject='+(typeof a.projectManager.createProject)+';';" +
      "o+='pm.projects='+(typeof a.projectManager.projects)+';';" +
      "return 'OK|' + o;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Открыть проект. Без открытого проекта импортировать некуда, а
  // Premiere после запуска может остаться с пустым проектом.
  open_project: function (args, cb) {
    var p = winPath(args.path || "");
    if (!p) { cb("ERR|не указан путь"); return; }
    runScript(
      "(function(){try{" +
      "var f=new File(" + q(p) + ");" +
      "if(!f.exists){return 'ERR|нет файла ' + f.fsName;}" +
      // openDocument в Premiere ждёт строку: с объектом File он ругается
      // «Illegal Parameter type».
      "var d=app.openDocument(f.fsName);" +
      "return 'OK|открыт проект: ' + d.name;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Открыть секвенцию в таймлайне: после импорта она лежит в проекте,
  // но активной не значится.
  open_sequence: function (args, cb) {
    var name = String(args.name || "");
    if (!name) { cb("ERR|не указано имя секвенции"); return; }
    runScript(
      "(function(){try{" +
      "var p=app.project, s, out='';" +
      // Способы разные: openSequence у проекта, у приложения и с путём.
      "try{ p.openSequence(" + q(name) + "); }catch(e){ out+='project.openSequence: '+e+'; '; }" +
      "s=p.activeSequence;" +
      "if(!s){ try{ app.openSequence(" + q(name) + "); }catch(e){ out+='app.openSequence: '+e+'; '; } s=p.activeSequence; }" +
      "if(!s){ var q2=p.sequences;" +
      "  for(var i=0;i<q2.length;i++){ if(q2[i].name===" + q(name) + "){ try{ p.openSequence(q2[i].name); }catch(e){} } }" +
      "  s=p.activeSequence; }" +
      "return 'OK|активная: ' + (s ? s.name : 'нет') + out;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb, 30000);
  },

  // Что в проекте: имя, секвенции, активная.
  project_info: function (args, cb) {
    runScript(
      "(function(){try{" +
      "var p=app.project, s='', i;" +
      "s+='Проект: ' + p.name + '\\n';" +
      "s+='Файл: ' + p.path + '\\n';" +
      "s+='Секвенций: ' + p.sequences.length + '\\n';" +
      "for(i=0;i<p.sequences.length;i++){s+='  - ' + p.sequences[i].name + '\\n';}" +
      "if(p.activeSequence){s+='Активная: ' + p.activeSequence.name + '\\n';}" +
      "return 'OK|' + s;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Все секвенции проекта с клипами по дорожкам: проверять, что импорт
  // лёг куда надо.
  sequences: function (args, cb) {
    runScript(
      "(function(){try{" +
      "var out='', i, t, c, tr, cl, s;" +
      "var f=function(v){ if(typeof v==='number'){return v.toFixed(2);} if(v&&typeof v.seconds==='number'){return v.seconds.toFixed(2);} if(v&&typeof v.value==='number'){return v.value.toFixed(2);} return String(v); };" +
      "for(i=0;i<app.project.sequences.length;i++){" +
      "s=app.project.sequences[i];" +
      "out+='СЕКВЕНЦИЯ ' + s.name + '\\n';" +
      "for(t=0;t<s.videoTracks.length;t++){" +
      "tr=s.videoTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='  V' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + '] тип=' + (typeof cl.start) + '\\n';" +
      "}}" +
      "for(t=0;t<s.audioTracks.length;t++){" +
      "tr=s.audioTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='  A' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
      "}}" +
      "}" +
      "return 'OK|' + out;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb, 30000);
  },

  // Клипы активной секвенции: что монтажёр увидит после импорта.
  sequence_clips: function (args, cb) {
    runScript(
      "(function(){try{" +
      "var seq=app.project.activeSequence;" +
      "if(!seq){return 'ERR|нет активной секвенции';}" +
      "var out='Секвенция: ' + seq.name + '\\n', t, c, tr, cl;" +
      "var f=function(v){ if(typeof v==='number'){return v.toFixed(2);} if(v&&typeof v.seconds==='number'){return v.seconds.toFixed(2);} if(v&&typeof v.value==='number'){return v.value.toFixed(2);} return String(v); };" +
      "for(t=0;t<seq.videoTracks.length;t++){" +
      "tr=seq.videoTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='V' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
      "}}" +
      "for(t=0;t<seq.audioTracks.length;t++){" +
      "tr=seq.audioTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='A' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
      "}}" +
      "return 'OK|' + out;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  },

  // Маркер на активной секвенции.
  add_marker: function (args, cb) {
    var t = Number(args.time || 0);
    var name = String(args.name || "AutoCut");
    runScript(
      "(function(){try{" +
      "var seq=app.project.activeSequence;" +
      "if(!seq){return 'ERR|нет активной секвенции';}" +
      "seq.markers.addMarker(" + q(name) + ", " + t + ");" +
      "return 'OK|маркер добавлен';" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  }
};

// --- обмен с сервером --------------------------------------------------

function httpCall(method, path, body, cb) {
  if (NODE_HTTP && PORT) {
    var data = body ? Buffer.from(body, "utf8") : null;
    var headers = {};
    if (data) {
      headers["Content-Type"] = "text/plain;charset=utf-8";
      headers["Content-Length"] = data.length;
    }
    var req = NODE_HTTP.request({host: "127.0.0.1", port: PORT, path: path,
                                 method: method, headers: headers},
      function (res) {
        var chunks = [];
        res.on("data", function (c) { chunks.push(c); });
        res.on("end", function () {
          cb(null, Buffer.concat(chunks).toString("utf8"));
        });
      });
    req.on("error", function (e) { cb(e, ""); });
    if (data) { req.write(data); }
    req.end();
    return;
  }
  if (!PORT) { cb(new Error("нет порта"), ""); return; }
  var opts = {method: method};
  if (body) {
    opts.headers = {"Content-Type": "text/plain;charset=utf-8"};
    opts.body = body;
  }
  fetch("http://127.0.0.1:" + PORT + path, opts)
    .then(function (r) { return r.text(); })
    .then(function (t) { cb(null, t); })
    .catch(function (e) { cb(e, ""); });
}

function postResult(id, ok, data) {
  httpCall("POST", "/result",
           JSON.stringify({id: id, ok: ok, "данные": data}),
           function (err) {
             if (err) { log("не отправил результат: " + err, "err"); }
           });
}

function handle(cmd) {
  if (!cmd || !cmd.id) { return; }
  var fn = COMMANDS[cmd.cmd];
  if (!fn) {
    postResult(cmd.id, false, "ERR|неизвестная команда " + cmd.cmd);
    return;
  }
  log("команда: " + cmd.cmd, "dim");
  fn(cmd.args || {}, function (res) {
    var text = String(res === undefined || res === null ? "ERR|нет ответа" : res);
    var ok = text.indexOf("OK|") === 0;
    postResult(cmd.id, ok, text);
    log(ok ? "готово" : "ошибка: " + text.slice(0, 120), ok ? "ok" : "err");
  });
}

// Порт ищем по очереди: 8931 может быть занят. По очереди, а не все
// сразу: если опрашивать все разом, замыкания в обработчиках делят
// одну переменную, и панель решит, что отвечает не тот сервер.
var PORT_INDEX = 0;

function findPort(done) {
  if (PORT) { done(true); return; }
  if (PORT_INDEX >= PORTS.length) {
    // Порты кончились — начинаем сначала. Иначе панель, поднявшаяся
    // раньше сервера, выжигает список и больше не пробует никогда.
    PORT_INDEX = 0;
    done(false);
    return;
  }
  var port = PORTS[PORT_INDEX++];
  if (NODE_HTTP) {
    var req = NODE_HTTP.request({host: "127.0.0.1", port: port,
                                 path: "/ping", method: "GET"},
      function (res) {
        res.resume();
        res.on("end", function () { PORT = port; done(true); });
      });
    req.on("error", function () { done(false); });
    req.end();
    return;
  }
  fetch("http://127.0.0.1:" + port + "/ping")
    .then(function (r) { return r.json(); })
    .then(function () { PORT = port; done(true); })
    .catch(function () { done(false); });
}

function poll() {
  findPort(function (found) {
    if (!found) {
      setDot(false, "сервер AutoCut не отвечает");
      setTimeout(poll, 400);
      return;
    }
    httpCall("GET", "/next", null, function (err, text) {
      if (err) {
        setDot(false, "сервер AutoCut не отвечает");
      } else {
        setDot(true, "на связи");
        var cmd = {};
        try { cmd = JSON.parse(text || "{}"); } catch (e) { cmd = {}; }
        if (cmd && cmd.id) { handle(cmd); }
      }
      setTimeout(poll, POLL_MS);
    });
  });
}

function setDot(on, text) {
  var d = document.getElementById("dot");
  var s = document.getElementById("state");
  if (d) { d.className = "dot " + (on ? "on" : "off"); }
  if (s) { s.textContent = text; }
}

function checkProject() {
  runScript("app.project.name", function (res) {
    log(res, String(res).indexOf("OK|") === 0 ? "ok" : "err");
  });
}

window.addEventListener("load", function () {
  var b1 = document.getElementById("btn-check");
  var b2 = document.getElementById("btn-refresh");
  if (b1) { b1.addEventListener("click", checkProject); }
  if (b2) {
    b2.addEventListener("click", function () {
      PORT = 0;
      PORT_INDEX = 0;
      PORTS = [8931, 8932, 8933];
      poll();
    });
  }
  log("панель поднялась, жду сервер…", "dim");
  poll();
});
