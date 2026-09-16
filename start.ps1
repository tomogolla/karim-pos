$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:DJANGO_DEBUG = '1'
python manage.py migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python manage.py runserver 127.0.0.1:8765
