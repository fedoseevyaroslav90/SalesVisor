#!/bin/bash
# Переменные раздела «Контроль заказов» в окружении портала «Инкаб ИИ» (ai-gw, /opt/litellm/.env):
#   SALESVISOR_BASE  — адрес сервиса на ai-ag
#   SALESVISOR_TOKEN — общий секрет; берётся из PORTAL_TOKEN в /opt/salesvisor/.env на ai-ag и передаётся
#                      по ssh-каналу, на экран и в журналы не попадает
# Кому открыт раздел, ведётся не здесь, а в портале: /manage → «Контроль заказов».
# Запуск из Git Bash:  bash /c/Users/ia.fedoseev/Desktop/SalesVisor/deploy/портал_окружение.sh
# После — штатная выкладка портала (93_inkab-ai/выложить_на_сервер.ps1 → deploy.sh full): только она
# переносит переменные в контейнер портала. Повторный запуск заменяет прежние значения.
set -e
AG="ssh -J ai-gw -p 20022 -i $HOME/.ssh/id_ed25519_ai -o IdentitiesOnly=yes yfedoseev@172.16.50.30"

echo "проверяю сервис на ai-ag ..."
$AG 'sudo grep -q "^PORTAL_TOKEN=." /opt/salesvisor/.env && curl -sf -m 5 http://127.0.0.1:8102/api/health >/dev/null' || {
  echo "ОШИБКА: на ai-ag нет /opt/salesvisor/.env с PORTAL_TOKEN или сервис не отвечает — сначала deploy/выкладка.sh"; exit 1; }

echo "переношу переменные в /opt/litellm/.env на ai-gw (значение секрета не показывается) ..."
{
  echo "SALESVISOR_BASE=http://172.16.50.30:8102"
  $AG 'sudo sed -n "s/^PORTAL_TOKEN=/SALESVISOR_TOKEN=/p" /opt/salesvisor/.env'
} | ssh ai-gw 'sudo sh -c "cp /opt/litellm/.env /opt/litellm/.env.bak-salesvisor && sed -i \"/^SALESVISOR_BASE=/d;/^SALESVISOR_TOKEN=/d\" /opt/litellm/.env && cat >> /opt/litellm/.env && chmod 600 /opt/litellm/.env && grep -c \"^SALESVISOR_\" /opt/litellm/.env"'
echo "готово: две строки SALESVISOR_* в /opt/litellm/.env (копия — /opt/litellm/.env.bak-salesvisor)."
echo "Дальше — выкладка портала: powershell -ExecutionPolicy Bypass -File \"C:/Users/ia.fedoseev/Desktop/Внедрение ИИ/90_Код/93_inkab-ai/выложить_на_сервер.ps1\""
