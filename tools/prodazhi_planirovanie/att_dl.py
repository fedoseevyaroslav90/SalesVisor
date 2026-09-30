"""Скачивание вложений комментариев задач «ВАЖНЫЕ НОВОСТИ У2» через REST (только чтение). Вебхук не печатается.
python att_dl.py <фильтр-вид> [лимит]  — виды: plan, fact, segs, other, mat, test"""
import json, os, re, sys, time, urllib.request
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
OUT = r"C:\Users\ia.fedoseev\Desktop\SalesVisor\data\у2"
kind_want = sys.argv[1]; limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 6
cfg = load_yaml(найти_конфиг()); bx = Bitrix(cfg.get("webhook", ""), rps=2)


def kind(n):
    l = n.lower().replace("ё", "е")
    if l.endswith((".png", ".jpg", ".jpeg")): return "img"
    if "отчет по мат" in l: return "mat"
    if "принят" in l: return "fact" if "факт" in l else "plan"
    if "отрезк" in l: return "segs"
    return "other"


todo = []
seen = set()
for tid, year in ((280039, "2025"), (321346, "2026")):
    d = json.load(open(f"crm/task_{tid}.json", encoding="utf-8"))
    for c in d.get("comments") or []:
        for o in (c.get("ATTACHED_OBJECTS") or {}).values():
            n = o.get("NAME") or ""
            k = kind(n)
            if kind_want == "test":
                if "долг" not in n.lower(): continue
            elif kind_want == "mat2026":
                if k != "mat" or year != "2026" or "(Z0+Z4)" not in n: continue
            elif kind_want == "matsample":
                if k != "mat" or "(Z0+Z4)" not in n: continue
                month = c["POST_DATE"][:7]
                slot = month if year == "2026" else f"{year}-Q{(int(month[5:7]) - 1) // 3 + 1}"
                if slot in seen: continue
                seen.add(slot)
            elif k != kind_want: continue
            todo.append((year, c["ID"], c["POST_DATE"][:10], o))
log = open(os.path.join(OUT, "_журнал_скачивания.tsv"), "a", encoding="utf-8")
done = 0
for year, cid, pdate, o in todo[:limit]:
    folder = os.path.join(OUT, year); os.makedirs(folder, exist_ok=True)
    safe = re.sub(r'[<>:"/\|?*]', "_", o["NAME"])
    path = os.path.join(folder, f"{pdate}_{o['ATTACHMENT_ID']}_{safe}")
    if os.path.exists(path) and os.path.getsize(path) == int(o.get("SIZE") or -1):
        continue
    info = bx.call("disk.attachedObject.get", {"id": o["ATTACHMENT_ID"]})
    url = (info or {}).get("DOWNLOAD_URL")
    if not url:
        print("нет DOWNLOAD_URL", o["NAME"]); continue
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "inkab-bx/1.0"}), timeout=600) as r, open(path + ".part", "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    f.write(b)
            os.replace(path + ".part", path); break
        except Exception as e:
            print("повтор", o["NAME"], type(e).__name__); time.sleep(5)
    done += 1
    log.write(f"{year}\t{cid}\t{pdate}\t{o['ATTACHMENT_ID']}\t{o['NAME']}\t{o.get('SIZE')}\t{os.path.getsize(path) if os.path.exists(path) else 0}\n"); log.flush()
print("скачано", done, "из", len(todo[:limit]))
