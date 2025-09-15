# PowerShell Backup Script for Windows to SMB Share
# This script backs up specified directories to a mounted SMB share
# Designed to run before ZFS snapshots

param(
    [string]$SmbPath = "\\192.168.1.192\desktop-backups",
    [string]$LogPath = "C:\Logs\backup.log",
    [int]$RetentionDays = 30
)

# Configuration
$BackupName = "$env:COMPUTERNAME-backup"
$BackupDest = Join-Path $SmbPath $BackupName

# Directories to backup (customize as needed)
$BackupDirs = @(
    $env:USERPROFILE,
    "C:\needs-sorted",
    "C:\games\StepMania 5",
    "C:\games\GOG Games",
    "C:\SDRSharp",
    "C:\Video",
    "D:\ellie-camera",
    "D:\media",
    "D:\VMs\rjt-vpn-workstation"
)

# Robocopy options
$RobocopyOpts = @(
    "/E",        # copy subdirectories, including Empty ones.
    "/DCOPY:T",  # T=Timestamps
    "/ZB",       # use restartable mode; if access denied use Backup mode.
    "/MIR",      # Mirror directory tree (copy all, delete extra files in destination)
    "/R:3",      # Retry 3 times on failed copies
    "/W:10",     # Wait 10 seconds between retries
    "/MT:8",     # Multi-threaded copy using 8 threads for faster performance
    "/V",        # Verbose output - show detailed file operations
    "/TS",       # Include source file timestamps in output
    "/FP",       # Include full pathname in output for easier troubleshooting
    "/NP",       # No progress percentage in log (cleaner log output)
    "/XJ",       # eXclude symbolic links (for both files and directories) and Junction points.
    "/LOG+:`"$LogPath`""  # Append to log file (+ means append, not overwrite)
)

# File exclusions
$FileExcludes = @(
    "*.tmp",
    "*.temp",
    "*.bak",
    "~$*",
    "Thumbs.db",
    ".DS_Store",
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys"
)

# Directory exclusions
$DirExcludes = @(
    '$RECYCLE.BIN',
    '$Recycle.Bin',
    "System Volume Information",
    "C:\Users\john\AppData\Local\Temp",
    "C:\Users\john\AppData\Local\Microsoft\Windows\Temporary Internet Files",
    "C:\Users\john\AppData\Local\Google\Chrome\User Data",
    "C:\Users\john\AppData\Local\Mozilla\Firefox\Profiles",
    "C:\Users\john\AppData\Roaming\Spotify\Storage",
    "C:\Users\john\AppData\Local\Packages",
    "C:\Users\john\AppData\Local\Docker\wsl\disk",
    "C:\Users\john\.kube\cache"
)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] [$Level] $Message"
    
    Write-Host $logMessage
    
    # Ensure log directory exists
    $logDir = Split-Path $LogPath
    if (!(Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    
    Add-Content -Path $LogPath -Value $logMessage
}

function Test-Prerequisites {
    Write-Log "Checking prerequisites."
    
    # Check if SMB path is accessible
    if (!(Test-Path $SmbPath)) {
        Write-Log "SMB share not accessible at $SmbPath" "ERROR"
        exit 1
    }
    
    # Check available space on SMB share (at least 10GB)
    try {
        $drive = Get-WmiObject -Class Win32_LogicalDisk | Where-Object { $_.DeviceID -eq (Split-Path $SmbPath -Qualifier) }
        if ($drive) {
            $availableGB = [math]::Round($drive.FreeSpace / 1GB, 2)
            Write-Log "Available space on backup destination: ${availableGB}GB"
            
            if ($availableGB -lt 10) {
                Write-Log "Less than 10GB available on backup destination" "ERROR"
                exit 1
            }
        }
    } catch {
        Write-Log "Could not check available space: $($_.Exception.Message)" "WARNING"
    }
    
    Write-Log "Prerequisites check completed"
}

function New-BackupStructure {
    Write-Log "Creating backup directory structure..."
    
    if (!(Test-Path $BackupDest)) {
        New-Item -ItemType Directory -Path $BackupDest -Force | Out-Null
        Write-Log "Created backup directory: $BackupDest"
    }
}

function Start-DirectoryBackup {
    param(
        [string]$SourcePath,
        [string]$DestinationPath,
        [string]$DirectoryName
    )
    
    Write-Log "Backing up $SourcePath to $DirectoryName..."
    
    if (!(Test-Path $SourcePath)) {
        Write-Log "Source directory $SourcePath does not exist, skipping..." "WARNING"
        return $false
    }
    
    $destDir = Join-Path $DestinationPath $DirectoryName
    
    # Build robocopy command with exclusions
    $robocopyArgs = @("`"$SourcePath`"", "`"$destDir`"") + $RobocopyOpts
    
    # Add file exclusions - /XF excludes files matching patterns
    foreach ($exclude in $FileExcludes) {
        $robocopyArgs += "/XF"  # Exclude files matching pattern
        $robocopyArgs += "`"$exclude`""
    }

    # Add directory exclusions - /XD excludes directories
    foreach ($exclude in $DirExcludes) {
        $robocopyArgs += "/XD"  # Exclude directories matching pattern
        $robocopyArgs += "`"$exclude`""
    }
    
    # Run robocopy
    $exitCode = $null
    write-host -f yellow "robocopy.exe $robocopyArgs"
    $result = & robocopy.exe $robocopyArgs
    $exitCode = $LASTEXITCODE

    # $exitCode = 0
    # Robocopy exit codes: 0-7 are success, 8+ are errors
    if ($null -eq $exitCode -or $exitCode -gt 7) {
        Write-Log "Failed to backup $DirectoryName (exit code: $exitCode)" "ERROR"
        return $false
    } else {
        Write-Log "Successfully backed up $DirectoryName (exit code: $exitCode)"
        return $true
    }
}

