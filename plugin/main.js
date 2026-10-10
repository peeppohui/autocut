/* AutoCut — панель UXP для Premiere Pro.
 *
 * Ограничение UXP, которое определило всю архитектуру:
 * запустить процесс и забрать его вывод панель не может. У UXP есть только
 * shell.openPath() — открыть файл в приложении по умолчанию. Никакого
 * child_process в Premiere нет (это только Hybrid Plugins, которых в
 * Premiere пока не существует).
 *
 * Поэтому схема такая:
 *   1. панель пишет autocut_job.json рядом с run_job.bat;
 *   2. открывает run_job.bat через shell.openPath() — пользователь один раз
 *      подтверждает запуск в системном диалоге;
 *   3. панель опрашивает autocut_result.json, пока он не появится и не
 *      обновится, и показывает отчёт.
 *
 * Прогресс в реальном времени показать нельзя: вывод процесса панели недоступен.
 * Поэтому статус честно пишет «идёт расчёт», а подробности — по завершении.
 */

const { entrypoints, shell } = require("uxp");

const PLUGIN_DIR = "plugin:/";
const JOB_FILE = PLUGIN_DIR + "autocut_job.json";
const RESULT_FILE = PLUGIN_DIR + "autocut_result.json";
const RUNNER = PLUGIN_DIR + "run_job.bat";
const SETTINGS = PLUGIN_DIR + "src/config/settings.json";

const POLL_MS = 1200;

const state = {
  mode: "interview",
  running: false,
  timer: null,
  lastFinished: null,
  outDir: "",
};

/* --------------------------------------------------------------- лог */

const logEl = document.querySelector("#log");
const statusEl = document.querySelector("#status");

