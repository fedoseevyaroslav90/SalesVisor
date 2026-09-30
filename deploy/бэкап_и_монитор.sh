#!/bin/bash
# SalesVisor в эксплуатации контура «Инкаб ИИ»:
#   ai-ag — блок в ночном /opt/backup-ag.sh: дамп базы (pg_dump) и .env в /backup/local, дальше — на ai-kb
#           вместе с остальным (хранится 7 дней, как всё на узле);
#   ai-gw — проверка «Контроль заказов SalesVisor (ai-ag)» в /opt/monitor/monitor.py (таймер 5 минут).
# Обе правки — с копией файла (.bak-salesvisor) и проверкой синтаксиса; повторный запуск ничего не дублирует.
# Запуск из Git Bash:  bash /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/бэкап_и_монитор.sh
set -e
AG="ssh -J ai-gw -p 20022 -i $HOME/.ssh/id_ed25519_ai -o IdentitiesOnly=yes -o ConnectionAttempts=5 yfedoseev@172.16.50.30"

echo "ai-ag: ночной бэкап ..."
$AG 'sudo python3 -' <<'EOF'
import pathlib, shutil, subprocess
p = pathlib.Path("/opt/backup-ag.sh")
s = p.read_text(encoding="utf-8")
if "salesvisor" in s:
    print("блок SalesVisor уже есть — без изменений")
else:
    anchor = 'find "$D" -maxdepth 1 -type f -mtime +7 -delete'
    assert s.count(anchor) == 1, "не нашёл строку очистки старых копий — правка отменена"
    # скрипт идёт под /bin/sh (dash): без конвейера и pipefail — сжатый формат pg_dump -Fc пишется сразу в файл,
    # код возврата docker exec — это код pg_dump; восстановление — pg_restore --clean
    block = '''# SalesVisor «Контроль заказов» (30.09.2026): дамп базы PostgreSQL (pg_dump -Fc) и .env (600, только root)
if docker ps --format '{{.Names}}' | grep -qx salesvisor-db; then
  if docker exec salesvisor-db pg_dump -U salesvisor -Fc salesvisor > "$D/salesvisor-db-$TS.dump" 2>>"$LOG" \\
     && [ -s "$D/salesvisor-db-$TS.dump" ] && tar czf "$D/ag-salesvisor-$TS.tar.gz" -C / opt/salesvisor/.env 2>>"$LOG"; then
    chmod 600 "$D/salesvisor-db-$TS.dump" "$D/ag-salesvisor-$TS.tar.gz"
    say "salesvisor база ок: $(du -h "$D/salesvisor-db-$TS.dump" | cut -f1)"
  else
    say "salesvisor база ОШИБКА"; fail=1
  fi
fi

'''
    shutil.copy2(p, "/opt/backup-ag.sh.bak-salesvisor")
    new = s.replace(anchor, block + anchor)
    p.write_text(new, encoding="utf-8")
    if subprocess.run(["sh", "-n", str(p)]).returncode != 0:
        shutil.copy2("/opt/backup-ag.sh.bak-salesvisor", p)
        raise SystemExit("ОШИБКА: sh -n не прошёл — /opt/backup-ag.sh возвращён")
    print("блок SalesVisor добавлен (копия — /opt/backup-ag.sh.bak-salesvisor)")
EOF

echo "ai-gw: монитор контура ..."
ssh ai-gw 'sudo python3 -' <<'EOF'
import pathlib, py_compile, shutil
p = pathlib.Path("/opt/monitor/monitor.py")
s = p.read_text(encoding="utf-8")
if "c_salesvisor" in s:
    print("проверка SalesVisor уже есть — без изменений")
else:
    fn_anchor = "def c_searxng():"
    ck_anchor = '    check("Тендер-агент (ai-ag)", c_tender)\n'
    assert s.count(fn_anchor) == 1 and s.count(ck_anchor) == 1, "не нашёл места вставки — правка отменена"
    fn = '''def c_salesvisor():
    # Контроль заказов SalesVisor (репозиторий Desktop/SalesVisor, compose salesvisor на ai-ag): api/health без секретов.
    # Добавлено 30.09.2026.
    s, b = http("http://172.16.50.30:8102/api/health", timeout=10)
    return s == 200 and json.loads(b).get("ok") is True, f"http {s}"


'''
    shutil.copy2(p, "/opt/monitor/monitor.py.bak-salesvisor")
    s = s.replace(fn_anchor, fn + fn_anchor).replace(ck_anchor, ck_anchor + '    check("Контроль заказов SalesVisor (ai-ag)", c_salesvisor)\n')
    p.write_text(s, encoding="utf-8")
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        shutil.copy2("/opt/monitor/monitor.py.bak-salesvisor", p)
        raise SystemExit(f"ОШИБКА: {e} — monitor.py возвращён")
    print("проверка SalesVisor добавлена (копия — /opt/monitor/monitor.py.bak-salesvisor)")
EOF
