// Входная точка ExtendScript для панели AutoCut.
//
// ScriptPath в манифесте — это точка входа в движок ExtendScript.
// Саму работу мы делаем через __adobe_cep__.evalScript, но без этого
// файла Premiere не поднимает контекст ExtendScript для панели.

// Имя панели видно из ExtendScript: по нему можно отличить нашу
// панель от чужой в отладке.
$.global.__autocutPanel = "AutoCut";
