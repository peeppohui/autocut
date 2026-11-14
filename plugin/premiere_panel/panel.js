// Панель AutoCut в Premiere Pro.
//
// Панель — интерфейс. Тяжёлое делает движок AutoCut (тот же, что
// отвечает по MCP), панель спрашивает его по HTTP и показывает ход
// работы. Готовая рыба импортируется в проект отсюда: человеку не
// нужно ничего кликать в Premiere.
//
// Про запросы — через Node (--enable-nodejs): панель живёт в file://,
// и браузерный fetch к http://127.0.0.1 упирается в CORS.

var PORTS = [8931, 8932, 8933];
var PORT = 0;
var PORT_INDEX = 0;
var POLL_MS = 1500;
var TASK = "";            // номер текущей фоновой задачи
var TIMER = null;

// В CEP Node бывает в трёх местах: глобальный require, cep_node.require
// и window.require. В Premiere 26 window.require пуст, поэтому старый
// вариант молча оставался без Node: панель рисовалась, но не могла
// ни запустить движок, ни прочитать результат ExtendScript.
function nodeRequire(name) {
  try {
    if (typeof require !== "undefined") { return require(name); }
  } catch (e) { }
  try {
    var cep = (typeof cep_node !== "undefined") ? cep_node :
              (typeof window !== "undefined" ? window.cep_node : null);
    if (cep && typeof cep.require === "function") { return cep.require(name); }
  } catch (e) { }
  try {
    if (typeof window !== "undefined" && window.require) {
      return window.require(name);
    }
  } catch (e) { }
  return null;
}

// Node-http в CEP зависает на 127.0.0.1: запрос уходит и не возвращается
// ни ошибкой, ни ответом. Fetch из той же панели работает, а мост отдаёт
// Access-Control-Allow-Origin: *, поэтому ходим через него.
var NODE_HTTP = null;
var NODE_BUFFER = nodeRequire("buffer");
var BUF = (NODE_BUFFER && NODE_BUFFER.Buffer) ? NODE_BUFFER.Buffer :
          (typeof Buffer !== "undefined" ? Buffer : null);

function log(text, cls) {
  var box = document.getElementById("log");
  if (!box) { return; }
  var line = document.createElement("div");
  if (cls) { line.className = cls; }
  line.textContent = text;
  box.appendChild(line);
  while (box.childNodes.length > 400) { box.removeChild(box.firstChild); }
  box.scrollTop = box.scrollHeight;
  sendLog(text);
}

var LOG_PENDING = [];
function sendLog(text) {
  LOG_PENDING.push(String(text));
  if (LOG_PENDING.length > 30) { LOG_PENDING.shift(); }
  if (!PORT) { return; }
  var chunk = LOG_PENDING.join("\n");
  LOG_PENDING = [];
  httpCall("POST", "/log", chunk, function () {});
}

