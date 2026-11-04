$ErrorActionPreference='Stop'
$taskWorkspace=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskProject=Join-Path $taskWorkspace 'qingjian-mini'
$taskRun=Join-Path $taskWorkspace 'work/mini_phone_sync_20261104'
New-Item -ItemType Directory -Path $taskRun -Force | Out-Null
$taskIPv4=Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -eq 'WLAN' -and $_.IPAddress -notlike '169.254.*'} | Select-Object -First 1 -ExpandProperty IPAddress
if(!$taskIPv4){throw '未找到Wi-Fi IPv4。请先让电脑和手机连接同一网络。'}
$taskNode=(Get-Command node).Source
$taskLocal=$false
try{$taskLocal=(Invoke-WebRequest 'http://127.0.0.1:8796/' -TimeoutSec 2).StatusCode -eq 200}catch{}
if(!$taskLocal){Start-Process -FilePath $taskNode -ArgumentList @((Join-Path $PSScriptRoot 'manual-server.cjs'),(Join-Path $taskWorkspace 'work/mini_manual_console_20261103/runtime')) -WorkingDirectory $taskWorkspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRun 'server.stdout.log') -RedirectStandardError (Join-Path $taskRun 'server.stderr.log')}
$taskBase='http://'+$taskIPv4+':8797'
$taskRelay=$false
try{$taskRelay=(Invoke-WebRequest ($taskBase+'/health') -TimeoutSec 2).StatusCode -eq 200}catch{}
if(!$taskRelay){Start-Process -FilePath $taskNode -ArgumentList @((Join-Path $PSScriptRoot 'phone-relay.cjs'),$taskIPv4) -WorkingDirectory $taskWorkspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRun 'relay.stdout.log') -RedirectStandardError (Join-Path $taskRun 'relay.stderr.log')}
for($taskI=0;$taskI -lt 20;$taskI++){if(Test-Path (Join-Path $taskWorkspace 'work/mini_manual_console_20261103/runtime/token.txt')){break};Start-Sleep -Milliseconds 200}
$taskToken=(Get-Content -LiteralPath (Join-Path $taskWorkspace 'work/mini_manual_console_20261103/runtime/token.txt') -Raw).Trim()
$taskConfig=@{mode='test';baseURL=$taskBase;socketURL='';refreshMs=500;testToken=$taskToken}
Set-Content -LiteralPath (Join-Path $taskProject 'config.js') -Value ('// 手机局域网计数联调，未接RK。勿上传发布。'+[Environment]::NewLine+'module.exports='+($taskConfig|ConvertTo-Json)+';') -Encoding utf8
$taskConnection=@{baseURL=$taskBase;token=$taskToken;build='M3-1104'}
Set-Content -LiteralPath (Join-Path $taskProject 'services/test-connection.js') -Value ('module.exports='+($taskConnection|ConvertTo-Json)+';') -Encoding utf8
Write-Output ('手机网络检查：'+$taskBase+'/health')
Write-Output '电脑控制台：http://127.0.0.1:8796/'
Write-Output '如电脑Wi-Fi地址变化，须重新预览扫码更新手机代码。'