function log(msg) {
  const stamp = new Date().toTimeString().slice(0, 8);
  logEl.textContent += `[${stamp}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

/* --------------------------------------------------------------- настройки */

async function readSettings() {
  const fs = require("uxp").storage.localFileSystem;
  try {
    const f = await fs.getEntry(SETTINGS);
    const txt = await f.read();
    return JSON.parse(txt);
  } catch (e) {
    log("Не прочитал settings.json: " + e + ". Возьму значения по умолчанию.");
    return {};
  }
}

function pick(obj, dotted, fallback) {
  let node = obj;
  for (const part of dotted.split(".")) {
    if (node === null || node === undefined || !(part in node)) return fallback;
    node = node[part];
  }
  return node === undefined ? fallback : node;
}

/* --------------------------------------------------------------- поля */

const FIELDS = {
  interview: [
    { id: "ref", label: "Общий план", type: "file", video: true, required: true },
    { id: "cam", label: "Камера гостя", type: "file", video: true },
    { id: "mic_guest", label: "Микр. гостя", type: "file" },
    { id: "mic_host", label: "Микр. ведущего", type: "file" },
    { id: "sync", label: "Синхронизировать", type: "check", def: true,
      hint: "Сводит камеры и микрофоны по звуку. Без хлопка работает по речи." },
    { id: "conform", label: "Привести к", type: "text", def: "",
      hint: "Например 1920x1080@25. Пусто — гостя подстроит автоматически." },
    { id: "out_dir", label: "Результат", type: "dir", required: true },
  ],
  digest: [
    { id: "input", label: "Папка съёмки", type: "dir", required: true },
    { id: "music", label: "Музыка", type: "file" },
    { id: "target_sec", label: "Хронометраж", type: "number", def: 60,
      hint: "Секунд. 0 — не ужимать." },
    { id: "device", label: "Устройство", type: "select",
      options: ["auto", "cpu", "cuda"] },
    { id: "out_dir", label: "Результат", type: "dir", required: true },
  ],
  news: [
    { id: "spec", label: "Spec JSON", type: "file", required: true,
      hint: "Описание материала: синхроны, перебивки, закадр, титры." },
    { id: "out_dir", label: "Результат", type: "dir", required: true },
  ],
};

function values() {
  const out = {};
  document.querySelectorAll("#params [data-id]").forEach((el) => {
    if (el.type === "checkbox") out[el.dataset.id] = el.checked;
    else out[el.dataset.id] = el.value.trim();
  });
  return out;
}

async function pickFile(input, folder) {
  try {
    const fs = require("uxp").storage.localFileSystem;
    const opts = folder
      ? { folders: true }
      : {
          types: ["mp4", "mov", "mxf", "avi", "mkv", "m4v", "mts", "wav", "mp3", "csv"],
        };
    const e = await fs.getFileForOpening(opts);
    if (!e) return;
    input.value = e.nativePath.replace(/\\/g, "/");
    log("Выбрано: " + e.name);
  } catch (err) {
    log("Не удалось открыть выбор файла: " + err);
  }
}

function renderParams(mode, settings) {
  const host = document.querySelector("#params");
  host.innerHTML = "";
  const fields = FIELDS[mode] || [];
  if (!fields.length) {
    host.innerHTML = '<div class="hint">Режим «Сюжет» в этой сборке ещё не готов.</div>';
    return;
  }
  for (const f of fields) {
    const row = document.createElement("div");
    row.className = "row";
    const label = document.createElement("label");
    label.textContent = f.label + (f.required ? " *" : "");
    row.appendChild(label);

    if (f.type === "check") {
      const wrap = document.createElement("div");
      wrap.className = "check";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.dataset.id = f.id;
      cb.checked = !!f.def;
      wrap.appendChild(cb);
      row.appendChild(wrap);
    } else if (f.type === "select") {
      const sel = document.createElement("select");
      sel.dataset.id = f.id;
      for (const o of f.options) {
        const opt = document.createElement("option");
        opt.value = o;
        opt.textContent = o;
        sel.appendChild(opt);
      }
      sel.value = pick(settings, `${mode}.device`, f.def || "auto");
      row.appendChild(sel);
    } else {
      const wrap = document.createElement("div");
      wrap.className = "pick";
      const inp = document.createElement("input");
      inp.type = f.type === "number" ? "number" : "text";
      inp.dataset.id = f.id;
      let def = f.def;
      if (f.id === "target_sec") def = pick(settings, "digest.target_sec", 60);
      if (f.id === "out_dir") def = state.outDir || "";
      inp.value = def === undefined ? "" : String(def);
      const btn = document.createElement("button");
      btn.textContent = "…";
      btn.addEventListener("click", () => pickFile(inp, f.type === "dir"));
      wrap.appendChild(inp);
      wrap.appendChild(btn);
      row.appendChild(wrap);
    }
    host.appendChild(row);
    if (f.hint) {
      const h = document.createElement("div");
      h.className = "hint";
      h.textContent = f.hint;
      host.appendChild(h);
    }
  }
}

/* --------------------------------------------------------------- файлы */

async function writeFile(url, content) {
  const fs = require("uxp").storage.localFileSystem;
  const f = await fs.createEntryWithUrl(url, { overwrite: true });
  await f.write(content);
}

async function readJson(url) {
  const fs = require("uxp").storage.localFileSystem;
  try {
    const f = await fs.getEntry(url);
    return JSON.parse(await f.read());
  } catch (e) {
    return null;
  }
}

/* --------------------------------------------------------------- запуск */

async function run() {
  if (state.running) return;
  const vals = values();

  const missing = (FIELDS[state.mode] || [])
    .filter((f) => f.required && !String(vals[f.id] || "").trim())
    .map((f) => f.label);
  if (missing.length) {
    setStatus("Заполни: " + missing.join(", "), "err");
    return;
  }
if (state.mode === "news") {
    setStatus("Режим «Сюжет» в этой сборке ещё не готов.", "warn");
  }

  const outDir = String(vals.out_dir || "").replace(/[\\/]+$/, "");
  const job = {
    mode: state.mode,
    out_dir: outDir,
    params: Object.assign({}, vals, {
      target_sec: vals.target_sec !== undefined ? Number(vals.target_sec) || 0 : undefined,
    }),
  };
  delete job.params.out_dir;

  // Забываем прошлый результат, иначе опрос поймает его мгновенно.
  state.lastFinished = null;
  try {
    await writeFile(JOB_FILE, JSON.stringify(job, null, 2));
    log("Задание записано: " + JOB_FILE);
    log("Параметры: " + JSON.stringify(job.params));
  } catch (e) {
    setStatus("Не смог записать задание: " + e, "err");
    return;
  }

  state.outDir = outDir;
  try {
    setStatus("Запускаю расчёт…", "busy");
    state.running = true;
    document.querySelector("#run").disabled = true;
    document.querySelector("#cancel").disabled = false;
    await shell.openPath(
      RUNNER,
      "AutoCut запускает локальный расчёт рыбы на Python " +
      "(Whisper/YOLO работаут вне Premiere)."
    );
    log("run_job.bat запущен. Считаю результат…");
    poll();
  } catch (e) {
    state.running = false;
    document.querySelector("#run").disabled = false;
    document.querySelector("#cancel").disabled = true;
    setStatus("Premiere не дал запустить: " + e, "err");
    log("Ошибка запуска: " + e);
  }
}

function poll() {
  if (!state.running) return;
  state.timer = setTimeout(async () => {
    const r = await readJson(RESULT_FILE);
    if (r && r.finished && r.finished !== state.lastFinished) {
      state.lastFinished = r.finished;
      finish(r);
      return;
    }
    setStatus("Идёт расчёт… (детали появятся после завершения)", "busy");
    poll();
  }, POLL_MS);
}

function finish(r) {
  state.running = false;
  document.querySelector("#run").disabled = false;
  document.querySelector("#cancel").disabled = true;

  log("--- отчёт расчёта ---");
  for (const line of r.log || []) log("  " + line);

  const bad = (r.steps || []).filter((s) => s.code !== 0);
  if (r.status === "ok") {
    setStatus("Готово за " + r.started.slice(11) + "–" + r.finished.slice(11), "ok");
  } else if (r.status === "not_implemented") {
    setStatus(r.log[r.log.length - 1] || "Режим не реализован.", "warn");
  } else {
    setStatus(
      "Ошибка на шаге «" + (bad.length ? bad[0].name : "?") + "» (код " +
        (bad.length ? bad[0].code : "?") + ")", "err");
  }

  const host = document.querySelector("#artifacts");
  host.innerHTML = "";
  for (const a of r.artifacts || []) {
    const el = document.createElement("a");
    el.textContent = a;
    el.href = "#";
    el.addEventListener("click", (e) => {
      e.preventDefault();
      log("Файл: " + state.outDir + "/" + a);
    });
    host.appendChild(el);
  }
}

function cancel() {
  if (!state.running) return;
  state.running = false;
  if (state.timer) clearTimeout(state.timer);
  document.querySelector("#run").disabled = false;
  document.querySelector("#cancel").disabled = true;
  setStatus("Ожидание снято. Расчёт может продолжаться — результат появится в папке.", "warn");
  log("Ожидание снято.");
}

/* --------------------------------------------------------------- вход */

async function init(root) {
  document.querySelector("#ver").textContent = "0.4.0";

  document.querySelectorAll(".mode").forEach((b) => {
    b.addEventListener("click", () => {
      if (state.running) return;
      state.mode = b.dataset.mode;
      document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      if (b.dataset.soon) {
        setStatus("Режим «Сюжет» в этой сборке ещё не готов.", "warn");
      } else {
        setStatus("Заполни пути и нажми «Собрать рыбу».", "");
      }
    });
  });

  document.querySelector("#run").addEventListener("click", run);
  document.querySelector("#cancel").addEventListener("click", cancel);
  document.querySelector("#clearlog").addEventListener("click", () => {
    logEl.textContent = "";
  });

  const settings = await readSettings();
  log("Панель загружена. Пороги читаю из settings.json.");
  document.querySelector('.mode[data-mode="interview"]').click();
}

module.exports = {
  entrypoints: {
    panels: {
      autocutPanel: {
        show(root) {
          init(root);
        },
        hide() {},
        destroy() {
          if (state.timer) clearTimeout(state.timer);
        },
      },
    },
  },
};