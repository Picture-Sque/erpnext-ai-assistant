# Build frontend and sync bundles into running ERPNext Docker containers
Write-Host "Building frontend bundle..." -ForegroundColor Cyan
Set-Location "$PSScriptRoot\frontend"
npm run build

Write-Host "Syncing bundle to Docker containers..." -ForegroundColor Cyan
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.js" frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/ai_assistant/desk/assistant.bundle.js
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.css" frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/ai_assistant/desk/assistant.bundle.css
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.js" frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/ai_assistant/desk/assistant.js
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.css" frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/ai_assistant/desk/assistant.css

docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.js" frappe_docker-backend-1:/home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/public/desk/assistant.bundle.js
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.css" frappe_docker-backend-1:/home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/public/desk/assistant.bundle.css
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.js" frappe_docker-backend-1:/home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/public/desk/assistant.js
docker cp "$PSScriptRoot\ai_assistant\public\desk\assistant.bundle.css" frappe_docker-backend-1:/home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/public/desk/assistant.css
docker cp "$PSScriptRoot\ai_assistant\hooks.py" frappe_docker-backend-1:/home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/hooks.py

Write-Host "Ensuring bench registration and permissions..." -ForegroundColor Cyan
docker exec -u 0 frappe_docker-frontend-1 chmod -R 755 /home/frappe/frappe-bench/sites/assets/ai_assistant
docker exec -u 0 frappe_docker-backend-1 chmod -R 755 /home/frappe/frappe-bench/apps/ai_assistant/ai_assistant/public

Write-Host "Clearing Frappe site cache..." -ForegroundColor Cyan
docker exec frappe_docker-backend-1 bench --site frontend clear-cache

Write-Host "Done! Hard refresh your browser (Ctrl + F5 or Cmd + Shift + R)." -ForegroundColor Green
