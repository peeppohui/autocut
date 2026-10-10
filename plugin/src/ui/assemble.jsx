/* AutoCut Assemble v2 — сборка рыбы ВНУТРИ Premiere Pro.
 *
 * Что делает: читает timeline CSV (наш selects.csv / timeline.csv),
 * находит файлы в бинах проекта по имени, создаёт секвенцию и раскладывает
 * куски по дорожкам с точными in/out, ставит маркеры с причинами склейки.
 * Плюс чинит офлайн-медиа (диалог Link Media) по указанной папке.
 *
 * Как запустить:
 *   1. Импортируй исходники в проект (File > Import, папка со съёмки).
 *      Для интервью импортируй и синхронизированные файлы из synced/.
 *   2. File > Scripts > Run Script File > выбери assemble.jsx.
 *   3. Укажи CSV таймлайна, затем папку с медиа (для релинка, можно пропустить).
 *
 * Дорожки (колонка track в CSV):
 *   пусто  -> V1 со связанным звуком на A1 (как раньше)
 *   V1..V4 -> видео на videoTracks[n-1], звук на audioTracks[n-1]
 *   A2..A4 -> только звук на audioTracks[n-1] (подложка, закадр)
 *
 * Колонка audio: отдельный звуковой файл (микрофон говорящего).
 * Если она есть, звук берётся из него, а не из видеоклипа.
 *
 * Нейросети внутрь Premiere не засунуть: detect.py / interview.py по-прежнему
 * готовятся снаружи (Python), сам монтаж — здесь.
 */