function httpCall(method, path, body, cb) {
  if (NODE_HTTP && PORT) {
    var data = body && BUF ? BUF.from(body, "utf8") : null;
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
          cb(null, BUF ? BUF.concat(chunks).toString("utf8") :
                 String(chunks));
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

function postJson(path, obj, cb) {
  httpCall("POST", path, JSON.stringify(obj), function (err, text) {
    if (err) { cb(err, null); return; }
    try { cb(null, JSON.parse(text || "{}")); }
    catch (e) { cb(null, {}); }
  });
}

function getJson(path, cb) {
  httpCall("GET", path, null, function (err, text) {
    if (err) { cb(err, null); return; }
    try { cb(null, JSON.parse(text || "{}")); }
    catch (e) { cb(null, {}); }
  });
}

function setDot(on, text) {
  var d = document.getElementById("dot");
  var s = document.getElementById("state");
  if (d) { d.className = "dot " + (on ? "on" : "off"); }
  if (s) { s.textContent = text; }
}

function findPort(done) {
  if (PORT) { done(true); return; }
  if (PORT_INDEX >= PORTS.length) {
    // Порты кончились — начинаем сначала. Иначе панель, поднявшаяся
    // раньше движка, выжигает список и больше не пробует никогда.
    PORT_INDEX = 0;
    done(false);
    return;
  }
  var port = PORTS[PORT_INDEX++];
  if (NODE_HTTP) {
    var settled = false;
    var timer = null;
    function finish(ok) {
      if (settled) { return; }
      settled = true;
      if (timer) { clearTimeout(timer); }
      done(ok);
    }
    var req = NODE_HTTP.request({host: "127.0.0.1", port: port,
                                 path: "/ping", method: "GET"},
      function (res) {
        res.resume();
        res.on("end", function () { PORT = port; finish(true); });
      });
    req.on("error", function () { finish(false); });
    // Без таймаута запрос к занятому, но зависшему порту не завершается
    // никогда: панель навсегда остаётся на «проверяю движок…» и даже
    // не пытается запустить свой. req.setTimeout здесь недостаточно:
    // он взводится только когда сокет уже подключился.
    timer = setTimeout(function () {
      try { req.destroy(); } catch (e) { }
      finish(false);
    }, 1200);
    req.end();
    return;
  }
  fetch("http://127.0.0.1:" + port + "/ping")
    .then(function (r) { return r.json(); })
    .then(function () { PORT = port; done(true); })
    .catch(function () { done(false); });
}

// --- ExtendScript в Premiere --------------------------------------------
//
// Две особенности Premiere 26, обе проверены на живой панели:
//
// 1) __adobe_cep__.evalScript с колбэком НЕ работает: панель намертво
//    зависает, и опросы сервера прекращаются. Синхронный вызов
//    (без колбэка) скрипт выполняет, но результат НЕ возвращает —
//    приходит всегда undefined.
// 2) Поэтому результат идёт через файл: скрипт пишет его на диск,
//    панель читает файл через Node.
//
// Отсюда правило: скрипт ВСЕГДА заканчивается записью в RESULT_FILE.

var NODE_FS = nodeRequire("fs");
var NODE_OS = nodeRequire("os");
var NODE_PATH = nodeRequire("path");
var NODE_CHILD = nodeRequire("child_process");

var RESULT_FILE = "";
try {
  // Node в панели Premiere 26 может не загрузиться (проверено: os
  // появляется, http и fs нет), поэтому путь к результату нужен и
  // без Node. Расширение ставится в папку пользователя — она
  // редактируемая, файл результата ложится рядом с panel.js.
  RESULT_FILE = (NODE_OS ? NODE_OS.tmpdir() : workDir()) + "/autocut_result.txt";
} catch (e) { RESULT_FILE = "autocut_result.txt"; }

// Каталог панели из file:///... URL — запасной tmpdir без Node.
function workDir() {
  try {
    var m = String(window.location.pathname || "").match(/^(.+)\/[^/]+$/);
    return m ? decodeURI(m[1]) : ".";
  } catch (e) { return "."; }
}

// Аварийный журнал панели. Если HTML отрисовался, но скрипт упал,
// в обычном логе CEP не будет ни строки — пишем ошибки сами.
var PANEL_LOG = "";
try {
  PANEL_LOG = (NODE_OS ? NODE_OS.tmpdir() : ".") + "/autocut_panel.log";
} catch (e) { PANEL_LOG = "autocut_panel.log"; }

function fileLog(text) {
  if (!NODE_FS) { return; }
  try {
    NODE_FS.appendFileSync(PANEL_LOG,
      new Date().toISOString() + " " + String(text) + "\n");
  } catch (e) { }
}

window.onerror = function (msg, src, line, col, err) {
  fileLog("ERROR " + msg + " at " + src + ":" + line + ":" + col +
          (err && err.stack ? "\n" + err.stack : ""));
};

window.addEventListener("load", function () {
  fileLog("panel loaded");
});

// Путь к движку записывает установщик панели. В исходниках панель
// лежит рядом с run_mcp.py, поэтому для разработки есть запасной путь.
var ENGINE_FILE = "";
var ENGINE_PYTHON_FILE = "";
var ENGINE_ENV_FILE = "";
try {
  if (NODE_PATH) {
    var panelDir = "";
    if (typeof __dirname !== "undefined") {
      panelDir = __dirname;
    } else if (window.location && window.location.pathname) {
      panelDir = decodeURIComponent(window.location.pathname)
        .replace(/^\/([A-Za-z]:)/, "$1");
      panelDir = NODE_PATH.dirname(panelDir);
    }
    if (panelDir) {
      ENGINE_FILE = NODE_PATH.join(panelDir, "engine_path.txt");
      ENGINE_PYTHON_FILE = NODE_PATH.join(panelDir, "engine_python.txt");
      ENGINE_ENV_FILE = NODE_PATH.join(panelDir, "engine_env.txt");
    }
  }
} catch (e) { ENGINE_FILE = ""; }

var ENGINE_STARTED = false;

function readEngineText(path) {
  if (!NODE_FS || !path) { return ""; }
  try { return String(NODE_FS.readFileSync(path, "utf8")).trim(); }
  catch (e) { return ""; }
}

function engineDir() {
  var dir = readEngineText(ENGINE_FILE);
  if (!dir && NODE_PATH && typeof __dirname !== "undefined") {
    var parent = NODE_PATH.resolve(__dirname, "..");
    try {
      if (NODE_FS && NODE_FS.existsSync(NODE_PATH.join(parent, "run_mcp.py"))) {
        dir = parent;
      }
    } catch (e) { }
  }
  return dir;
}

// Портативный ffmpeg лежит в .tools рядом с движком. Ищем его сами:
// установщик может не знать, куда именно распаковали архив.
function localFfmpegDir(dir) {
  if (!NODE_FS || !NODE_PATH) { return ""; }
  var base = NODE_PATH.resolve(dir, "..", ".tools", "ffmpeg");
  try {
    var names = NODE_FS.readdirSync(base);
    for (var i = 0; i < names.length; i++) {
      var bin = NODE_PATH.join(base, names[i], "bin");
      if (NODE_FS.existsSync(NODE_PATH.join(bin, "ffmpeg.exe"))) {
        return bin;
      }
    }
  } catch (e) { }
  return "";
}

// Запуск движка из панели. Пользователь не должен открывать консоль:
// панель поднимает run_mcp.py сама, если он ещё не слушает порт.
function startEngine() {
  if (ENGINE_STARTED || !NODE_CHILD || !NODE_FS || !NODE_PATH) { return; }
  var dir = engineDir();
  if (!dir) { return; }
  var script = NODE_PATH.join(dir, "run_mcp.py");
  try {
    if (!NODE_FS.existsSync(script)) { return; }
  } catch (e) { return; }

  var logPath = NODE_PATH.join(dir, "autocut_engine.log");

  // Окружение: панель может запускать портативный Python, которого нет
  // в PATH. Установщик кладёт пути в engine_env.txt, а для разработки
  // ищем ffmpeg в .tools сами.
  var env = {};
  try { for (var k in process.env) { env[k] = process.env[k]; } } catch (e) { }
  var extra = readEngineText(ENGINE_ENV_FILE) || localFfmpegDir(dir);
  if (extra) {
    env.PATH = extra + ";" + (env.PATH || "");
  }
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUTF8 = "1";

  var names = [];
  var configured = readEngineText(ENGINE_PYTHON_FILE);
  if (configured) { names.push(configured); }
  try {
    var localPython = NODE_PATH.resolve(dir, "..", ".tools",
                                        "python311", "python.exe");
    if (NODE_FS.existsSync(localPython)) { names.push(localPython); }
  } catch (e) { }
  names.push("python");
  names.push("py");

  var idx = 0;
  function attempt() {
    if (idx >= names.length) {
      log("движок не запустился: python не найден", "err");
      return;
    }
    var exe = names[idx++];
    var probe = null;
    try {
      probe = NODE_CHILD.spawnSync(exe, ["--version"],
                                   {windowsHide: true, env: env});
    } catch (e) { probe = null; }
    if (!probe || probe.error || probe.status !== 0) { attempt(); return; }
    var out = "ignore";
    try { out = NODE_FS.openSync(logPath, "a"); } catch (e) { out = "ignore"; }
    try {
      var child = NODE_CHILD.spawn(exe, [script, "--bridge-only"], {
        cwd: dir,
        detached: true,
        windowsHide: true,
        env: env,
        stdio: ["ignore", out, out]
      });
      child.on("error", function () {
        ENGINE_STARTED = false;
        attempt();
      });
      child.unref();
      ENGINE_STARTED = true;
      log("движок AutoCut запускается: " + exe, "dim");
    } catch (e) { attempt(); }
    try { if (out !== "ignore") { NODE_FS.closeSync(out); } } catch (e) { }
  }
  attempt();
}

function evalScript(script, cb) {
  try {
    if (!(window.__adobe_cep__ && window.__adobe_cep__.evalScript)) {
      return "ERR|панель не в Premiere";
    }

    // Обычный CEP отдаёт результат ТОЛЬКО через колбэк: вызов с одним
    // аргументом нигде не выполняется и значение не возвращает. Файл
    // здесь запасной канал: если колбэк не пришёл, ExtendScript пишет
    // результат в него, и мы читаем через cep.fs.
    window.__adobe_cep__.evalScript(script, cb || function() {});
    return "";
  } catch (e) {
    return "ERR|" + e;
  }
}

function readResult() {
  // Node в этой панели может не загрузиться, тогда остаётся
  // встроенная ФС CEP: window.cep.fs.readFile -> {err, data}.
  if (window.cep && window.cep.fs && window.cep.fs.readFile) {
    try { return String(window.cep.fs.readFile(RESULT_FILE).data || ""); }
    catch (e) { }
  }
  if (!NODE_FS) { return ""; }
  try {
    return NODE_FS.readFileSync(RESULT_FILE, "utf8");
  } catch (e) {
    return "";
  }
}

// Выполнить скрипт и отдать его результат.
// Результат едут двумя путями: синхронный возврат evalScript (не
// везде работает) и файл. Маркер в начале файла страхует от
// чтения результата прошлого вызова, если свой не успел записаться.
function runScript(script, cb, maxTries) {
  try { if (NODE_FS) { NODE_FS.unlinkSync(RESULT_FILE); } } catch (e) { }
  var token = "r" + (new Date()).getTime();
  var wrapped =
    "(function(){" +
    "var __r='';" +
    "try{ __r=" + script + "; }catch(e){ __r='ERR|'+e.toString(); }" +
    "try{ var f=new File(" + q(RESULT_FILE) + ");" +
    "if(f.open('w')){ f.write('" + token + "\\n'+String(__r)); f.close(); } }catch(e){}" +
    "return __r;})()";
  var ready = false;
  var sync = evalScript(wrapped, function(res) {
    // Канал колбэка — основной в CEP: использовать первым.
    if (!ready && typeof res === "string" && res && res !== "undefined") {
      ready = true; cb(res);
    }
  });
  var tries = 0;
  (function wait() {
    // Синхронный возврат в Premiere 26 пустой, но где-то работает.
    if (!ready && typeof sync === "string" && sync && sync !== "undefined") {
      cb(sync); ready = true; return;
    }
    var res = readResult() || "";
    var mark = token + "\n";
    if (res.indexOf(mark) === 0) { cb(res.slice(mark.length)); ready = true; return; }
    // Импорт XML и большой readback проекта идут дольше 5 секунд: без
    // запаса панель сделала бы работу и «потеряла» результат, как уже
    // случилось с import_xml. 600 шагов по 50 мс = запас 30 с.
    // Импорт «Отбора» (83 клипа) длится дольше, поэтому у него свой
    // запас ожидания — сколько_нибудь секунд на минуту.
    if (ready || tries++ > (maxTries || 599)) { if (!ready) { cb("ERR|не дождались результата"); } return; }
    setTimeout(wait, 50);
  })();
}

function winPath(p) {
  return String(p).replace(/\\/g, "/");
}

function q(s) {
  return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

// Импорт готовой рыбы в проект и открытие секвенции.
// В Premiere 26 importXML нет — то же ветвление, что в команде import_xml.
function importXml(path, cb) {
  var script =
    "(function(){try{" +
    "var f=new File(" + q(winPath(path)) + ");" +
    "if(!f.exists){return 'ERR|нет файла ' + f.fsName;}" +
    "if(typeof(app.project.importXML)==='function'){app.project.importXML(f);}" +
    "else{app.project.importFiles(f.fsName,false,null,false);}" +
    "return 'OK|XML импортирован: ' + f.name;" +
    "}catch(e){return 'ERR|' + e.toString();}})()";
  runScript(script, function (res) {
    log("импорт: " + res, String(res).indexOf("OK|") === 0 ? "ok" : "err");
    showSequences();
    if (cb) { cb(res); }
  }, 3600);
}

// --- команды ------------------------------------------------------------
//
// Команды приходят с сервера. Каждая получает аргументы и колбэк:
// запуск ExtendScript асинхронный по сути (результат приходит из файла),
// и ответ уходит сразу после него.

var COMMANDS = {
  // Диагностика: чем именно мы говорим с Premiere.
  diag: function (args, cb) {
    var ceps = window.cep && window.cep.fs && window.cep.fs.readFile;
    var info = "node=" + (NODE_HTTP ? "да" : "нет") +
               " fs=" + (NODE_FS ? "да" : "нет") +
               " cepfs=" + (ceps ? "да" : "нет") +
               " native=" + (window.__adobe_cep__ && window.__adobe_cep__.evalScript
                             ? "да" : "нет") +
               " порт=" + (PORT || "-") +
               " файл=" + RESULT_FILE;
    runScript("'версия ' + app.version", function (res) {
      // Ответ начинается с OK|, чтобы /result отмечал диагностику
      // как успешную, когда ExtendScript реально ответил.
      var ok = (res !== undefined && String(res).indexOf("ERR|") !== 0);
      cb((ok ? "OK|" : "ERR|") + info + " | ExtendScript: " + res);
    });
  },

  // Отладочная дверь: выполнить произвольное ExtendScript-выражение.
  // Без неё поиск правильного API (например, импорта XML) требует
  // переустановки панели на каждой попытке.
  probe: function (args, cb) {
    var expr = String(args.expr || "'версия ' + app.version");
    runScript(expr, function (res) { cb(String(res)); });
  },

  // Импорт готовой рыбы в проект: XML ложится в проект и создаёт
  // секвенцию, человек ничего не кликает.
  //
  // В Premiere 26 нет `app.project.importXML` (доказано probe-командой
  // в живом проекте), зато `importFiles` с XML в аргументах импортирует
  // XML вместе с секвенцией. Выбор делаем в ExtendScript: имя проверки
  // через `typeof` не должно ронять строку.
  import_xml: function (args, cb) {
    var p = winPath(args.path || "");
    if (!p) { cb("ERR|не указан путь"); return; }
    runScript(
      "(function(){try{" +
      "var f=new File(" + q(p) + ");" +
      "if(!f.exists){return 'ERR|нет файла ' + f.fsName;}" +
      "if(typeof(app.project.importXML)==='function'){app.project.importXML(f);}" +
      "else{app.project.importFiles(f.fsName,false,null,false);}" +
      "return 'OK|XML импортирован: ' + f.name;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb, 3600);
  },

  // Что в проекте: секвенции, дорожки, клипы.
  project_info: function (args, cb) {
    runScript(
      "(function(){try{" +
      "var out='', i, t, c, tr, cl, s;" +
      "var f=function(v){ if(typeof v==='number'){return v.toFixed(2);} " +
      "if(v&&typeof v.seconds==='number'){return v.seconds.toFixed(2);} " +
      "return String(v); };" +
      "for(i=0;i<app.project.sequences.length;i++){" +
      "s=app.project.sequences[i];" +
      "out+='СЕКВЕНЦИЯ ' + s.name + '\\n';" +
      "for(t=0;t<s.videoTracks.length;t++){" +
      "tr=s.videoTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='  V' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
      "}}" +
      "for(t=0;t<s.audioTracks.length;t++){" +
      "tr=s.audioTracks[t];" +
      "for(c=0;c<tr.clips.length;c++){" +
      "cl=tr.clips[c];" +
      "out+='  A' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
      "}}" +
      "}" +
      "return 'OK|' + out;" +
      "}catch(e){return 'ERR|' + e.toString();}})()", cb);
  }
};

// --- очередь команд ------------------------------------------------------
//
// Агент и панель разговаривают через одну очередь на движке. Раньше
// команды клались в неё и висели: панель их не забирала, поэтому
// `premiere_import` ждал ответа, которого никто не отправлял.
//
// Сервер держит `/next` до 20 секунд, так что это long-poll: запрос
// висит, пока команда не появится, и не грузит процесс.

var CMDBUSY = false;

function pollCommands() {
  if (!PORT) { setTimeout(pollCommands, 1000); return; }
  if (CMDBUSY) { return; }
  CMDBUSY = true;
  getJson("/next", function (err, cmd) {
    CMDBUSY = false;
    if (err || !cmd || !cmd.cmd) { setTimeout(pollCommands, 700); return; }
    runCommand(cmd);
  });
}

function runCommand(cmd) {
  var fn = COMMANDS[cmd.cmd];
  var finished = false;
  var done = function (res) {
    if (finished) { return; }
    finished = true;
    var text = String(res == null ? "" : res);
    var ok = text.indexOf("OK|") === 0;
    log("команда " + cmd.cmd + ": " + text, ok ? "ok" : "err");
    postJson("/result", {id: cmd.id, ok: ok, "данные": text},
             function () { pollCommands(); });
  };
  if (!fn) { done("ERR|неизвестная команда " + cmd.cmd); return; }
  try {
    fn(cmd.args || {}, done);
  } catch (e) {
    done("ERR|" + e.toString());
  }
}

// --- действия ------------------------------------------------------------

// --- вид панели -----------------------------------------------------------
//
// Карточка источника, футер-черновик и тумблеры — это вид, а не логика.
// Логика в движке: панель только спрашивает и рисует.

function humanTime(sec) {
  // «3 ч 42 мин» из секунд: так человек читает объём материала.
  var s = Math.max(0, Math.round(sec || 0));
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  if (h) { return h + " ч " + m + " мин"; }
  if (m) { return m + " мин"; }
  return s + " с";
}

function timecode(sec, fps) {
  // 00:01:00:00 — часы:минуты:секунды:кадры, как в Premiere.
  fps = fps || 25;
  var s = Math.max(0, sec || 0);
  var f = Math.floor((s % 1) * fps);
  var total = Math.floor(s);
  var hh = Math.floor(total / 3600);
  var mm = Math.floor((total % 3600) / 60);
  var ss = total % 60;
  function two(v) { return (v < 10 ? "0" : "") + v; }
  return two(hh) + ":" + two(mm) + ":" + two(ss) + ":" + two(f);
}

function shortTime(sec) {
  var s = Math.max(0, Math.round(sec || 0));
  var m = Math.floor(s / 60);
  return (m < 10 ? "0" : "") + m + ":" + ((s % 60) < 10 ? "0" : "") + (s % 60);
}

// Сколько секунд в исходниках: нужно для подписи «вместо X отсмотра».
var SOURCE_SECONDS = 0;

function showSource(folder, files, seconds) {
  SOURCE_SECONDS = seconds || 0;
  var name = document.getElementById("source-name");
  var meta = document.getElementById("source-meta");
  if (name) {
    var parts = String(folder || "").replace(/[\\/]+$/, "").split(/[\\/]/);
    name.textContent = parts[parts.length - 1] || "—";
    name.title = folder || "";
  }
  if (meta) {
    meta.textContent = files + " клипов · " + humanTime(seconds);
  }
}

// Флаги тумблеров: панель их собирает и отдаёт движку.
function flags() {
  var out = {};
  var rows = document.querySelectorAll(".switch");
  for (var i = 0; i < rows.length; i++) {
    var b = rows[i];
    out[b.getAttribute("data-flag")] = !b.classList.contains("off");
  }
  return out;
}

// Черновик в футере: сколько клипов и из чего собрано.
function drawDraft(data) {
  var title = document.getElementById("draft-title");
  var time = document.getElementById("draft-time");
  var tl = document.getElementById("draft-timeline");
  var note = document.getElementById("draft-note");
  if (!data) { return; }
  var blocks = data.блоки || [];
  var clips = data.клипов || 0;
  var secs = data.секунд || 0;
  if (title) {
    title.textContent = "ЧЕРНОВИК · " + clips + " КЛИПОВ";
  }
  if (time) { time.textContent = timecode(secs); }
  if (tl) {
    tl.innerHTML = "";
    // Блоки с реальными позициями: мини-таймлайн должен совпадать с
    // монтажом, иначе картинка врёт. Нарезка рисуется поверх голоса —
    // так же, как в Premiere.
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      var d = document.createElement("div");
      var from = typeof b.начало === "number" ? b.начало : 0;
      var to = typeof b.конец === "number" ? b.конец : (b.секунд || 0);
      var left = secs > 0 ? (from / secs) * 100 : 0;
      var width = secs > 0 ? ((to - from) / secs) * 100 : 0;
      d.style.left = Math.max(0, Math.min(100, left)) + "%";
      d.style.width = Math.max(0.4, Math.min(100 - left, width)) + "%";
      d.style.background = PART_COLORS[b.часть] || "#7C4DFF";
      d.title = b.часть + ": " + Math.round(to - from) + " с";
      tl.appendChild(d);
    }
  }
  if (note) {
    var src = data.исходников_секунд || SOURCE_SECONDS || 0;
    note.textContent = secs > 0
      ? (src > 0
          ? "Вместо " + humanTime(src) + " отсмотра — " +
            Math.round(secs) + " с готовой «рыбы»"
          : Math.round(secs) + " с готовой «рыбы» на таймлайне")
      : "Вместо 3 ч отсмотра — готовая «рыба» на таймлайне";
  }
}

var PART_COLORS = {
  "стендап": "#7C4DFF",
  "интервью": "#5163EE",
  "нарезка": "#5E7BFF",
  "музыка": "#4D8DFF"
};

function refreshDraft() {
  var folder = document.getElementById("folder").value.trim();
  if (!folder) { return; }
  postJson("/action", {action: "summary", args: {folder: folder}},
           function (err, res) {
             if (err || !res || !res.ok) { return; }
             drawDraft(res);
           });
}

function findMaterial() {
  var folder = document.getElementById("folder").value.trim();
  if (!folder) { log("укажите папку с материалом", "err"); return; }
  log("ищу материал в " + folder, "dim");
  postJson("/action", {action: "material", args: {folder: folder}},
           function (err, res) {
             if (err || !res) { log("движок не отвечает", "err"); return; }
             if (!res.ok) { log(res.error || "не нашлось", "err"); return; }
             var parts = res.части || {};
             var out = [];
             var files = 0, seconds = 0;
             for (var k in parts) {
               files += parts[k].файлов || 0;
               seconds += parts[k].секунд || 0;
               out.push(k + ": " + parts[k].файлов + " шт, " +
                        parts[k].секунд + " с");
             }
             showSource(folder, files, seconds);
             document.getElementById("parts").textContent = out.join("   |   ");
             log("найдено: " + out.join("  |  "), "ok");
             refreshDraft();
           });
}

// Выбор папки: диалог Premiere. Если диалог недоступен (или его
// закрыли), показываем поле ввода — панель не должна упираться в
// одно окно.
function pickFolder() {
  var script =
    "(function(){try{" +
    "var f=Folder.selectDialog('Выберите папку с материалом');" +
    "if(!f){return 'ERR|отменено';}" +
    "return 'OK|' + f.fsName;" +
    "}catch(e){return 'ERR|' + e.toString();}})()";
  runScript(script, function (res) {
    var text = String(res || "");
    if (text.indexOf("OK|") !== 0) {
      log("папка не выбрана", "dim");
      return;
    }
    var input = document.getElementById("folder");
    input.value = text.slice(3);
    try { window.localStorage.setItem("autocut.folder", input.value); }
    catch (e) { }
    findMaterial();
  });
}

function buildDigest() {
  var folder = document.getElementById("folder").value.trim();
  if (!folder) { log("укажите папку с материалом", "err"); return; }
  var target = document.getElementById("target").value.trim();
  var out = folder.replace(/[\\/]+$/, "") + "/autocut_out";
  log("собираю дайджест, это минуты", "dim");
  var args = {folder: folder, out: out, target_sec: target ? Number(target) : null};
  // Флаги тумблеров: движок сам решает, что с ними делать.
  var f = flags();
  for (var k in f) { args[k] = f[k]; }
  postJson("/action", {
    action: "digest",
    args: args
  }, function (err, res) {
    if (err || !res || !res.ok) {
      log("движок не взял задачу: " + (res && res.error ? res.error : "?"), "err");
      return;
    }
    TASK = res.задача;
    log("задача " + TASK + ", слежу", "dim");
    watchTask();
  });
}

function cancelTask() {
  if (!TASK) { log("нет активной задачи", "dim"); return; }
  setDot(false, "отменяю…");
  postJson("/action", {action: "cancel", args: {task_id: TASK}},
           function (err, res) {
             if (err || !res || !res.ok) {
               log("не удалось отменить: " +
                   (res && res.error ? res.error : "?"), "err");
               return;
             }
             log("отмена запрошена", "dim");
             watchTask();
           });
}

function runDoctor() {
  log("проверяю движок…", "dim");
  postJson("/action", {action: "doctor", args: {}}, function (err, res) {
    if (err || !res) { log("движок не отвечает", "err"); return; }
    if (res.ok) {
      log("движок готов: Python " + (res.python || "?") + ", ffmpeg есть", "ok");
    } else {
      log("не хватает: " + ((res.ошибки || []).join("; ") || "?"), "err");
    }
    var mods = res.модули || {};
    var missing = [];
    for (var k in mods) { if (!mods[k]) { missing.push(k); } }
    if (missing.length) { log("нет модулей: " + missing.join(", "), "err"); }
    if (res.свободно_гб) {
      log("свободно на диске: " + res.свободно_гб + " ГБ", "dim");
    }
  });
}

// Импортируем несколько XML по очереди: секвенций две — «Сбор»
// (рыба) и «Отбор» (не взятые исходники). Порядок не важен, но
// список собираем в порядке шагов задачи.
function importXmlList(paths, i, cb) {
  if (!paths.length || i >= paths.length) { cb(true); return; }
  var xml = paths[i];
  importXml(xml, function (res) {
    if (String(res).indexOf("OK|") !== 0) { cb(false); return; }
    importXmlList(paths, i + 1, cb);
  });
}

function watchTask() {
  if (TIMER) { clearTimeout(TIMER); }
  if (!TASK) { return; }
  getJson("/task?id=" + TASK, function (err, t) {
    if (err || !t) { TIMER = setTimeout(watchTask, 1500); return; }
    var st = t.состояние || "";
    if (st === "идёт" || st === "в очереди") {
      var steps = t.результаты || [];
      var step = steps.length ? (steps[steps.length - 1].шаг || "") : "";
      var msg = "собираю… " + Math.round(t.секунд || 0) + " с";
      if (step) { msg += " · " + step; }
      setDot(true, msg);
      TIMER = setTimeout(watchTask, 2000);
      return;
    }
    if (st === "готово") {
      TASK = "";
      setDot(true, "готово, импортирую в проект");
      // Все XML из результатов шагов: и «Сбор», и «Отбор».
      var xmls = [];
      ((t.результаты || []).forEach(function (r) {
        var p = r.результат || "";
        if (p && /\.xml$/i.test(p) && xmls.indexOf(p) < 0) { xmls.push(p); }
      }));
      var xml = (t.результат && t.результат.xml) || "";
      if (xml && xmls.indexOf(xml) < 0) { xmls.push(xml); }
      if (!xmls.length) { log("XML не указан", "err"); return; }
      importXmlList(xmls, 0, function (okAll) {
        setDot(okAll, okAll ? "готово" : "ошибка импорта");
      });
      return;
    }
    if (st === "отменено") {
      TASK = "";
      setDot(false, "отменено");
      log("задача отменена", "dim");
      return;
    }
    TASK = "";
    setDot(false, "ошибка");
    log("задача провалилась: " + (t.ошибка || "?"), "err");
    (t.журнал || []).slice(-20).forEach(function (l) { log("  " + l, "dim"); });
  });
}

function showSequences() {
  var script =
    "(function(){try{" +
    "var out='', i, t, c, tr, cl, s;" +
    "var f=function(v){ if(typeof v==='number'){return v.toFixed(2);} " +
    "if(v&&typeof v.seconds==='number'){return v.seconds.toFixed(2);} " +
    "return String(v); };" +
    "for(i=0;i<app.project.sequences.length;i++){" +
    "s=app.project.sequences[i];" +
    "out+='СЕКВЕНЦИЯ ' + s.name + '\\n';" +
    "for(t=0;t<s.videoTracks.length;t++){" +
    "tr=s.videoTracks[t];" +
    "for(c=0;c<tr.clips.length;c++){" +
    "cl=tr.clips[c];" +
    "out+='  V' + (t+1) + ' ' + cl.name + ' [' + f(cl.start) + '..' + f(cl.end) + ']\\n';" +
    "}}" +
    "}" +
    "return 'OK|' + out;" +
    "}catch(e){return 'ERR|' + e.toString();}})()";
  runScript(script, function (res) {
  if (String(res).indexOf("OK|") !== 0) { log("проект: " + res, "err"); return; }
  var text = String(res).slice(3);
  document.getElementById("parts").textContent = text.split("\n")[0] || "";
  text.split("\n").slice(0, 24).forEach(function (l) { log(l, "dim"); });
  });
}

function checkProject() {
  showSequences();
}

// --- запуск --------------------------------------------------------------

function poll() {
  findPort(function (found) {
    if (!found) {
      setDot(false, "движок AutoCut не отвечает");
      startEngine();
      setTimeout(poll, 1000);
      return;
    }
    setDot(true, "движок на связи");
    if (TASK) { watchTask(); }
  });
}

// --- вид: слайдер, тумблеры, чипы ---------------------------------------

function initView() {
  // Длительность: монотаймкод рядом со слайдером.
  var slider = document.getElementById("target");
  var dur = document.getElementById("dur-value");
  function syncDur() {
    if (!dur) { return; }
    dur.textContent = shortTime(Number(slider.value || 0));
  }
  if (slider) {
    slider.addEventListener("input", syncDur);
    syncDur();
  }

  // Тумблеры: включен/выключен меняет вид и запоминается.
  var sw = document.querySelectorAll(".switch");
  for (var i = 0; i < sw.length; i++) {
    (function (btn) {
      var key = "autocut.flag." + btn.getAttribute("data-flag");
      try {
        var saved = window.localStorage.getItem(key);
        if (saved === "0") { btn.classList.add("off"); }
        if (saved === "1") { btn.classList.remove("off"); }
      } catch (e) { }
      btn.addEventListener("click", function () {
        var off = btn.classList.toggle("off");
        btn.setAttribute("aria-checked", off ? "false" : "true");
        try { window.localStorage.setItem(key, off ? "0" : "1"); }
        catch (e) { }
      });
    })(sw[i]);
  }

  // Чипы типа ролика: активный — с градиентом.
  var chips = document.querySelectorAll(".chip");
  for (var j = 0; j < chips.length; j++) {
    (function (chip) {
      chip.addEventListener("click", function () {
        if (chip.disabled) { return; }
        for (var k = 0; k < chips.length; k++) {
          chips[k].classList.remove("active");
        }
        chip.classList.add("active");
        try {
          window.localStorage.setItem("autocut.kind", chip.getAttribute("data-kind"));
        } catch (e) { }
      });
    })(chips[j]);
  }

  // Прошлую папку помним: каждый раз искать заново неудобно.
  var input = document.getElementById("folder");
  try {
    var savedFolder = window.localStorage.getItem("autocut.folder");
    if (savedFolder && input) {
      input.value = savedFolder;
      showSource(savedFolder, 0, 0);
      findMaterial();
    }
  } catch (e) { }
}

window.addEventListener("load", function () {
  var bFind = document.getElementById("btn-find");
  var bBuild = document.getElementById("btn-digest");
  var bCheck = document.getElementById("btn-check");
  var bCancel = document.getElementById("btn-cancel");
  var bDoctor = document.getElementById("btn-doctor");
  var bRefresh = document.getElementById("btn-refresh");
  if (bFind) { bFind.addEventListener("click", pickFolder); }
  if (bBuild) { bBuild.addEventListener("click", buildDigest); }
  if (bCheck) { bCheck.addEventListener("click", checkProject); }
  if (bCancel) { bCancel.addEventListener("click", cancelTask); }
  if (bDoctor) { bDoctor.addEventListener("click", runDoctor); }
  if (bRefresh) {
    bRefresh.addEventListener("click", function () {
      PORT = 0;
      PORTS = [8931, 8932, 8933];
      poll();
    });
  }
  initView();
  log("панель AutoCut поднялась", "dim");
  poll();
  pollCommands();
});
