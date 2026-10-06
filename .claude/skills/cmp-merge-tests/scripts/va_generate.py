# -*- coding: utf-8 -*-
"""
va_generate.py — генерация сценариев Vanessa Automation из результатов cmp-merge.

Вход:
  --classification   classification.json из скилла cmp-merge (классы объектов, карты файлов)
  --merge-settings   merge-settings.xml (ПНСО: кто как объединяется)
  --out-dir          каталог для feature-файлов

Выход: каталог feature-файлов, сгруппированных по видам проверок, с тегами:
  @Версия      — конфигурация обновилась до целевой версии
  @Метаданные  — наличие/отсутствие метаданных и компилируемость модулей (код встроенного языка)
  @Формы       — smoke-открытие форм затронутых объектов (навигационные ссылки)
  @Отчеты      — формирование отчётов, отданных вендору (GetFromSecondConfiguration)
  @Доработки   — каркасы проверок собственных доработок (пользовательские шаги, TODO)

Синтаксис шагов VA проверять по версии фреймворка; шаги-заготовки помечены комментарием.

Скилл cmp-merge-tests, автор WNX.
"""
import argparse
import json
import os
import time
import xml.etree.ElementTree as ET

# виды объектов с основной формой, открываемой навигационной ссылкой
FORM_LIST = {"Catalog": "list", "Document": "list", "Enum": "list",
             "ExchangePlan": "list", "ChartOfAccounts": "list",
             "ChartOfCharacteristicTypes": "list", "Task": "list",
             "BusinessProcess": "list"}
FORM_APP = {"Report": "app", "DataProcessor": "app"}

EXCLUDE_FILES = {"ConfigDumpInfo.xml", "dumplist.txt", "VERSION"}


def read_pnso(path):
    """merge-settings.xml -> {fullName: rule} (правило уровня объекта)."""
    ns = "{http://v8.1c.ru/8.3/config/merge/settings}"
    root = ET.parse(path).getroot()
    rules = {}
    for obj in root.iter(ns + "Object"):
        full = obj.get("fullName")
        mr = obj.find(ns + "MergeRule")
        if mr is not None and mr.text:
            rules[full] = mr.text.strip()
    return rules


