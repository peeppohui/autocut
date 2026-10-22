// Панель AutoCut: связь с Premiere и с сервером AutoCut.
//
// Панель сама ничего не решает. Она:
//   1) опрашивает сервер AutoCut (http://127.0.0.1:8931/next) и берёт
//      команду, если она есть;
//   2) выполняет команду в Premiere через evalScript — это ExtendScript,
//      тот же язык, что был в «Файл → Скрипты», только вызывается из
//      панели;
//   3) отправляет результат обратно на сервер.
//
// Связь с сервером — обычный HTTP: панель CEP умеет fetch, а слушает
// сервер на 127.0.0.1.

// Порт сервера: первый из списка, кто ответит. Список — потому что
// 8931 может быть занят, и тогда сервер встаёт на следующий.
var BRIDGE_PORTS = [8931, 8932, 8933];
var BRIDGE = "";
var POLL_MS = 1200;

function log(text, cls) {
  var box = document.getElementById("log");
  var line = document.createElement("div");
  if (cls) { line.className = cls; }
  line.textContent = text;
  box.appendChild(line);
  while (box.childNodes.length > 300) { box.removeChild(box.firstChild); }
  box.scrollTop = box.scrollHeight;
}

// --- вызов ExtendScript внутри Premiere -------------------------------
//
// CSInterface.js из состава CEP нет (его надо тащить), но и не нужно:
// нативная привязка __adobe_cep__.evalScript доступна всегда.
function evalScript(script) {
  try {
    if (window.__adobe_cep__ && window.__adobe_cep__.evalScript) {
      return window.__adobe_cep__.evalScript(script);
    }
  } catch (e) {
    return "ERR|" + e;
  }
  return "ERR|панель не в Premiere";
}

function winPath(p) {
  return String(p).replace(/\\/g, "/");
}

function q(s) {
  return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

// --- команды ------------------------------------------------------------

var COMMANDS = {
  // Импорт готовой рыбы. Главная команда: XML кладёт в проект и создаёт
  // секвенцию, и человеку не надо ничего кликать.
  import_xml: function (args) {
    var p = winPath(args.path || "");
    if (!p) { return "ERR|не указан путь"; }
    var script =
      "(function(){" +
      "try{" +
      "var f=new File(" + q(p) + ");" +
      "if(!f.exists){return 'ERR|нет файла ' + f.fsName;}" +
      "var r=app.project.importXML(f);" +
      "var n=0; try{n=r.length;}catch(e){n=0;}" +
      "return 'OK|импортировано объектов: ' + n;" +
      "}catch(e){return 'ERR|' + e.toString();}" +
      "})()";
    return evalScript(script);
  },

  // Что в проекте: имя, секвенции, активная.
  project_info: function () {
    var script =
      "(function(){try{" +
      "var p=app.project, s='', i;" +
      "s+='Проект: ' + p.name + '\\n';" +
      "s+='Файл: ' + p.path + '\\n';" +
      "s+='Секвенций: ' + p.sequences.length + '\\n';" +
      "for(i=0;i<p.sequences.length;i++){s+='  - ' + p.sequences[i].name + '\\n';}" +
      "if(p.activeSequence){s+='Активная: ' + p.activeSequence.name + '\\n';}" +
      "return 'OK|' + s;" +
      "}catch(e){return 'ERR|' + e.toString();}})()";
    return evalScript(script);
  },

  // Клипы активной секвенции: что монтажёр увидит после импорта.
  sequence_clips: function () {
    var script =
      "(function(){try{" +
      "var seq=app.project.activeSequence;" +
      "if(!seq){return 'ERR|нет активной секвенции';}" +
      "var out='Секвенция: ' + seq.name + '\\n', t, c, tr, cl;" +
      "for(t=0;t<seq.videoTracks.length;t++){" +
      "tr=seq.videoTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='V' + (t+1) + ' ' + cl.name + ' [' + cl.start.toFixed(2) + '..' + cl.end.toFixed(2) + ']\\n';" +
      "}}" +
      "for(t=0;t<seq.audioTracks.length;t++){" +
      "tr=seq.audioTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='A' + (t+1) + ' ' + cl.name + ' [' + cl.start.toFixed(2) + '..' + cl.end.toFixed(2) + ']\\n';" +
      "}}" +
      "return 'OK|' + out;" +
      "}catch(e){return 'ERR|' + e.toString();}})()";
    return evalScript(script);
  },

  // Маркер на активной секвенции.
  add_marker: function (args) {
    var t = Number(args.time || 0);
    var name = String(args.name || "AutoCut");
    var script =
      "(function(){try{" +
      "var seq=app.project.activeSequence;" +
      "if(!seq){return 'ERR|нет активной секвенции';}" +
      "seq.markers.addMarker(" + q(name) + ", " + t + ");" +
      "return 'OK|маркер добавлен';" +
      "}catch(e){return 'ERR|' + e.toString();}})()";
    return evalScript(script);
  }
};

// --- обмен с сервером --------------------------------------------------

function postResult(id, ok, data) {
  fetch(BRIDGE + "/result", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id, ok: ok, "данные": data })
  }).catch(function (e) {
    log("не отправил результат: " + e, "err");
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
  var res;
  try {
    res = fn(cmd.args || {});
  } catch (e) {
    res = "ERR|" + e;
  }
  var ok = String(res).indexOf("OK|") === 0;
  postResult(cmd.id, ok, String(res));
  log(ok ? "готово" : "ошибка", ok ? "ok" : "err");
}

var PORT_INDEX = 0;

// Ищем живой порт по очереди: сначала 8931, потом 8932, потом 8933.
// По очереди, а не сразу все: если опрашивать все разом, обработчики
// замыкаются на одну переменную, и панель подумает, что отвечает
// не тот сервер.
function probe(done) {
  if (BRIDGE) { done(true); return; }
  if (PORT_INDEX >= BRIDGE_PORTS.length) { done(false); return; }
  var port = BRIDGE_PORTS[PORT_INDEX];
  PORT_INDEX = PORT_INDEX + 1;
  var url = "http://127.0.0.1:" + port;
  fetch(url + "/ping")
    .then(function (r) { return r.json(); })
    .then(function () { BRIDGE = url; done(true); })
    .catch(function () { done(false); });
}

function poll() {
  probe(function (found) {
    if (!found) {
      setDot(false, "сервер AutoCut не отвечает");
      setTimeout(poll, POLL_MS);
      return;
    }
    fetch(BRIDGE + "/next")
      .then(function (r) { return r.json(); })
      .then(function (cmd) {
        setDot(true, "на связи");
        if (cmd && cmd.id) { handle(cmd); }
      })
      .catch(function () {
        setDot(false, "сервер AutoCut не отвечает");
      })
      .then(function () { setTimeout(poll, POLL_MS); });
  });
}

function setDot(on, text) {
  document.getElementById("dot").className = "dot " + (on ? "on" : "off");
  document.getElementById("state").textContent = text;
}

function checkProject() {
  var res = evalScript(COMMANDS.project_info({}));
  log(res, String(res).indexOf("OK|") === 0 ? "ok" : "err");
}

document.getElementById("btn-check").addEventListener("click", checkProject);
document.getElementById("btn-refresh").addEventListener("click", function () {
  poll();
});

log("панель поднялась, жду сервер…", "dim");
poll();
