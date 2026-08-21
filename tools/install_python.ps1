<#
Instalador simples para Python no Windows (script PowerShell).

Uso (PowerShell):
  .\tools\install_python.ps1                    # instala versão padrão (configurável abaixo)
  .\tools\install_python.ps1 -Version 3.11.4 -AllUsers -InstallDir 'C:\Program Files\Python311' -AddToPath -Quiet
  .\tools\install_python.ps1 -Url 'https://www.python.org/ftp/python/3.11.4/python-3.11.4-amd64.exe' -Quiet

Opções:
  -Version    : versão do Python (ex: 3.11.4). Usado para construir a URL padrão do instalador.
  -Url        : URL completa do instalador a baixar. Quando fornecido, ignora -Version.
  -InstallDir : diretório de instalação (padrão para AllUsers=InstallAllUsers=1: 'C:\\Program Files\\Python<major><minor>', para per-user usa o diretório do instalador por default)
  -AllUsers   : instala para todos os usuários (requer privilégio administrativo)
  -AddToPath  : adiciona Python ao PATH durante a instalação (usa PrependPath=1)
  -Quiet      : executa sem prompts interativos (modo silencioso), mas o script ainda pedirá confirmação se necessário
  -Force      : sobrescreve diretório de destino sem pedir confirmação

Notas de segurança e operação:
- O script baixa o instalador oficial do python.org; verifique o checksum manualmente se quiser máxima confiança.
- Para instalação AllUsers (em 'C:\\Program Files'), é necessário executar o PowerShell como Administrador.
- Quando instalar em per-user sem privilégios, use o instalador com InstallAllUsers=0 e TargetDir apontando para um diretório sob o perfil do usuário.
- O script tenta escolher o instalador amd64. Se precisar de x86, passe a URL manualmente.
- Não faz commits git; apenas modifica o sistema do host onde rodar.
#>

param(
    [string]$Version = "3.11.4",
    [string]$Url = "",
    [string]$InstallDir = "",
    [switch]$AllUsers,
    [switch]$AddToPath,
    [switch]$Quiet,
    [switch]$Force
)

function Fail($msg) { Write-Error $msg; exit 1 }

# Se o usuário forneceu uma URL explícita, usa-a; caso contrário constrói a URL a partir da versão
if (-not [string]::IsNullOrWhiteSpace($Url)) {
    $downloadUrl = $Url
} else {
    if (-not $Version) { Fail "Versão não informada e --Url não fornecida." }
    # monta a URL padrão para o instalador amd64
    $downloadUrl = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
}

Write-Host "Python installer URL: $downloadUrl"

# determina local temporário para download
$tmpFile = Join-Path $env:TEMP ([IO.Path]::GetFileName($downloadUrl))

try {
    Write-Host "Baixando instalador para: $tmpFile"
    Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -Headers @{ 'User-Agent' = 'python-installer-script' } -UseBasicParsing
} catch {
    Fail "Falha ao baixar o instalador: $_"
}

# Determina opções de instalação
$installArgs = @()

if ($AllUsers) {
    $installArgs += 'InstallAllUsers=1'
    if (-not $InstallDir) {
        # default para Program Files com base na versão
        $verNoDots = $Version -replace '\\.', ''
        $defaultDir = "C:\\Program Files\\Python$($Version -replace '\\..*$','')"
        $InstallDir = $defaultDir
    }
} else {
    $installArgs += 'InstallAllUsers=0'
}

if ($InstallDir) {
    $installArgs += "TargetDir=$InstallDir"
}

if ($AddToPath) {
    $installArgs += 'PrependPath=1'
}

# Silent flags for the official installer
$silentFlags = '/quiet InstallLauncherAllUsers=0'
# Note: InstallLauncherAllUsers controls the 'python launcher' installation for all users; keep default 0

$installerCmd = "$tmpFile $silentFlags $([string]::Join(' ', $installArgs))"

Write-Host "Comando do instalador: $installerCmd"

# Se instalar para todos os usuários e não é admin, avisa
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($AllUsers -and -not $isAdmin) {
    Write-Warning "Você pediu InstallAllUsers, mas não está executando como Administrador. A instalação pode falhar. Reexecute o PowerShell como Administrador ou omita -AllUsers."
}

if (-not $Quiet) {
    $confirm = Read-Host "Pronto para executar o instalador? (s/N)"
    if ($confirm.ToLower() -ne 's' -and $confirm.ToLower() -ne 'y') { Write-Host 'Abortado pelo usuário.'; Remove-Item -LiteralPath $tmpFile -ErrorAction SilentlyContinue; exit 0 }
}

# Executa o instalador e espera
try {
    $proc = Start-Process -FilePath $tmpFile -ArgumentList $silentFlags, $installArgs -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        Write-Warning "Instalador retornou código de saída: $($proc.ExitCode)"
    } else {
        Write-Host "Instalação concluída com código de saída 0."
    }
} catch {
    Fail "Falha ao executar o instalador: $_"
}

# Limpar arquivo temporário
try { Remove-Item -LiteralPath $tmpFile -Force -ErrorAction SilentlyContinue } catch {}

# Relatórios finais
if ($InstallDir) {
    Write-Host "Python deve estar instalado em: $InstallDir"
} else {
    Write-Host "Python foi instalado; se --TargetDir não foi especificado, o instalador escolheu o local padrão do sistema."
}

Write-Host "Se optar por adicionar ao PATH via instalador, use -AddToPath na chamada."
Write-Host "Fim."
