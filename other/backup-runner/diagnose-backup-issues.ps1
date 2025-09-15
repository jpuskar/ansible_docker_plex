# Comprehensive Backup Issue Diagnostic Script
# Tests various robocopy scenarios to identify why files appear modified

param(
    [string]$SmbPath = "\\192.168.1.192\desktop-backups\diagnostic-test",
    [string]$TestDir = "C:\temp\backup-test"
)

function Write-DiagLog {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $color = switch ($Level) {
        "ERROR" { "Red" }
        "WARNING" { "Yellow" }
        "SUCCESS" { "Green" }
        default { "White" }
    }
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

function Test-RobocopyScenario {
    param(
        [string]$ScenarioName,
        [string]$SourcePath,
        [string]$DestPath,
        [array]$RobocopyOptions = @("/E", "/DCOPY:T")
    )

    Write-DiagLog "=== TESTING: $ScenarioName ===" "INFO"

    $tempLog = [System.IO.Path]::GetTempFileName()

    # First run - actual copy
    $args1 = @("`"$SourcePath`"", "`"$DestPath`"") + $RobocopyOptions + @("/LOG:`"$tempLog`"")
    Write-DiagLog "First run (copy): robocopy.exe $args1"

    $result1 = & robocopy.exe $args1
    $exit1 = $LASTEXITCODE
    Write-DiagLog "First run exit code: $exit1"

    # Second run - should be no changes
    $args2 = @("`"$SourcePath`"", "`"$DestPath`"") + $RobocopyOptions + @("/LOG+:`"$tempLog`"")
    Write-DiagLog "Second run (check): robocopy.exe $args2"

    $result2 = & robocopy.exe $args2
    $exit2 = $LASTEXITCODE
    Write-DiagLog "Second run exit code: $exit2"

    # Analyze results
    if ($exit2 -eq 0) {
        Write-DiagLog "✅ No changes detected on second run - GOOD!" "SUCCESS"
    } elseif ($exit2 -eq 1) {
        Write-DiagLog "⚠️  Files copied on second run - investigating..." "WARNING"

        # Third run with list-only to see what it wants to copy
        $args3 = @("`"$SourcePath`"", "`"$DestPath`"") + $RobocopyOptions + @("/L", "/V", "/LOG+:`"$tempLog`"")
        Write-DiagLog "Third run (list only): robocopy.exe $args3"
        $result3 = & robocopy.exe $args3

    } else {
        Write-DiagLog "❌ Unexpected exit code: $exit2" "ERROR"
    }

    # Show log content
    if (Test-Path $tempLog) {
        $logLines = Get-Content $tempLog | Select-Object -Last 20
        Write-DiagLog "Log excerpt (last 20 lines):"
        $logLines | ForEach-Object { Write-DiagLog "  $_" }
    }

    Remove-Item $tempLog -Force -ErrorAction SilentlyContinue
    Write-DiagLog ""
}

function Create-TestFiles {
    param([string]$BasePath)

    Write-DiagLog "Creating test files in $BasePath"

    if (!(Test-Path $BasePath)) {
        New-Item -ItemType Directory -Path $BasePath -Force | Out-Null
    }

    # Create various test files
    $testFiles = @{
        "simple.txt" = "Simple text file content"
        "with spaces.txt" = "File with spaces in name"
        "unicode-café.txt" = "File with unicode characters: café résumé naïve"
        "large-file.dat" = ("A" * 1000 + "`n") * 100  # ~100KB file
    }

    $subDir = Join-Path $BasePath "subdirectory"
    if (!(Test-Path $subDir)) {
        New-Item -ItemType Directory -Path $subDir -Force | Out-Null
    }

    foreach ($fileName in $testFiles.Keys) {
        $filePath = Join-Path $BasePath $fileName
        $testFiles[$fileName] | Set-Content $filePath -Encoding UTF8

        # Also create one in subdirectory
        $subFilePath = Join-Path $subDir $fileName
        $testFiles[$fileName] | Set-Content $subFilePath -Encoding UTF8
    }

    Write-DiagLog "Created $($testFiles.Count * 2) test files"
}

function Test-SambaSettings {
    Write-DiagLog "=== SAMBA CONFIGURATION TESTS ===" "INFO"

    $scenarios = @(
        @{
            Name = "Standard Copy"
            Options = @("/E", "/DCOPY:T")
        },
        @{
            Name = "With FFT (FAT File Times)"
            Options = @("/E", "/DCOPY:T", "/FFT")
        },
        @{
            Name = "Mirror Mode"
            Options = @("/MIR", "/DCOPY:T")
        },
        @{
            Name = "Archive Attribute Only"
            Options = @("/E", "/M")
        },
        @{
            Name = "Size and Date"
            Options = @("/E", "/DCOPY:T", "/XA:H")
        }
    )

    foreach ($scenario in $scenarios) {
        $destPath = Join-Path $SmbPath $scenario.Name.Replace(" ", "_")
        Test-RobocopyScenario -ScenarioName $scenario.Name -SourcePath $TestDir -DestPath $destPath -RobocopyOptions $scenario.Options
    }
}

# Main execution
Write-DiagLog "Starting comprehensive backup diagnostics..." "INFO"
Write-DiagLog "Test directory: $TestDir"
Write-DiagLog "SMB destination: $SmbPath"

# Clean up previous tests
if (Test-Path $TestDir) {
    Remove-Item $TestDir -Recurse -Force
}

# Create test files
Create-TestFiles $TestDir

# Test SMB connectivity
if (!(Test-Path (Split-Path $SmbPath))) {
    Write-DiagLog "❌ Cannot access SMB share: $(Split-Path $SmbPath)" "ERROR"
    exit 1
}

# Create SMB test directory
if (!(Test-Path $SmbPath)) {
    New-Item -ItemType Directory -Path $SmbPath -Force | Out-Null
}

# Run all tests
Test-SambaSettings

Write-DiagLog "=== SUMMARY ===" "INFO"
Write-DiagLog "Check the results above to identify the issue:"
Write-DiagLog "• Exit code 0 = No files copied (good)"
Write-DiagLog "• Exit code 1 = Files copied (indicates timestamp/attribute issues)"
Write-DiagLog "• Exit code 2+ = Errors occurred"
Write-DiagLog ""
Write-DiagLog "Common fixes:"
Write-DiagLog "• Add /FFT if FAT file times option helps"
Write-DiagLog "• Check Samba server timezone configuration"
Write-DiagLog "• Review Samba smb.conf settings for dos filetimes, create mask, etc."

# Cleanup
Write-DiagLog "Cleaning up test files..."
Remove-Item $TestDir -Recurse -Force -ErrorAction SilentlyContinue