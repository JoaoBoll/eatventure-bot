#
# Instalador simples para scrcpy (Windows) no workspace do projeto.
#
# Uso (PowerShell):
#   .\tools\install_scrcpy.ps1
#   .\tools\install_scrcpy.ps1 -InstallDir C:\scrcpy
#   .\tools\install_scrcpy.ps1 -Version v1.26 -InstallDir tools\scrcpy
#   .\tools\install_scrcpy.ps1 -Force
#
# Opções:
#   -Version    : tag da release (ex: v1.26). Se vazio, usa a última release.
#   -InstallDir : diretório de instalação.
#                 Padrão: pasta 'scrcpy' dentro de 'tools'.
#   -Force      : sobrescreve instalação existente sem pedir confirmação.
#
# Notas:
# - O script baixa o asset ZIP para Windows.
# - O conteúdo é extraído diretamente para InstallDir.
# - Não mantém a pasta interna do ZIP (ex: scrcpy-win64-vX.Y).
# - Requer PowerShell 5+.
#

param(
    [string]$Version = "",
    [string]$InstallDir = "$(Split-Path -Parent $MyInvocation.MyCommand.Definition)\scrcpy",
    [switch]$Force
)

function Fail($msg) {
    Write-Error $msg
    exit 1
}

# ============================================================
# Resolve caminho absoluto do diretório de instalação
# ============================================================

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

if ([System.IO.Path]::IsPathRooted($InstallDir)) {
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
}
else {
    $InstallDir = [System.IO.Path]::GetFullPath(
        (Join-Path (Get-Location).Path $InstallDir)
    )
}

Write-Host ""
Write-Host "========================================"
Write-Host " Instalador scrcpy"
Write-Host "========================================"
Write-Host ""
Write-Host "Diretório de instalação:"
Write-Host "  $InstallDir"
Write-Host ""

# ============================================================
# Escolhe a URL da API do GitHub
# ============================================================

if ([string]::IsNullOrWhiteSpace($Version)) {
    $api = 'https://api.github.com/repos/Genymobile/scrcpy/releases/latest'
}
else {
    # Aceita versão com ou sem prefixo "v"
    $tag = $Version

    if (-not $tag.StartsWith("v")) {
        $tag = "v$tag"
    }

    $api = "https://api.github.com/repos/Genymobile/scrcpy/releases/tags/$tag"
}

Write-Host "Consultando GitHub API:"
Write-Host "  $api"
Write-Host ""

