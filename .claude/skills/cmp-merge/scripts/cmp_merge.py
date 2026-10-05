# -*- coding: utf-8 -*-
"""
cmp_merge.py — универсальный скрипт скилла cmp-merge (автор WNX).

Трёхстороннее сравнение XML-выгрузок конфигурации 1С (база B, предок P, цель T),
оценка коллизий и генерация файла настроек сравнения и объединения (ПНСО).

Подкоманды:
  classify        — классификация объектов по трём каталогам (md5-индексация с кэшем)
  collisions      — коллизии в конфликтных модулях (.bsl), merge3
  xml-collisions  — структурный разбор XML-файлов, изменённых обеими сторонами
  generate        — генерация ПНСО по эталону + профилю политики

Универсальность: пути задаются параметрами; конфигурационно-специфичны только
профили политики (префиксы объектов, отдаваемых вендору целиком / объединяемых
с приоритетом основной). Точечные решения и шапка берутся из эталонного файла
настроек, сохранённого из диалога платформы.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

TO_PLATFORM = {
    "Catalogs": "Catalog", "Documents": "Document", "Enums": "Enum", "Reports": "Report",
    "DataProcessors": "DataProcessor", "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister", "AccountingRegisters": "AccountingRegister",
    "CalculationRegisters": "CalculationRegister", "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicType",
    "ChartsOfCalculationTypes": "ChartOfCalculationType", "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task", "Constants": "Constant", "ExchangePlans": "ExchangePlan",
    "CommonModules": "CommonModule", "CommonPictures": "CommonPicture",
    "CommonForms": "CommonForm", "CommonCommands": "CommonCommand",
    "CommonTemplates": "CommonTemplate", "CommonAttributes": "CommonAttribute",
    "DefinedTypes": "DefinedType", "FilterCriteria": "FilterCriterion",
    "EventSubscriptions": "EventSubscription", "ScheduledJobs": "ScheduledJob",
    "FunctionalOptions": "FunctionalOption", "SettingsStorages": "SettingsStorage",
    "XDTOPackages": "XDTOPackage", "WSReferences": "WSReference",
    "HTTPServices": "HTTPService", "WebServices": "WebService",
    "ExternalDataSources": "ExternalDataSource", "Roles": "Role", "Languages": "Language",
    "Subsystems": "Subsystem", "Sequences": "Sequence", "DocumentJournals": "DocumentJournal",
    "CommandGroups": "CommandGroup", "SessionParameters": "SessionParameter",
    "Styles": "Style",
}
EXCLUDE_FILES = {"ConfigDumpInfo.xml", "dumplist.txt", "VERSION"}
EXCLUDE_PREFIXES = (".git", "readme", "README", "sonar")


def index(root, cache_path):
    """Индекс каталога выгрузки: relpath -> md5 (кэш по размеру)."""
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    files, changed = {}, 0
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            parts = rel.split("/")
            if parts[0] not in TO_PLATFORM and parts[0] not in ("Ext",) and rel != "Configuration.xml":
                continue
            if os.path.basename(rel) in EXCLUDE_FILES or \
                    os.path.basename(rel).startswith(EXCLUDE_PREFIXES):
                continue
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            key = cache.get(rel)
            if key and key[0] == size:
                files[rel] = key[1]
            else:
                h = hashlib.md5()
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                files[rel] = h.hexdigest()
                cache[rel] = (size, files[rel])
                changed += 1
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    return files, changed


def obj_of(relpath):
    parts = relpath.split("/")
    if len(parts) == 2 and parts[1].lower().endswith(".xml"):
        return parts[0] + "." + parts[1][:-4]
    if len(parts) >= 3:
        return parts[0] + "." + parts[1]
    return "Configuration"


def to_platform(obj):
    if "." not in obj:
        return obj
    top, name = obj.split(".", 1)
    return TO_PLATFORM.get(top, top) + "." + name


def diff(a, b):
    da, db = set(a), set(b)
    common = da & db
    return {p for p in common if a[p] != b[p]} | (da - db) | (db - da)


def group_by_obj(paths):
    g = {}
    for p in paths:
        g.setdefault(obj_of(p), set()).add(p)
    return g


def cmd_classify(args):
    t0 = time.time()
    B, _ = index(args.b, os.path.join(args.out_dir, "hash_B.json"))
    P, _ = index(args.p, os.path.join(args.out_dir, "hash_P.json"))
    T, _ = index(args.t, os.path.join(args.out_dir, "hash_T.json"))
    print("Файлов: B=%d P=%d T=%d (%.1f сек)" % (len(B), len(P), len(T), time.time() - t0))

    dbp, dpt = diff(B, P), diff(P, T)
    obj_bp, obj_pt = group_by_obj(dbp), group_by_obj(dpt)
    objs_p = {obj_of(p) for p in P}
    objs_t = {obj_of(p) for p in T}
    objs_b = {obj_of(p) for p in B}

    cls = {k: set() for k in ("vendorOnly", "baseOnly", "conflict", "newInT", "deletedInT")}
    for o in (set(obj_bp) | set(obj_pt)):
        in_bp, in_pt = o in obj_bp, o in obj_pt
        if in_bp and in_pt:
            cls["conflict"].add(o)
        elif in_pt:
            (cls["vendorOnly"] if (o in objs_p or o in objs_b) else cls["newInT"]).add(o)
        else:
            cls["baseOnly"].add(o)
    cls["deletedInT"] = objs_p - objs_t - objs_b
    cls["newInT"] |= objs_t - objs_p - objs_b

    obj_bp = {to_platform(o): p for o, p in obj_bp.items()}
    obj_pt = {to_platform(o): p for o, p in obj_pt.items()}
    for k in cls:
        cls[k] = {to_platform(o) for o in cls[k]}

    print("=== Классификация объектов ===")
    for k in ("vendorOnly", "newInT", "baseOnly", "conflict", "deletedInT"):
        print("%-12s %d" % (k, len(cls[k])))

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "classification.json"), "w", encoding="utf-8") as f:
        json.dump({"counts": {k: len(v) for k, v in cls.items()},
                   "classification": {k: sorted(v) for k, v in cls.items()},
                   "objects_bp": {o: sorted(p) for o, p in obj_bp.items()},
                   "objects_pt": {o: sorted(p) for o, p in obj_pt.items()}},
                  f, ensure_ascii=False, indent=1)
    print("Отчёт: %s" % os.path.join(args.out_dir, "classification.json"))


def read_lines(root, rel):
    full = os.path.join(root, rel)
    if not os.path.exists(full):
        return None
    with open(full, "rb") as f:
        return f.read().splitlines(keepends=True)


def cmd_collisions(args):
    from merge3 import Merge3
    t0 = time.time()
    with open(os.path.join(args.out_dir, "classification.json"), encoding="utf-8") as f:
        cls = json.load(f)
    bp, pt = cls["objects_bp"], cls["objects_pt"]
    conflict = cls["classification"]["conflict"]

    stats = {"modules": 0, "modules_clean": 0, "hunks": 0, "lines_a": 0, "lines_b": 0,
             "lines_same": 0, "lines_conflict": 0}
    xml_both = 0
    vendor_bsl = 0
    per_module, examples = [], []

    for o in sorted(conflict):
        set_bp, set_pt = set(bp.get(o, [])), set(pt.get(o, []))
        both_bsl = sorted(p for p in (set_bp & set_pt) if p.lower().endswith(".bsl"))
        xml_both += len([p for p in (set_bp & set_pt) if p.lower().endswith(".xml")])
        vendor_bsl += len([p for p in (set_pt - set_bp) if p.lower().endswith(".bsl")])
        for rel in both_bsl:
            base, a, b = read_lines(args.p, rel), read_lines(args.b, rel), read_lines(args.t, rel)
            if base is None or a is None or b is None:
                continue
            m3 = Merge3(base, a, b)
            hunks = la = lb = lsame = lconf = 0
            snippets = []
            for tag, *block in m3.merge_regions():
                if tag == "a":
                    la += len(block[0])
                elif tag == "b":
                    lb += len(block[0])
                elif tag == "same":
                    lsame += len(block[0])
                elif tag == "conflict":
                    hunks += 1
                    z, x, y = block
                    lconf += len(z) + len(x) + len(y)
                    if len(examples) < 15:
                        dec = lambda ls: "".join(l.decode("utf-8", "replace") for l in ls)[:400]
                        snippets.append({"predok": dec(z), "nasha": dec(x), "vendornaya": dec(y)})
            stats["modules"] += 1
            stats["hunks"] += hunks
            stats["lines_a"] += la
            stats["lines_b"] += lb
            stats["lines_same"] += lsame
            stats["lines_conflict"] += lconf
            if hunks == 0:
                stats["modules_clean"] += 1
            else:
                per_module.append({"file": rel, "hunks": hunks, "lines_conflict": lconf,
                                   "our_lines": la, "vendor_lines": lb})
                for s in snippets:
                    examples.append({"file": rel, **s})

    per_module.sort(key=lambda x: -x["hunks"])
    print("=== Коллизии в конфликтных модулях (.bsl) ===")
    print("Модулей с изменениями обеих сторон: %d (чисто: %d, разбора: %d)" %
          (stats["modules"], stats["modules_clean"], stats["modules"] - stats["modules_clean"]))
    print("Конфликтных hunk'ов: %d; строк: наши=%d вендор=%d дубли=%d в коллизиях=%d" %
          (stats["hunks"], stats["lines_a"], stats["lines_b"], stats["lines_same"],
           stats["lines_conflict"]))
    print("XML-файлов обеими сторонами: %d; вендорских .bsl автоматически: %d" %
          (xml_both, vendor_bsl))
    for m in per_module[:15]:
        print("  %3d hunks (%4d строк)  %s" % (m["hunks"], m["lines_conflict"], m["file"]))
    with open(os.path.join(args.out_dir, "collisions.json"), "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "per_module": per_module, "examples": examples}, f,
                  ensure_ascii=False, indent=1)
    print("Отчёт: %s (%.1f сек)" % (os.path.join(args.out_dir, "collisions.json"),
                                    time.time() - t0))


def strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def xml_to_map(root, path=""):
    out, counters = {}, {}
    for child in root:
        t = strip_ns(child.tag)
        i = counters.get(t, 0)
        counters[t] = i + 1
        p = "%s/%s[%d]" % (path, t, i)
        attrs = " ".join("%s=%s" % (k, v) for k, v in child.attrib.items())
        text = (child.text or "").strip()
        if not len(child) and not attrs and not text:
            out[p] = ""
        else:
            if attrs or text:
                out[p + "#"] = ("%s|%s" % (attrs, text))[:300]
            out.update(xml_to_map(child, p))
    return out


def read_map(root, rel):
    full = os.path.join(root, rel)
    if not os.path.exists(full):
        return None
    try:
        return xml_to_map(ET.parse(full).getroot())
    except ET.ParseError as e:
        return {"PARSE_ERROR": str(e)}


def cmd_xml_collisions(args):
    t0 = time.time()
    with open(os.path.join(args.out_dir, "classification.json"), encoding="utf-8") as f:
        cls = json.load(f)
    bp, pt = cls["objects_bp"], cls["objects_pt"]
    conflict = cls["classification"]["conflict"]

    targets = []
    for o in sorted(conflict):
        for rel in sorted(set(bp.get(o, [])) & set(pt.get(o, []))):
            if rel.lower().endswith(".xml"):
                parts = rel.split("/")
                is_main = len(parts) >= 2 and parts[-1] == parts[-2] + ".xml"
                kind = ("main" if is_main or rel == "Configuration.xml"
                        else "form" if "/Forms/" in rel else "other")
                targets.append((o, rel, kind))
    print("XML обеими сторонами: %d (main=%d form=%d other=%d)" % (
        len(targets), sum(1 for _, _, k in targets if k == "main"),
        sum(1 for _, _, k in targets if k == "form"),
        sum(1 for _, _, k in targets if k == "other")))

    per_file, prop_tags = [], Counter()
    for o, rel, kind in targets:
        mb, mp, mt = read_map(args.b, rel), read_map(args.p, rel), read_map(args.t, rel)
        if mb is None or mp is None or mt is None:
            continue
        both = only_b = only_t = 0
        for k in set(mb) | set(mp) | set(mt):
            vb, vp, vt = mb.get(k), mp.get(k), mt.get(k)
            if vp == vb and vp == vt:
                continue
            if vp == vb:
                only_t += 1
            elif vp == vt:
                only_b += 1
            else:
                both += 1
                segs = k.replace("#", "").split("/")
                prop_tags[segs[-1] if segs else k] += 1
        if both:
            per_file.append({"obj": o, "file": rel, "kind": kind, "both": both,
                             "only_b": only_b, "only_t": only_t})
    per_file.sort(key=lambda x: -x["both"])
    print("Файлов с коллизиями: %d; всего путей в коллизиях: %d (в т.ч. шум индексов)" %
          (len(per_file), sum(x["both"] for x in per_file)))
    print("Частые конфликтующие теги: %s" % prop_tags.most_common(10))
    for x in per_file[:20]:
        print("  %5d both (%5d our, %5d vendor) [%s] %s" %
              (x["both"], x["only_b"], x["only_t"], x["kind"], x["file"]))
    with open(os.path.join(args.out_dir, "xml_collisions.json"), "w", encoding="utf-8") as f:
        json.dump({"targets": targets, "per_file": per_file}, f, ensure_ascii=False, indent=1)
    print("Отчёт: %s (%.1f сек)" % (os.path.join(args.out_dir, "xml_collisions.json"),
                                    time.time() - t0))


def cmd_generate(args):
    with open(args.classification, encoding="utf-8") as f:
        cls = json.load(f)
    conflict = cls["classification"]["conflict"]

    with open(args.etalon, encoding="utf-8-sig") as f:
        etalon = f.read()

    # шапка эталона — до <Objects> включительно, байт-в-байт
    idx = etalon.index("<Objects>")
    header = etalon[:idx] + "<Objects>"

    # блоки эталона: Object целиком (MergeRule или Property-level) — воспроизводим как есть
    m = re.search(r"<Objects>(.*)</Objects>", etalon, re.S)
    etalon_blocks = re.findall(r"\t*<Object fullName=\"[^\"]+\">.*?</Object>", m.group(1), re.S)
    etalon_names = set()
    for b in etalon_blocks:
        etalon_names.add(re.search(r'fullName="([^"]+)"', b).group(1))

    # массовые правила по профилю политики
    vendor_full = [p for p in (args.vendor_full or "").split(",") if p]
    summarize = [p for p in (args.summarize or "").split(",") if p]
    out_blocks = []
    added = set(etalon_names)

    for o in sorted(conflict):
        if o in added:
            continue
        if any(o.startswith(p) for p in vendor_full):
            out_blocks.append('\t\t<Object fullName="%s">\n\t\t\t<MergeRule>GetFromSecondConfiguration</MergeRule>\n\t\t</Object>' % o)
            added.add(o)
    for o in sorted(conflict):
        if o in added:
            continue
        if any(o.startswith(p) for p in summarize):
            out_blocks.append('\t\t<Object fullName="%s">\n\t\t\t<MergeRule>MergePrioritizingMainConfiguration</MergeRule>\n\t\t</Object>' % o)
            added.add(o)

    body = header + "\n" + "\n".join(out_blocks)
    if etalon_blocks:
        body += "\n" + "\n".join(etalon_blocks)
    body += "\n\t</Objects>\n</Settings>\n"

    out_path = args.out_file or os.path.join(args.out_dir, "merge-settings.xml")
    with open(out_path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(body)

    ET.parse(out_path)  # well-formed
    with open(out_path, encoding="utf-8-sig") as f:
        ours = f.read()
    our_names = re.findall(r'fullName="([^"]+)"', ours)
    missing = [o for o in etalon_names if o not in set(our_names)]
    print("ПНСО: %s" % out_path)
    print("Записей: %d (эталон: %d, массовые: %d)" % (len(our_names), len(etalon_names),
                                                      len(our_names) - len(etalon_names)))
    print("Сверка с эталоном: отсутствует %d" % len(missing))
    for o in missing:
        print("  ОТСУТСТВУЕТ:", o)


def main():
    ap = argparse.ArgumentParser(description="cmp-merge: сравнение конфигураций и генерация ПНСО")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--b", required=True, help="каталог B (база с доработками)")
        p.add_argument("--p", required=True, help="каталог P (чистая версия-предок)")
        p.add_argument("--t", required=True, help="каталог T (целевая версия)")
        p.add_argument("--out-dir", required=True, help="каталог отчётов/кэшей")

    p = sub.add_parser("classify", help="классификация объектов")
    add_common(p)

    p = sub.add_parser("collisions", help="коллизии в модулях (.bsl)")
    add_common(p)

    p = sub.add_parser("xml-collisions", help="структурный разбор XML-коллизий")
    add_common(p)

    p = sub.add_parser("generate", help="генерация ПНСО")
    p.add_argument("--etalon", required=True, help="эталонный файл настроек (из платформы)")
    p.add_argument("--classification", required=True, help="classification.json из classify")
    p.add_argument("--out-file", default=None, help="куда писать ПНСО")
    p.add_argument("--vendor-full", default="", help="префиксы объектов через запятую: отдать вендору целиком")
    p.add_argument("--summarize", default="Role.", help="префиксы через запятую: объединить с приоритетом основной")

    args = ap.parse_args()
    if args.cmd == "classify":
        os.makedirs(args.out_dir, exist_ok=True)
        cmd_classify(args)
    elif args.cmd == "collisions":
        cmd_collisions(args)
    elif args.cmd == "xml-collisions":
        cmd_xml_collisions(args)
    elif args.cmd == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()