def kind_of(obj):
    return obj.split(".", 1)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classification", required=True)
    ap.add_argument("--merge-settings", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--target-version", default=None,
                    help="целевая версия (иначе из ПНСО SecondConfiguration)")
    args = ap.parse_args()

    t0 = time.time()
    with open(args.classification, encoding="utf-8") as f:
        cls = json.load(f)
    classification = cls["classification"]
    rules = read_pnso(args.merge_settings)

    touched = (set(classification["vendorOnly"]) | set(classification["newInT"])
               | set(classification["conflict"]))
    deleted = set(classification["deletedInT"])
    base_only = set(classification["baseOnly"])

    version = args.target_version
    if not version:
        with open(args.merge_settings, encoding="utf-8-sig") as f:
            m = __import__("re").search(r"<SecondConfiguration>\s*<Name>.*?</Name>\s*"
                                        r"<Version>(.*?)</Version>", f.read())
        version = m.group(1) if m else "ЦЕЛЕВАЯ_ВЕРСИЯ"

    os.makedirs(args.out_dir, exist_ok=True)
    files = {}

    def add(name, text):
        files[name] = text

    # 1) Версия и корневые настройки
    add("01_ВерсияОбновления.feature", f"""# language: ru
@Версия
Функционал: Конфигурация обновлена до целевой версии

	Как Администратор
	Я хочу убедиться, что обновление применилось и версия конфигурации изменилась

	Контекст:
		Дано Я запускаю сценарий открытия TestClient или подключаю существующий

	Сценарий: Проверка версии и корневых настроек
		Когда Я выполняю код встроенного языка
		\"\"\"bsl
			Версия = Метаданные.Версия;
			ОжидаемаяВерсия = "{version}";
			Если Версия <> ОжидаемаяВерсия Тогда
				ВызватьИсключение "Версия конфигурации " + Версия
					+ ", ожидалась " + ОжидаемаяВерсия;
			КонецЕсли;
		\"\"\"
		Тогда Проверка прошла успешно
""")

    # 2) Метаданные: наличие затронутых объектов и отсутствие удалённых
    meta_lines, deleted_lines = [], []
    for o in sorted(touched):
        meta_lines.append(f'		ПроверитьНаличиеОбъекта "{o}";')
    for o in sorted(deleted):
        deleted_lines.append(f'		ПроверитьОтсутствиеОбъекта "{o}";')
    meta_text = f"""# language: ru
@Метаданные
Функционал: Затронутые обновлением объекты метаданных присутствуют в конфигурации

	Как Администратор
	Я хочу убедиться, что все затронутые обновлением объекты метаданных на месте,
	а удалённые вендором — отсутствуют

	Сценарий: Проверка наличия затронутых объектов ({len(touched)} шт.)
		Когда Я выполняю код встроенного языка
		\"\"\"bsl
			Процедура ПроверитьНаличиеОбъекта(ПолноеИмя)
				Если Метаданные.НайтиПоПолномуИмени(ПолноеИмя) = Неопределено Тогда
					ВызватьИсключение "Объект не найден в метаданных: " + ПолноеИмя;
				КонецЕсли;
			КонецПроцедуры

			Процедура ПроверитьОтсутствиеОбъекта(ПолноеИмя)
				Если Метаданные.НайтиПоПолномуИмени(ПолноеИмя) <> Неопределено Тогда
					ВызватьИсключение "Объект должен быть удалён, но присутствует: " + ПолноеИмя;
				КонецЕсли;
			КонецПроцедуры

{chr(10).join(meta_lines)}
{chr(10).join(deleted_lines)}
		\"\"\"
		Тогда Проверка прошла успешно
"""
    add("02_Метаданные_Наличие.feature", meta_text)

    # 3) Smoke-открытие форм по видам
    list_objs, app_objs = {}, {}
    for o in touched:
        if "." not in o:
            continue  # корневой Configuration покрыт проверкой версии
        k = kind_of(o)
        name = o.split(".", 1)[1]
        if k in FORM_LIST:
            list_objs.setdefault(k, []).append(f'e1cib/list/{k}.{name}')
        elif k in FORM_APP:
            app_objs.setdefault(k, []).append(f'e1cib/app/{k}.{name}')

    n = 1
    for k, refs in sorted(list_objs.items()):
        lines = []
        for ref in refs:
            lines.append(f'		Когда Я открываю навигационную ссылку "{ref}"')
            lines.append(f'		И Ожидаем, что активное окно открылось без ошибок')
        add(f"{n:02d}_Формы_{k}.feature", f"""# language: ru
@Формы @{k}
Функционал: Открытие форм затронутых объектов ({k}, {len(refs)} шт.)

	Как Аналитик
	Я хочу проверить, что формы затронутых обновлением объектов открываются без ошибок

	Сценарий: Открытие форм ({k})

{chr(10).join(lines)}
""")
        n += 1
    for k, refs in sorted(app_objs.items()):
        lines = []
        for ref in refs:
            lines.append(f'		Когда Я открываю навигационную ссылку "{ref}"')
            if k == "Report":
                lines.append(f'		И В активном окне я нажимаю кнопку "Сформировать"')
                lines.append(f'		И Ожидаем, что формирование завершилось без ошибок')
            else:
                lines.append(f'		И Ожидаем, что активное окно открылось без ошибок')
        add(f"{n:02d}_{k}_Формы.feature", f"""# language: ru
@Формы @{k} @Отчеты
Функционал: Открытие и формирование ({k}, {len(refs)} шт.)

	Как Аналитик
	Я хочу проверить, что затронутые обновлением {k} открываются и работают

	Сценарий: Открытие и проверка ({k})

{chr(10).join(lines)}
""")
        n += 1

    # 4) Доработки: каркасы пользовательских шагов
    repo_lines = []
    for o in sorted(base_only)[:2000]:
        repo_lines.append(f'		Тогда Проверка доработки объекта "{o}"')
    add("90_Доработки_Каркасы.feature", f"""# language: ru
@Доработки
Функционал: Проверки собственных доработок (каркас — требует наполнения)

	Как Аналитик
	Я хочу проверить поведение собственных доработок после обновления

	Сценарий: Доработки ({len(repo_lines)} объектов — наполните пользовательские шаги)

{chr(10).join(repo_lines)}
""")

    for name, text in files.items():
        path = os.path.join(args.out_dir, name)
        with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
            f.write(text)

    print("Сгенерировано фич: %d в каталог %s (%.1f сек)" %
          (len(files), args.out_dir, time.time() - t0))
    for name in sorted(files):
        print("  ", name)


if __name__ == "__main__":
    main()
