/* AutoCut Assemble — сборка рыбы ВНУТРИ Premiere Pro.
 *
 * Что делает: читает timeline CSV (наш selects.csv / timeline.csv),
 * находит файлы в бинах проекта по имени, создаёт секвенцию и кладёт
 * куски по порядку с точными in/out + маркеры с планами.
 * Плюс чинит офлайн-медиа (твой диалог Link Media) по указанной папке.
 *
 * Как запустить:
 *   1. Импортируй исходники в проект (File > Import, папка с флешки).
 *   2. File > Scripts > Run Script File > выбери assemble.jsx.
 *   3. Укажи CSV таймлайна, затем папку с медиа (для релинка, можно пропустить).
 *
 * Нейросети внутрь Premiere не засунуть: detect.py / interview.py по-прежнему
 * готовятся снаружи (Python), сам монтаж — здесь. Проверено по докам API;
 * если твоя версия Premiere ругнётся — кидай текст ошибки, допилю.
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
       children, у клипа — getMediaPath. */
    function isBin(item) {
        try { return item.children && typeof item.children.numItems !== "undefined"; }
        catch (e) { return false; }
    }
    function baseName(p) { return (p + "").replace(/\\/g, "/").split("/").pop().toLowerCase(); }
    function findClip(node, fileName) {
        var i, item, found;
        var want = baseName(fileName);
        for (i = 0; i < node.children.numItems; i++) {
            item = node.children[i];
            try {
                if (isBin(item)) {
                    found = findClip(item, fileName);
                    if (found) { return found; }
                } else {
                    var hit = false, nm = "";
                    try { nm = item.name; } catch (e1) {}
                    if (nm && baseName(nm) === want) { hit = true; }
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
        cDur = col("duration"), cIn = col("in"), cOut = col("out"), cDesc = col("desc");
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

    function num(s, fb) { var v = parseFloat(s); return isNaN(v) ? fb : v; }

    var t = 0, placed = 0, missing = [], musicPlaced = false;
    for (var r = 1; r < rows.length; r++) {
        var row = rows[r];
        if (!row || row.length <= cFile || !row[cFile]) { continue; }
        var fname = row[cFile].replace(/^\s+|\s+$/g, "");
        var base = fname.replace(/^.*[\\/]/, "");
        var isMusic = /\.wav$/i.test(base) || /\.mp3$/i.test(base);

        var item = findClip(app.project.rootItem, base);
        if (!item) { missing.push(base); log("нет в проекте: " + base); continue; }

        var dur = cDur >= 0 ? num(row[cDur], 3) : 3;
        var hasExact = (cIn >= 0 && cOut >= 0 && row[cIn] !== "" && row[cOut] !== "");
        var inS = hasExact ? num(row[cIn], 0) : 0;
        var outS = hasExact ? num(row[cOut], inS + dur) : inS + dur;
        if (!(outS > inS)) { outS = inS + dur; }

        try {
            // source in/out: сначала "все типы" (4), иначе видео+аудио по отдельности
            var inSet = false;
            try { item.setInPoint(inS, 4); item.setOutPoint(outS, 4); inSet = true; }
            catch (e4) {
                try { item.setInPoint(inS, 1); item.setOutPoint(outS, 1); } catch (e5) {}
                try { item.setInPoint(inS, 0); item.setOutPoint(outS, 0); inSet = true; } catch (e6) {}
            }

            if (isMusic) {
                var aTr = seq.audioTracks.numTracks > 1 ? 1 : 0;
                try { seq.insertClip(item, t, -1, aTr); }
                catch (e7) { seq.audioTracks[aTr].insertClip(item, t); }
                musicPlaced = true;
                t += (outS - inS);
            } else {
                try { seq.insertClip(item, t, 0, 0); }
                catch (e8) {
                    seq.videoTracks[0].insertClip(item, t);
                    try { seq.audioTracks[0].insertClip(item, t); } catch (e9) {}
                }
                t += (outS - inS);
            }
            placed++;

            // маркер с планом (не критично — молча пропускаем если API нет)
            try {
                var plan = cPlan >= 0 ? row[cPlan] : "";
                var hero = cHero >= 0 ? row[cHero] : "";
                if (plan || hero) {
                    var mk = seq.markers.createMarker(t - (outS - inS));
                    mk.name = "[" + plan + "] " + base;
                    var dsc = cDesc >= 0 ? row[cDesc] : "";
                    mk.comments = hero + (dsc ? " — " + dsc : "");
                }
            } catch (e10) {}
        } catch (e11) {
            missing.push(base + " (ошибка вставки: " + e11 + ")");
            log("ошибка вставки " + base + ": " + e11);
        }
    }

    var msg = "AutoCut: вставлено кусков: " + placed + ", длина: " + t.toFixed(1) + "с"
        + (musicPlaced ? ", музыка на A2." : ".")
        + (relinked ? " Релинк: " + relinked + "." : "")
        + (missing.length ? "\nНе найдено в проекте (" + missing.length + "):\n" + missing.join("\n") : "");
    log(msg);
    alert(msg);
    app.endUndoGroup();
})();