function Invoke-BackupProcess {
    $totalDirs = $BackupDirs.Count
    $currentDir = 0
    $successCount = 0
    
    foreach ($dir in $BackupDirs) {
        $currentDir++
        
        # Determine directory name for backup
        $dirName = Split-Path $dir -Leaf
        if ($dir -eq $env:USERPROFILE) {
            $dirName = "UserProfile-$env:USERNAME"
        }
        
        Write-Log "($currentDir/$totalDirs) Processing directory: $dir"
        
        if (Start-DirectoryBackup -SourcePath $dir -DestinationPath $BackupDest -DirectoryName $dirName) {
            $successCount++
        }
    }
    
    Write-Log "Backup completed: $successCount/$totalDirs directories backed up successfully"
}

function Remove-OldBackups {
    Write-Log "Skipping old backup cleanup - ZFS snapshots will handle retention"
}

function New-BackupSummary {
    $summaryFile = Join-Path $BackupDest "backup-summary.txt"
    
    $summary = @"
Backup Summary
==============
Date: $(Get-Date)
Computer: $env:COMPUTERNAME
User: $env:USERNAME
Backup Location: $BackupDest
PowerShell Version: $($PSVersionTable.PSVersion)

Directories Backed Up:
$(($BackupDirs | ForEach-Object { "  - $_" }) -join "`n")

Backup Size:
$((Get-ChildItem -Path $BackupDest -Recurse | Measure-Object -Property Length -Sum | ForEach-Object { "{0:N2} MB" -f ($_.Sum / 1MB) }))

Available Space After Backup:
$(try { Get-WmiObject -Class Win32_LogicalDisk | Where-Object { $_.DeviceID -eq (Split-Path $SmbPath -Qualifier) } | ForEach-Object { "{0:N2} GB free of {1:N2} GB total" -f ($_.FreeSpace/1GB), ($_.Size/1GB) } } catch { "Unable to determine" })
"@
    
    Set-Content -Path $summaryFile -Value $summary
    Write-Log "Backup summary created at $summaryFile"
}

# Main execution
function Main {
    try {
        Write-Log "Starting backup process..."
        Write-Log "Backup destination: $BackupDest"
        Write-Log "SMB Path: $SmbPath"
        
        Test-Prerequisites
        New-BackupStructure
        Invoke-BackupProcess
        New-BackupSummary
        Remove-OldBackups
        
        Write-Log "Backup process completed successfully"
        Write-Log "Next step: ZFS snapshots can now be taken"
        
    } catch {
        Write-Log "Backup process failed: $($_.Exception.Message)" "ERROR"
        exit 1
    }
}

# Handle Ctrl+C gracefully
$null = Register-EngineEvent PowerShell.Exiting -Action {
    Write-Log "Backup process interrupted by user" "WARNING"
}

# Run main function
Main