(function () {
    app.beginUndoGroup("AutoCut assemble");

    function log(m) { $.writeln("[autocut] " + m); }

    /* --- мини CSV-парсер (кавычки и запятые внутри полей держит) --- */
    function parseCSV(text) {
        var rows = [], cur = [], field = "", inQ = false, i, ch;
        for (i = 0; i < text.length; i++) {
            ch = text.charAt(i);
            if (inQ) {
                if (ch === '"') {
                    if (text.charAt(i + 1) === '"') { field += '"'; i++; }
                    else { inQ = false; }
                } else { field += ch; }
            } else {
                if (ch === '"') { inQ = true; }
                else if (ch === ",") { cur.push(field); field = ""; }
                else if (ch === "\n") { cur.push(field); rows.push(cur); cur = []; field = ""; }
                else if (ch === "\r") { /* skip */ }
                else { field += ch; }
            }
        }
        if (field !== "" || cur.length > 0) { cur.push(field); rows.push(cur); }
        return rows;
    }

    /* --- рекурсивный поиск клипа по имени файла во всём проекте ---
       Типы не сравниваем числами (различаются по версиям): у бина есть
       children, у клипа — getMediaPath. Результаты запоминаем, иначе на
       каждой строке заново обходим весь проект. */
    function isBin(item) {
        try { return item.children && typeof item.children.numItems !== "undefined"; }
        catch (e) { return false; }
    }
    function baseName(p) { return (p + "").replace(/\\/g, "/").split("/").pop().toLowerCase(); }

    var cache = {};
    function findClip(node, fileName) {
        var want = baseName(fileName);
        if (cache[want] !== undefined) { return cache[want] || null; }
        var found = searchClip(node, want);
        if (found) { cache[want] = found; return found; }
        return null;
    }
    function searchClip(node, want) {
        var i, item, hit, found;
        for (i = 0; i < node.children.numItems; i++) {
            item = node.children[i];
            try {
                if (isBin(item)) {
                    found = searchClip(item, want);
                    if (found) { return found; }
                } else {
                    hit = false;
                    try { if (item.name && baseName(item.name) === want) { hit = true; } } catch (e1) {}
                    if (!hit) {
                        try {
                            var mp = item.getMediaPath();
                            if (mp && baseName(mp) === want) { hit = true; }
                        } catch (e2) {}
                    }
                    if (hit) { return item; }
                }
            } catch (e3) {}
        }
        return null;
    }

    function collectFiles(folder, out) {
        var files = folder.getFiles(), i;
        for (i = 0; i < files.length; i++) {
            if (files[i] instanceof Folder) { collectFiles(files[i], out); }
            else { out[files[i].name.toLowerCase()] = files[i].fsName; }
        }
    }

    /* --- диалоги --- */
    var csvFile = File.openDialog("AutoCut: выбери timeline CSV (selects.csv / timeline.csv)", "*.csv", false);
    if (!csvFile) { log("отмена: CSV не выбран"); app.endUndoGroup(); return; }
    csvFile.encoding = "UTF-8";
    csvFile.open("r");
    var rows = parseCSV(csvFile.read());
    csvFile.close();
    if (rows.length < 2) { alert("AutoCut: в CSV нет строк."); app.endUndoGroup(); return; }

    var header = rows[0];
    function col(name) {
        for (var j = 0; j < header.length; j++) {
            if (header[j].toLowerCase() === name) { return j; }
        }
        return -1;
    }
    var cFile = col("file"), cPlan = col("plan"), cHero = col("hero"),
        cDur = col("duration"), cIn = col("in"), cOut = col("out"), cDesc = col("desc"),
        cTrack = col("track"), cAudio = col("audio");
    if (cFile < 0) { alert("AutoCut: в CSV нет колонки file."); app.endUndoGroup(); return; }

    var mediaFolder = Folder.selectDialog("AutoCut: папка с исходниками для релинка (можно Отмена)");
    var mediaMap = {};
    if (mediaFolder) { collectFiles(mediaFolder, mediaMap); }

    /* --- релинк офлайна по именам (убирает диалог Link Media) --- */
    function relinkAll(node) {
        var i, item, fixed = 0;
        for (i = 0; i < node.children.numItems; i++) {
            item = node.children[i];
            try {
                if (isBin(item)) { fixed += relinkAll(item); }
                else {
                    var off = false;
                    try { off = item.isOffline(); } catch (e) {}
                    var nm = "";
                    try { nm = item.name; } catch (e4) {}
                    if (off && nm && mediaMap[nm.toLowerCase()]) {
                        try {
                            if (item.canChangeMediaPath && item.canChangeMediaPath()) {
                                item.changeMediaPath(mediaMap[nm.toLowerCase()]);
                                fixed++;
                            }
                        } catch (e2) { log("релинк не удался: " + nm); }
                    }
                }
            } catch (e3) {}
        }
        return fixed;
    }
    var relinked = 0;
    if (mediaFolder) {
        relinked = relinkAll(app.project.rootItem);
        log("перелинковано: " + relinked);
    }

    /* --- секвенция: открытая активная либо новая --- */
    var seq = app.project.activeSequence;
    if (!seq) {
        try {
            seq = app.project.createNewSequence("AutoCut_рыба", "autocut-" + new Date().getTime());
        } catch (e) { alert("AutoCut: не смог создать секвенцию: " + e); app.endUndoGroup(); return; }
    }
    log("секвенция: " + seq.name);

    /* Перезапись бьёт по содержимому дорожек, поэтому в непустую
       секвенцию без спроса ничего не кладём. Старая версия вставляла
       поверх и тоже портила монтаж — просто тихо. */
    function seqHasClips(s) {
        var i, n = 0;
        try {
            for (i = 0; i < s.videoTracks.numTracks; i++) {
                n += s.videoTracks[i].clips.numItems;
            }
        } catch (e) {}
        try {
            for (i = 0; i < s.audioTracks.numTracks; i++) {
                n += s.audioTracks[i].clips.numItems;
            }
        } catch (e2) {}
        return n;
    }
    var existing = seqHasClips(seq);
    if (existing > 0) {
        var go = confirm(
            "AutoCut: секвенция «" + seq.name + "» уже не пустая (" + existing +
            " клипов).\n\nСобрать рыбу ПОВЕРХ текущего монтажа? " +
            "Отмена — закрыть скрипт, не трогав ничего.");
        if (!go) {
            log("отмена: секвенция не пустая, ничего не тронуто");
            app.endUndoGroup();
            return;
        }
    }

    function num(s, fb) { var v = parseFloat(s); return isNaN(v) ? fb : v; }

    /* --- дорожки: сколько нужно, столько и создаём --- */
    function ensureVideoTracks(n) {
        while (seq.videoTracks.numTracks < n) {
            try { seq.videoTracks.addTrack(); }
            catch (e) { log("не смог добавить видеодорожку: " + e); break; }
        }
    }
    function ensureAudioTracks(n) {
        while (seq.audioTracks.numTracks < n) {
            try { seq.audioTracks.addTrack(); }
            catch (e) { log("не смог добавить аудиодорожку: " + e); break; }
        }
    }

    /* Куда класть строку.
       Возвращает {video: индекс видеодорожки или -1, audio: индекс аудио или -1} */
    function routeOf(tr) {
        tr = (tr || "").toUpperCase().replace(/\s/g, "");
        if (/^V\d+$/.test(tr)) {
            var vi = parseInt(tr.substring(1), 10);
            if (vi >= 1 && vi <= 4) { return { video: vi - 1, audio: vi - 1 }; }
        }
        if (/^A\d+$/.test(tr)) {
            var ai = parseInt(tr.substring(1), 10);
            if (ai >= 1 && ai <= 4) { return { video: -1, audio: ai - 1 }; }
        }
        return { video: 0, audio: 0 };
    }

    var t = 0, placed = 0, missing = [], markers = 0, trackStats = {};

    for (var r = 1; r < rows.length; r++) {
        var row = rows[r];
        if (!row || row.length <= cFile || !row[cFile]) { continue; }
        var fname = row[cFile].replace(/^\s+|\s+$/g, "");
        var base = baseName(fname);
        var route = routeOf(cTrack >= 0 ? row[cTrack] : "");

        var item = findClip(app.project.rootItem, base);
        if (!item) { missing.push(base); log("нет в проекте: " + base); continue; }

        var dur = cDur >= 0 ? num(row[cDur], 3) : 3;
        var hasExact = (cIn >= 0 && cOut >= 0 && row[cIn] !== "" && row[cOut] !== "");
        var inS = hasExact ? num(row[cIn], 0) : 0;
        var outS = hasExact ? num(row[cOut], inS + dur) : inS + dur;
        if (!(outS > inS)) { outS = inS + dur; }

        // отдельный звук (микрофон) из колонки audio
        var audioItem = null;
        if (cAudio >= 0 && row[cAudio]) {
            var an = baseName(row[cAudio]);
            audioItem = findClip(app.project.rootItem, an);
            if (!audioItem) { log("нет микрофона в проекте: " + an); }
        }

        var okThis = false;
        try {
            // source in/out: сначала "все типы" (4), иначе видео+аудио по отдельности.
            // ExtendScript — это ES3, forEach здесь может отсутствовать,
            // поэтому обычный цикл.
            var srcItems = [item, audioItem];
            for (var q = 0; q < srcItems.length; q++) {
                var it = srcItems[q];
                if (!it) { continue; }
                try { it.setInPoint(inS, 4); it.setOutPoint(outS, 4); }
                catch (e4) {
                    try { it.setInPoint(inS, 1); it.setOutPoint(outS, 1); } catch (e5) {}
                    try { it.setInPoint(inS, 0); it.setOutPoint(outS, 0); } catch (e6) {}
                }
            }

            if (route.video >= 0) {
                ensureVideoTracks(route.video + 1);
                ensureAudioTracks(route.audio + 1);
                // overwriteItem кладёт картинку на videoTracks[n] и связанный
                // звук на audioTracks[n]. Если нет — insertClip по дорожкам.
                var putVideo = false;
                try { seq.overwriteItem(item, t, route.video); putVideo = true; }
                catch (e7) {
                    try { seq.videoTracks[route.video].overwriteItem(item, t); putVideo = true; }
                    catch (e8) {
                        try { seq.insertClip(item, t, 0, route.video); putVideo = true; }
                        catch (e9) {
                            try { seq.videoTracks[route.video].insertClip(item, t); putVideo = true; }
                            catch (e10) { log("видео не вставилось " + base + ": " + e10); }
                        }
                    }
                }
                if (putVideo) { okThis = true; }
            }

            // звук: из микрофона, если задан, иначе из самого видеоклипа
            if (route.audio >= 0) {
                var src = audioItem || (route.video >= 0 ? null : item);
                if (src) {
                    ensureAudioTracks(route.audio + 1);
                    var putAudio = false;
                    try { seq.audioTracks[route.audio].overwriteItem(src, t); putAudio = true; }
                    catch (e11) {
                        try { seq.audioTracks[route.audio].insertClip(src, t); putAudio = true; }
                        catch (e12) {
                            try { seq.insertClip(src, t, -1, route.audio); putAudio = true; }
                            catch (e13) { log("звук не вставился " + base + ": " + e13); }
                        }
                    }
                    if (putAudio && route.video < 0) { okThis = true; }
                } else if (route.video < 0) {
                    log("нет источника звука для строки: " + base);
                }
            }
        } catch (e11b) {
            missing.push(base + " (ошибка вставки: " + e11b + ")");
            log("ошибка вставки " + base + ": " + e11b);
        }

        if (okThis) {
            placed++;
            var key = (route.video >= 0 ? "V" + (route.video + 1) : "-") +
                      "/" + "A" + (route.audio + 1);
            trackStats[key] = (trackStats[key] || 0) + 1;
        }

        // маркер на начало куска: план, герой и причина склейки
        if (okThis) {
            try {
                var plan = cPlan >= 0 ? row[cPlan] : "";
                var hero = cHero >= 0 ? row[cHero] : "";
                var dsc = cDesc >= 0 ? row[cDesc] : "";
                var mk = seq.markers.createMarker(t);
                mk.name = "[" + (plan || "?") + "|" + (hero || "?") + "] " + base;
                mk.comments = hero + (dsc ? " — " + dsc : "");
                markers++;
            } catch (eM) { log("маркер не поставился: " + eM); }
        }

        t += (outS - inS);
    }

    var stats = [];
    for (var k in trackStats) { stats.push(k + ": " + trackStats[k]); }

    var msg = "AutoCut: вставлено кусков: " + placed + ", длина: " + t.toFixed(1) + "с\n"
        + "Дорожки: " + (stats.length ? stats.join(", ") : "нет") + "\n"
        + "Маркеров: " + markers
        + (relinked ? "\nРелинк: " + relinked + " офлайн-файлов" : "")
        + (missing.length ? "\nНе найдено в проекте (" + missing.length + "):\n" + missing.slice(0, 20).join("\n") : "");
    log(msg);
    alert(msg);
    app.endUndoGroup();
})();