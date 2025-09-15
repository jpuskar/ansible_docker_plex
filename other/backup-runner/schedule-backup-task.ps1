# PowerShell script to create Windows Scheduled Task for backup
# Run this script as Administrator to set up the backup schedule

param(
    [string]$BackupScriptPath = "C:\Users\john\Documents\git\ansible_docker_plex\other\backup-runner\run-backups.ps1",
    [string]$SmbPath = "\\your-server\backup",
    [string]$ScheduleTime = "23:00",  # 11:00 PM
    [string]$TaskName = "NightlyBackup"
)

# Check if running as Administrator
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator to create scheduled tasks."
    exit 1
}

Write-Host "Creating scheduled task: $TaskName"
Write-Host "Schedule time: $ScheduleTime daily"
Write-Host "Script path: $BackupScriptPath"
Write-Host "SMB path: $SmbPath"

# Define the action (what to run)
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$BackupScriptPath`" -SmbPath `"$SmbPath`""

# Define the trigger (when to run)
$trigger = New-ScheduledTaskTrigger -Daily -At $ScheduleTime

# Define task settings
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

# Define the principal (run as SYSTEM with highest privileges)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Create the scheduled task
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Nightly backup to SMB share before ZFS snapshots"
    Write-Host "Scheduled task '$TaskName' created successfully!" -ForegroundColor Green
    
    # Show the created task
    Get-ScheduledTask -TaskName $TaskName | Format-List
    
} catch {
    Write-Error "Failed to create scheduled task: $($_.Exception.Message)"
    exit 1
}

Write-Host "`nTo test the backup manually, run:" -ForegroundColor Yellow
Write-Host "Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "`nTo modify the task later:" -ForegroundColor Yellow
Write-Host "Set-ScheduledTask -TaskName '$TaskName' -Trigger (New-ScheduledTaskTrigger -Daily -At '22:30')" -ForegroundColor Cyan