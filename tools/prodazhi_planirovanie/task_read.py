"""Только чтение задачи Битрикс24 через скрипты/bx.py проекта CRM (вебхук берётся из конфига, не печатается)."""
import json, sys
sys.path.insert(0, r"C:\Users\ia.fedoseev\Desktop\CRM-проект\скрипты")
from bx import Bitrix, load_yaml, найти_конфиг
TID = int(sys.argv[1])
cfg = load_yaml(найти_конфиг())
bx = Bitrix(cfg.get("webhook", ""), rps=2)
t = bx.call("tasks.task.get", {"taskId": TID, "select": ["ID", "TITLE", "DESCRIPTION", "STATUS", "CREATED_DATE", "DEADLINE", "CLOSED_DATE",
                                                        "CREATED_BY", "RESPONSIBLE_ID", "ACCOMPLICES", "AUDITORS", "GROUP_ID", "PARENT_ID",
                                                        "UF_CRM_TASK", "UF_TASK_WEBDAV_FILES", "TAGS", "CHECKLIST"]})
task = (t or {}).get("task", t)
out = {"task": task}
try:
    out["comments"] = bx.call("task.commentitem.getlist", {"TASKID": TID, "ORDER": {"ID": "asc"}})
except Exception as e:
    out["comments_error"] = str(e)[:200]
try:
    out["checklist"] = bx.call("task.checklistitem.getlist", {"TASKID": TID})
except Exception as e:
    out["checklist_error"] = str(e)[:200]
try:
    sub = bx.call("tasks.task.list", {"filter": {"PARENT_ID": TID}, "select": ["ID", "TITLE", "STATUS", "RESPONSIBLE_ID", "DEADLINE", "CREATED_DATE"]})
    out["subtasks"] = (sub or {}).get("tasks", sub)
except Exception as e:
    out["subtasks_error"] = str(e)[:200]
json.dump(out, open(f"crm/task_{TID}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("ok", TID, len(out.get("comments") or []), "комм.", len(out.get("subtasks") or []), "подзадач")
