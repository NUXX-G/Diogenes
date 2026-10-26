<#
.SYNOPSIS
    Comprueba que el equipo está listo para levantar Diogenes (Fase 0).

.DESCRIPTION
    Revisa Docker, Docker Compose, la memoria disponible para WSL2/Docker y la
    conectividad con los servicios externos que necesita el proyecto. Sirve
    para los portátiles y para detectar bloqueos de red en el aula.
    Solo lee: no cambia nada del sistema.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\check-entorno.ps1
#>

$ErrorActionPreference = 'Stop'

# Memoria mínima recomendada para Docker (qwen3.5:4b en Q8_0 + n8n + tools-mcp).
$MemoriaMinimaGB = 8

# Servicios externos que tienen que ser accesibles.
$Destinos = @(
    @{ Nombre = 'Docker Hub';         Url = 'https://registry-1.docker.io/v2/' }
    @{ Nombre = 'Registro de Ollama'; Url = 'https://registry.ollama.ai/v2/' }
    @{ Nombre = 'API de Telegram';    Url = 'https://api.telegram.org/' }
    @{ Nombre = 'Google OAuth';       Url = 'https://oauth2.googleapis.com/' }
)

$script:Fallos = 0
$script:Avisos = 0

function Ok($texto)    { Write-Host "  [OK]    $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "  [AVISO] $texto" -ForegroundColor Yellow; $script:Avisos++ }
function Fallo($texto) { Write-Host "  [FALLO] $texto" -ForegroundColor Red; $script:Fallos++ }

# Ejecuta un comando externo y devuelve su salida, o $null si falla.
function Ejecutar([scriptblock]$comando) {
    try {
        $salida = & $comando 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($salida | Out-String).Trim()
    } catch {
        return $null
    }
}

# --- 1. Docker ---------------------------------------------------------------
Write-Host "`n1. Docker" -ForegroundColor Cyan

$dockerVersion = Ejecutar { docker --version }
if ($dockerVersion) { Ok $dockerVersion } else { Fallo 'No se encuentra el comando docker. Instala Docker Desktop.' }

$servidor = Ejecutar { docker info --format '{{.ServerVersion}}' }
if ($servidor) {
    Ok "Motor de Docker en marcha (servidor $servidor)"
} else {
    Fallo 'El motor de Docker no responde. Abre Docker Desktop y espera a que arranque.'
}

$composeVersion = Ejecutar { docker compose version }
if ($composeVersion) { Ok $composeVersion } else { Fallo 'Docker Compose v2 no disponible (docker compose).' }

# --- 2. Memoria --------------------------------------------------------------
Write-Host "`n2. Memoria" -ForegroundColor Cyan

$ramEquipoGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Ok "RAM del equipo: $ramEquipoGB GB"

# Límite configurado a mano en .wslconfig (si existe).
$wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $wslconfig) {
    $linea = Select-String -Path $wslconfig -Pattern '^\s*memory\s*=\s*(\S+)' | Select-Object -First 1
    if ($linea) {
        Ok "Límite en .wslconfig: memory=$($linea.Matches[0].Groups[1].Value)"
    } else {
        Ok '.wslconfig existe pero no fija memory (WSL2 usa el 50 % de la RAM)'
    }
} else {
    Ok 'Sin .wslconfig: WSL2 usa por defecto el 50 % de la RAM'
}

# Memoria real que ve Docker (la de la máquina virtual de WSL2).
if ($servidor) {
    $memDocker = Ejecutar { docker info --format '{{.MemTotal}}' }
    if ($memDocker) {
        $memDockerGB = [math]::Round([double]$memDocker / 1GB, 1)
        if ($memDockerGB -ge $MemoriaMinimaGB) {
            Ok "Memoria disponible para Docker: $memDockerGB GB"
        } else {
            Aviso "Docker solo tiene $memDockerGB GB (recomendado >= $MemoriaMinimaGB GB). Sube memory= en $wslconfig y ejecuta 'wsl --shutdown'."
        }
    } else {
        Aviso 'No se pudo leer la memoria de Docker.'
    }
} else {
    Aviso 'Memoria de Docker sin comprobar: el motor no está en marcha.'
}

# --- 3. Conectividad ---------------------------------------------------------
Write-Host "`n3. Conectividad" -ForegroundColor Cyan

# Windows PowerShell 5.1 no siempre activa TLS 1.2 por defecto.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

foreach ($destino in $Destinos) {
    try {
        $respuesta = Invoke-WebRequest -Uri $destino.Url -Method Head -UseBasicParsing -TimeoutSec 10
        Ok "$($destino.Nombre) accesible (HTTP $([int]$respuesta.StatusCode))"
    } catch [System.Net.WebException] {
        # Una respuesta HTTP de error (401, 404...) significa que SÍ hay conexión.
        if ($_.Exception.Response) {
            $codigo = [int]$_.Exception.Response.StatusCode
            Ok "$($destino.Nombre) accesible (HTTP $codigo)"
        } else {
            Fallo "$($destino.Nombre) NO accesible ($($destino.Url)): $($_.Exception.Message)"
        }
    } catch {
        Fallo "$($destino.Nombre) NO accesible ($($destino.Url)): $($_.Exception.Message)"
    }
}

# --- Resumen -----------------------------------------------------------------
Write-Host ''
if ($script:Fallos -gt 0) {
    Write-Host "Resultado: $($script:Fallos) fallo(s), $($script:Avisos) aviso(s). Revisa lo marcado en rojo." -ForegroundColor Red
    exit 1
}
if ($script:Avisos -gt 0) {
    Write-Host "Resultado: sin fallos, $($script:Avisos) aviso(s)." -ForegroundColor Yellow
    exit 0
}
Write-Host 'Resultado: todo correcto.' -ForegroundColor Green
exit 0