try {
    $release = Invoke-RestMethod `
        -Uri $api `
        -Headers @{ 'User-Agent' = 'scrcpy-installer-script' } `
        -UseBasicParsing
}
catch {
    Fail "Falha consultando GitHub API: $_"
}

if (-not $release) {
    Fail "Release não encontrada."
}

Write-Host "Release encontrada:"
Write-Host "  $($release.tag_name)"
Write-Host ""

# ============================================================
# Procura asset ZIP para Windows
# ============================================================

$asset = $release.assets |
    Where-Object {
        $_.name -match '(win|windows)' -and
        $_.name -match '\.zip$'
    } |
    Select-Object -First 1

if (-not $asset) {
    # Fallback: qualquer ZIP
    $asset = $release.assets |
        Where-Object {
            $_.name -match '\.zip$'
        } |
        Select-Object -First 1
}

if (-not $asset) {
    Fail "Nenhum asset .zip encontrado na release."
}

Write-Host "Asset encontrado:"
Write-Host "  $($asset.name)"
Write-Host ""

# ============================================================
# Prepara arquivo temporário
# ============================================================

$tmp = Join-Path $env:TEMP $asset.name

# Evita conflito caso já exista um arquivo com mesmo nome
if (Test-Path $tmp) {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

Write-Host "Baixando:"
Write-Host "  $($asset.browser_download_url)"
Write-Host ""

try {
    Invoke-WebRequest `
        -Uri $asset.browser_download_url `
        -OutFile $tmp `
        -Headers @{ 'User-Agent' = 'scrcpy-installer-script' } `
        -UseBasicParsing
}
catch {
    Fail "Falha ao baixar asset: $_"
}

Write-Host "Download concluído."
Write-Host ""

# ============================================================
# Prepara diretório de destino
# ============================================================

if (Test-Path $InstallDir) {

    if (-not $Force) {

        $resp = Read-Host `
            "Diretório '$InstallDir' já existe. Sobrescrever? [y/N]"

        if (
            $resp.ToLower() -ne 'y' -and
            $resp.ToLower() -ne 'yes'
        ) {
            Write-Host "Abortado pelo usuário."

            Remove-Item `
                -LiteralPath $tmp `
                -Force `
                -ErrorAction SilentlyContinue

            exit 0
        }
    }

    Write-Host "Removendo instalação anterior..."

    Remove-Item `
        -LiteralPath $InstallDir `
        -Recurse `
        -Force `
        -ErrorAction SilentlyContinue
}

New-Item `
    -ItemType Directory `
    -Path $InstallDir `
    -Force |
    Out-Null

# ============================================================
# Extrai ZIP em diretório temporário
# ============================================================

$extractDir = Join-Path `
    $env:TEMP `
    ("scrcpy-extract-" + [guid]::NewGuid().ToString())

New-Item `
    -ItemType Directory `
    -Path $extractDir `
    -Force |
    Out-Null

Write-Host "Extraindo ZIP..."
Write-Host "  Origem : $tmp"
Write-Host "  Temporário: $extractDir"
Write-Host ""

try {
    Expand-Archive `
        -LiteralPath $tmp `
        -DestinationPath $extractDir `
        -Force
}
catch {
    Remove-Item `
        -LiteralPath $extractDir `
        -Recurse `
        -Force `
        -ErrorAction SilentlyContinue

    Fail "Falha ao extrair ZIP: $_"
}

# ============================================================
# Move o conteúdo para InstallDir
#
# O ZIP normalmente possui:
#
#   scrcpy-win64-vX.Y\
#       scrcpy.exe
#       scrcpy-server.jar
#       adb.exe
#       ...
#
# Queremos:
#
#   tools\scrcpy\
#       scrcpy.exe
#       scrcpy-server.jar
#       adb.exe
#       ...
# ============================================================

Write-Host "Organizando arquivos..."

$topLevelItems = Get-ChildItem `
    -LiteralPath $extractDir `
    -Force

$innerDirectories = $topLevelItems |
    Where-Object { $_.PSIsContainer }

$innerFiles = $topLevelItems |
    Where-Object { -not $_.PSIsContainer }

if (
    $innerDirectories.Count -eq 1 -and
    $innerFiles.Count -eq 0
) {
    # ZIP possui uma única pasta raiz.
    # Move apenas o conteúdo dela.

    $innerDir = $innerDirectories[0]

    Write-Host "Pasta interna encontrada:"
    Write-Host "  $($innerDir.Name)"
    Write-Host ""

    Get-ChildItem `
        -LiteralPath $innerDir.FullName `
        -Force |
        Move-Item `
            -Destination $InstallDir `
            -Force
}
else {
    # Caso o ZIP já tenha os arquivos diretamente na raiz.

    Get-ChildItem `
        -LiteralPath $extractDir `
        -Force |
        Move-Item `
            -Destination $InstallDir `
            -Force
}

# ============================================================
# Limpa arquivos temporários
# ============================================================

Remove-Item `
    -LiteralPath $extractDir `
    -Recurse `
    -Force `
    -ErrorAction SilentlyContinue

Remove-Item `
    -LiteralPath $tmp `
    -Force `
    -ErrorAction SilentlyContinue

# ============================================================
# Verifica instalação
# ============================================================

Write-Host ""
Write-Host "========================================"
Write-Host " Instalação concluída"
Write-Host "========================================"
Write-Host ""

Write-Host "Conteúdo de:"
Write-Host "  $InstallDir"
Write-Host ""

Get-ChildItem `
    -Path $InstallDir |
    ForEach-Object {
        Write-Host "  $($_.Name)"
    }

# ============================================================
# Procura executável
# ============================================================

$exe = Get-ChildItem `
    -Path $InstallDir `
    -Filter 'scrcpy.exe' `
    -Recurse `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

$jar = Get-ChildItem `
    -Path $InstallDir `
    -Filter 'scrcpy-server*.jar' `
    -Recurse `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

Write-Host ""

if ($exe) {
    Write-Host "scrcpy.exe encontrado:"
    Write-Host "  $($exe.FullName)"
}
else {
    Write-Warning `
        "scrcpy.exe não encontrado automaticamente."
}

if ($jar) {
    Write-Host ""
    Write-Host "scrcpy-server encontrado:"
    Write-Host "  $($jar.FullName)"
}
else {
    Write-Warning `
        "scrcpy-server.jar não encontrado automaticamente."
}

# ============================================================
# Adiciona scrcpy ao PATH do usuário
# ============================================================

$scrcpyDir = $InstallDir

$userPath = [Environment]::GetEnvironmentVariable(
    "Path",
    "User"
)

if ([string]::IsNullOrWhiteSpace($userPath)) {
    $userPath = ""
}

$pathEntries = $userPath -split ';' |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$normalizedScrcpyDir = [System.IO.Path]::GetFullPath($scrcpyDir).TrimEnd('\')

$alreadyInPath = $false

foreach ($entry in $pathEntries) {
    try {
        $normalizedEntry = [System.IO.Path]::GetFullPath($entry).TrimEnd('\')

        if ($normalizedEntry -ieq $normalizedScrcpyDir) {
            $alreadyInPath = $true
            break
        }
    }
    catch {
        # Ignora entradas inválidas do PATH
    }
}

if ($alreadyInPath) {
    Write-Host "scrcpy já está no PATH do usuário:"
    Write-Host "  $scrcpyDir"
}
else {
    if ([string]::IsNullOrWhiteSpace($userPath)) {
        $newUserPath = $scrcpyDir
    }
    else {
        $newUserPath = "$userPath;$scrcpyDir"
    }

    [Environment]::SetEnvironmentVariable(
        "Path",
        $newUserPath,
        "User"
    )

    Write-Host "scrcpy adicionado ao PATH do usuário:"
    Write-Host "  $scrcpyDir"
}

# Atualiza o PATH desta sessão do PowerShell também
$env:Path = "$scrcpyDir;$env:Path"

# ============================================================
# Recomendações
# ============================================================

Write-Host ""

if ($exe -and $jar) {

    Write-Host @"
Configuração encontrada:

SCRCPY_PATH = r'$($exe.FullName)'
SCRCPY_SERVER_PATH = r'$($jar.FullName)'

"@
}

Write-Host @"
Para o projeto, prefira construir esses caminhos
relativamente à raiz do projeto, em vez de gravar
um caminho absoluto como C:\Users\Usuario\....

Exemplo em Python:

    from pathlib import Path

    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    SCRCPY_PATH = PROJECT_ROOT / "tools" / "scrcpy" / "scrcpy.exe"
    SCRCPY_SERVER_PATH = PROJECT_ROOT / "tools" / "scrcpy" / "scrcpy-server.jar"

Os binários podem permanecer fora do controle de versão.
"@

Write-Host ""
Write-Host "Fim."