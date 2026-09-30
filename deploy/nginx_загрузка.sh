#!/bin/bash
# Предел тела для ручной загрузки выгрузок SAP в «Контроль заказов» на шлюзе ai-gw (nginx):
# сниппет /etc/nginx/snippets/inkab-orders-upload.conf и его include рядом с include Тендер-агента
# в /etc/nginx/sites-enabled/inkab-ai. Копия конфигурации — inkab-ai.bak-orders; если nginx -t не прошёл,
# всё возвращается как было. Повторный запуск безопасен (include второй раз не добавляется).
# Запуск из Git Bash:  bash /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/nginx_загрузка.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
grep -q $'\r' "$DIR/inkab-orders-upload.conf" && { echo "ОШИБКА: CRLF в inkab-orders-upload.conf"; exit 1; }
ssh ai-gw 'cat > /tmp/inkab-orders-upload.conf' < "$DIR/inkab-orders-upload.conf"
ssh ai-gw 'sudo bash -s' <<'EOF'
set -e
SITE=/etc/nginx/sites-enabled/inkab-ai
cp -p "$SITE" "$SITE.bak-orders"
install -m 644 /tmp/inkab-orders-upload.conf /etc/nginx/snippets/inkab-orders-upload.conf
rm -f /tmp/inkab-orders-upload.conf
if ! grep -q "inkab-orders-upload.conf" "$SITE"; then
  sed -i '/include snippets\/inkab-tender-upload.conf;/a\
\
    # Контроль заказов SalesVisor: ручная загрузка выгрузки SAP до 160 МБ (30.09.2026)\
    include snippets/inkab-orders-upload.conf;' "$SITE"
fi
if nginx -t 2>&1; then
  systemctl reload nginx
  echo "ГОТОВО: nginx перечитан; include:"; grep -n "inkab-orders-upload" "$SITE"
else
  cp -p "$SITE.bak-orders" "$SITE"; rm -f /etc/nginx/snippets/inkab-orders-upload.conf
  echo "ОШИБКА: nginx -t не прошёл — конфигурация возвращена как была"; exit 1
fi
EOF
