<#
.SYNOPSIS
    Start, stop and check the three databases JourneyMind uses, on Windows,
    without installing anything into the machine.

.DESCRIPTION
    MySQL, Redis and Neo4j are downloaded once as portable archives into
    $Root (C:\jm-databases by default), given their own data directories, and
    run as ordinary child processes on NON-DEFAULT PORTS bound to 127.0.0.1:

        MySQL   3307     (so it cannot collide with a real MySQL on 3306)
        Redis   6380     (      "                      Redis on 6379)
        Neo4j   7688     (      "                      Neo4j on 7687)

    Nothing is registered as a Windows service, nothing is added to PATH, and
    nothing outside $Root is written to. `-Action remove` deletes $Root and the
    machine is back to where it started.

    This script exists so that "start the databases" is a command rather than a
    page of instructions. It is a development convenience, NOT a deployment
    story: a real deployment points the MYSQL_*/REDIS_*/NEO4J_* variables at
    managed instances and never runs this.

.PARAMETER Action
    setup    download and initialise anything missing (safe to re-run)
    start    start whatever is not already listening
    stop     stop the processes this script started
    status   report what is listening, and what the app can reach
    remove   stop everything and delete $Root entirely

.PARAMETER Password
    The password given to all three at setup time. It must match the
    MYSQL_PASSWORD / REDIS_PASSWORD / NEO4J_PASSWORD values in .env. Only read
    at `setup`; afterwards the databases hold their own credentials.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\databases.ps1 -Action setup
    powershell -ExecutionPolicy Bypass -File scripts\databases.ps1 -Action status
#>
[CmdletBinding()]
param(
    [ValidateSet('setup', 'start', 'stop', 'status', 'remove')]
    [string] $Action = 'status',

    [string] $Root = 'C:\jm-databases',

    # Used only by `setup`. Defaults to the value in .env when there is one, so
    # the common case is that nobody has to type a password at all.
    [string] $Password = ''
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Versions are pinned. A setup that silently picks up a new major version of
# MySQL six months from now is not reproducible.
$MySqlVersion = '8.0.46'
$RedisVersion = '8.10.1'
$Neo4jVersion = '5.26.0'
$JdkVersion   = '21.0.12.1+1'

$MySqlPort = 3307
$RedisPort = 6380
$Neo4jBolt = 7688
$Neo4jHttp = 7475

$MySqlHome = Join-Path $Root "mysql\mysql-$MySqlVersion-winx64"
$MySqlData = Join-Path $Root 'mysql-data'
$MySqlIni  = Join-Path $Root 'my.ini'
$RedisHome = Join-Path $Root "redis\Redis-$RedisVersion-Windows-x64-msys2"
$RedisConf = Join-Path $Root 'redis.local.conf'
$RedisData = Join-Path $Root 'redis-data'
$Neo4jHome = Join-Path $Root "neo4j\neo4j-community-$Neo4jVersion"
$JdkHome   = Join-Path $Root "jdk\jdk-$JdkVersion"
$Downloads = Join-Path $Root 'downloads'
$Logs      = Join-Path $Root 'logs'

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
function Write-Step($Message) { Write-Host "  $Message" -ForegroundColor Cyan }
function Write-Ok($Message)   { Write-Host "  $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "  $Message" -ForegroundColor Yellow }

function Test-Port([int] $Port) {
    # Test-NetConnection is slow enough to be noticeable when called six times;
    # a direct TCP connect with a short timeout answers the same question.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $null = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        Start-Sleep -Milliseconds 120
        return $client.Connected -or (Test-Connection -ComputerName 127.0.0.1 -Count 1 -Quiet -ErrorAction SilentlyContinue -and $false)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-Listening([int] $Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

function Wait-ForPort([int] $Port, [string] $Name, [int] $TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Listening $Port) { Write-Ok "$Name is listening on $Port"; return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Warn "$Name did not start listening on $Port within ${TimeoutSeconds}s - see $Logs"
    return $false
}

function Get-DotEnvValue([string] $Key) {
    $envFile = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path $envFile)) { return '' }
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ''
}

function Get-Archive([string] $Url, [string] $Destination) {
    if (Test-Path $Destination) {
        Write-Step "already downloaded: $(Split-Path -Leaf $Destination)"
        return
    }
    Write-Step "downloading $(Split-Path -Leaf $Destination) ..."
    $previous = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'    # the progress bar makes this ~10x slower
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    } finally {
        $ProgressPreference = $previous
    }
}

function Expand-Once([string] $Archive, [string] $Into, [string] $Marker) {
    if (Test-Path $Marker) { Write-Step "already extracted: $(Split-Path -Leaf $Marker)"; return }
    Write-Step "extracting $(Split-Path -Leaf $Archive) ..."
    New-Item -ItemType Directory -Force -Path $Into | Out-Null
    Expand-Archive -Path $Archive -DestinationPath $Into -Force
}

# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
function Invoke-Setup {
    if (-not $Password) {
        $Password = Get-DotEnvValue 'MYSQL_PASSWORD'
    }
    if (-not $Password) {
        throw ("No password given and MYSQL_PASSWORD is not set in .env. " +
               "Copy .env.example to .env, choose a local password, put the " +
               "same one in MYSQL_PASSWORD / REDIS_PASSWORD / NEO4J_PASSWORD, " +
               "then run setup again.")
    }

    New-Item -ItemType Directory -Force -Path $Root, $Downloads, $Logs | Out-Null

    # --- JDK, which Neo4j needs and which we do not want to install machine-wide
    $jdkZip = Join-Path $Downloads 'jdk21.zip'
    Get-Archive "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-$($JdkVersion -replace '\+','%2B')/OpenJDK21U-jdk_x64_windows_hotspot_$($JdkVersion -replace '\+','_').zip" $jdkZip
    Expand-Once $jdkZip (Join-Path $Root 'jdk') $JdkHome

    # --- MySQL
    $mysqlZip = Join-Path $Downloads 'mysql.zip'
    Get-Archive "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-$MySqlVersion-winx64.zip" $mysqlZip
    Expand-Once $mysqlZip (Join-Path $Root 'mysql') $MySqlHome

    if (-not (Test-Path $MySqlIni)) {
        @"
[mysqld]
basedir=$MySqlHome
datadir=$MySqlData
port=$MySqlPort
bind-address=127.0.0.1
default-authentication-plugin=mysql_native_password
max_allowed_packet=64M
innodb_buffer_pool_size=256M
"@ | Set-Content -Path $MySqlIni -Encoding ascii
        Write-Ok "wrote $MySqlIni"
    }

    if (-not (Test-Path $MySqlData)) {
        Write-Step 'initialising the MySQL data directory ...'
        & (Join-Path $MySqlHome 'bin\mysqld.exe') "--defaults-file=$MySqlIni" --initialize-insecure
        Start-MySql
        if (Wait-ForPort $MySqlPort 'MySQL') {
            Write-Step 'creating the journeymind database and user ...'
            # root has no password after --initialize-insecure; the first thing
            # done is to give it one and create the unprivileged app user the
            # application actually connects as.
            $sql = @"
ALTER USER 'root'@'localhost' IDENTIFIED BY '$Password';
CREATE DATABASE IF NOT EXISTS journeymind CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'journeymind'@'127.0.0.1' IDENTIFIED BY '$Password';
GRANT ALL PRIVILEGES ON journeymind.* TO 'journeymind'@'127.0.0.1';
FLUSH PRIVILEGES;
"@
            $sql | & (Join-Path $MySqlHome 'bin\mysql.exe') -h 127.0.0.1 -P $MySqlPort -u root
            Write-Ok 'MySQL user journeymind created'
        }
    }

    # --- Redis
    $redisZip = Join-Path $Downloads 'redis.zip'
    Get-Archive "https://github.com/redis-windows/redis-windows/releases/download/$RedisVersion/Redis-$RedisVersion-Windows-x64-msys2.zip" $redisZip
    Expand-Once $redisZip (Join-Path $Root 'redis') $RedisHome

    New-Item -ItemType Directory -Force -Path $RedisData | Out-Null
    if (-not (Test-Path $RedisConf)) {
        @"
port $RedisPort
bind 127.0.0.1
requirepass $Password
dir $($RedisData -replace '\\','/')
appendonly yes
appendfsync everysec
save 900 1
maxmemory-policy noeviction
"@ | Set-Content -Path $RedisConf -Encoding ascii
        Write-Ok "wrote $RedisConf"
    }

    # --- Neo4j
    $neoZip = Join-Path $Downloads 'neo4j.zip'
    Get-Archive "https://dist.neo4j.org/neo4j-community-$Neo4jVersion-windows.zip" $neoZip
    Expand-Once $neoZip (Join-Path $Root 'neo4j') $Neo4jHome

    $neoConf = Join-Path $Neo4jHome 'conf\neo4j.conf'
    if (Test-Path $neoConf) {
        # Move Neo4j off its default ports for the same reason as the others.
        $conf = Get-Content $neoConf -Raw
        $conf = $conf -replace '(?m)^#?\s*server\.bolt\.listen_address=.*$', "server.bolt.listen_address=127.0.0.1:$Neo4jBolt"
        $conf = $conf -replace '(?m)^#?\s*server\.http\.listen_address=.*$', "server.http.listen_address=127.0.0.1:$Neo4jHttp"
        Set-Content -Path $neoConf -Value $conf -Encoding ascii
        Write-Ok "Neo4j pinned to bolt $Neo4jBolt / http $Neo4jHttp"
    }

    $env:JAVA_HOME = $JdkHome
    $neoData = Join-Path $Neo4jHome 'data\databases\neo4j'
    if (-not (Test-Path $neoData)) {
        Write-Step 'setting the initial Neo4j password ...'
        & (Join-Path $Neo4jHome 'bin\neo4j-admin.bat') dbms set-initial-password $Password
    }

    Write-Ok 'setup complete. Run with -Action start.'
}

# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------
function Start-MySql {
    if (Test-Listening $MySqlPort) { Write-Step "MySQL already listening on $MySqlPort"; return }
    Write-Step 'starting MySQL ...'
    Start-Process -FilePath (Join-Path $MySqlHome 'bin\mysqld.exe') `
        -ArgumentList "--defaults-file=$MySqlIni" `
        -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $Logs 'mysql.err.log')
}

function Start-Redis {
    if (Test-Listening $RedisPort) { Write-Step "Redis already listening on $RedisPort"; return }
    Write-Step 'starting Redis ...'
    Start-Process -FilePath (Join-Path $RedisHome 'redis-server.exe') `
        -ArgumentList "`"$RedisConf`"" `
        -WorkingDirectory $RedisHome `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Logs 'redis.log')
}

function Start-Neo4j {
    if (Test-Listening $Neo4jBolt) { Write-Step "Neo4j already listening on $Neo4jBolt"; return }
    Write-Step 'starting Neo4j ...'
    $env:JAVA_HOME = $JdkHome
    Start-Process -FilePath (Join-Path $Neo4jHome 'bin\neo4j.bat') `
        -ArgumentList 'console' `
        -WorkingDirectory $Neo4jHome `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Logs 'neo4j.log') `
        -RedirectStandardError (Join-Path $Logs 'neo4j.err.log')
}

function Invoke-Start {
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    Start-MySql; Start-Redis; Start-Neo4j
    Wait-ForPort $MySqlPort 'MySQL' | Out-Null
    Wait-ForPort $RedisPort 'Redis' | Out-Null
    Wait-ForPort $Neo4jBolt 'Neo4j' 120 | Out-Null
}

function Invoke-Stop {
    # Only processes running out of $Root are touched, so a MySQL or Redis the
    # developer installed properly is never stopped by this script.
    $stopped = 0
    foreach ($p in Get-Process -Name 'mysqld', 'redis-server', 'java' -ErrorAction SilentlyContinue) {
        try {
            if ($p.Path -and $p.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Step "stopping $($p.ProcessName) (pid $($p.Id))"
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                $stopped++
            }
        } catch { }
    }
    if ($stopped -eq 0) { Write-Step 'nothing of ours was running' }
    else { Write-Ok "stopped $stopped process(es)" }
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
function Invoke-Status {
    Write-Host ''
    Write-Host '  Ports' -ForegroundColor White
    foreach ($row in @(
        @{ Name = 'MySQL'; Port = $MySqlPort },
        @{ Name = 'Redis'; Port = $RedisPort },
        @{ Name = 'Neo4j (bolt)'; Port = $Neo4jBolt })) {
        if (Test-Listening $row.Port) { Write-Ok "$($row.Name.PadRight(13)) listening on $($row.Port)" }
        else { Write-Warn "$($row.Name.PadRight(13)) NOT listening on $($row.Port)" }
    }

    # The ports being open is not the same as the application being able to
    # authenticate, so ask the application's own connection code.
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
    Write-Host ''
    Write-Host '  What the application can actually reach' -ForegroundColor White
    & $python -c @"
import sys; sys.path.insert(0, r'$ProjectRoot\backend')
import logging; logging.disable(logging.WARNING)
from app.db import status
for name, s in status().items():
    mark = 'ok  ' if s['available'] else 'DOWN'
    print(f'  {mark} {name:<7} {s[\"reason\"] or \"\"}')
"@
}

# ---------------------------------------------------------------------------
function Invoke-Remove {
    Invoke-Stop
    if (Test-Path $Root) {
        Write-Warn "deleting $Root (all local database data)"
        Start-Sleep -Seconds 2          # let the processes release their files
        Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
    }
    Write-Ok 'removed'
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "JourneyMind databases - $Action" -ForegroundColor White
Write-Host ('-' * 60)

switch ($Action) {
    'setup'  { Invoke-Setup;  Invoke-Status }
    'start'  { Invoke-Start;  Invoke-Status }
    'stop'   { Invoke-Stop }
    'status' { Invoke-Status }
    'remove' { Invoke-Remove }
}
Write-Host ''
