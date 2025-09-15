# Samba Timestamp Diagnostic Script
# This script helps identify why robocopy thinks files are modified when backing up to Samba shares

param(
    [string]$TestDir = "C:\temp\samba-test-source",
    [string]$SmbPath = "\\192.168.1.192\desktop-backups\timestamp-test"
)

function Write-TestLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Write-Host "[$timestamp] $Message"
}

function Get-FileDetails {
    param([string]$Path)

    if (!(Test-Path $Path)) {
        return $null
    }

    $file = Get-Item $Path
    return [PSCustomObject]@{
        Path = $Path
        Size = $file.Length
        CreationTime = $file.CreationTime
        LastWriteTime = $file.LastWriteTime
        LastAccessTime = $file.LastAccessTime
        Attributes = $file.Attributes
        CreationTimeUtc = $file.CreationTimeUtc
        LastWriteTimeUtc = $file.LastWriteTimeUtc
        LastAccessTimeUtc = $file.LastAccessTimeUtc
    }
}

function Compare-FileTimestamps {
    param([object]$SourceFile, [object]$DestFile)

    Write-TestLog "=== TIMESTAMP COMPARISON ==="
    Write-TestLog "Source: $($SourceFile.Path)"
    Write-TestLog "Dest:   $($DestFile.Path)"
    Write-TestLog ""

    # Compare sizes
    $sizeDiff = $DestFile.Size - $SourceFile.Size
    Write-TestLog "Size: Source=$($SourceFile.Size), Dest=$($DestFile.Size), Diff=$sizeDiff"

    # Compare timestamps (local time)
    $createDiff = ($DestFile.CreationTime - $SourceFile.CreationTime).TotalMilliseconds
    $writeDiff = ($DestFile.LastWriteTime - $SourceFile.LastWriteTime).TotalMilliseconds
    $accessDiff = ($DestFile.LastAccessTime - $SourceFile.LastAccessTime).TotalMilliseconds

    Write-TestLog "Creation Time (Local):"
    Write-TestLog "  Source: $($SourceFile.CreationTime)"
    Write-TestLog "  Dest:   $($DestFile.CreationTime)"
    Write-TestLog "  Diff:   ${createDiff}ms"

    Write-TestLog "Write Time (Local):"
    Write-TestLog "  Source: $($SourceFile.LastWriteTime)"
    Write-TestLog "  Dest:   $($DestFile.LastWriteTime)"
    Write-TestLog "  Diff:   ${writeDiff}ms"

    Write-TestLog "Access Time (Local):"
    Write-TestLog "  Source: $($SourceFile.LastAccessTime)"
    Write-TestLog "  Dest:   $($DestFile.LastAccessTime)"
    Write-TestLog "  Diff:   ${accessDiff}ms"

    # Compare timestamps (UTC)
    $createDiffUtc = ($DestFile.CreationTimeUtc - $SourceFile.CreationTimeUtc).TotalMilliseconds
    $writeDiffUtc = ($DestFile.LastWriteTimeUtc - $SourceFile.LastWriteTimeUtc).TotalMilliseconds
    $accessDiffUtc = ($DestFile.LastAccessTimeUtc - $SourceFile.LastAccessTimeUtc).TotalMilliseconds

    Write-TestLog "Creation Time (UTC):"
    Write-TestLog "  Source: $($SourceFile.CreationTimeUtc)"
    Write-TestLog "  Dest:   $($DestFile.CreationTimeUtc)"
    Write-TestLog "  Diff:   ${createDiffUtc}ms"

    Write-TestLog "Write Time (UTC):"
    Write-TestLog "  Source: $($SourceFile.LastWriteTimeUtc)"
    Write-TestLog "  Dest:   $($DestFile.LastWriteTimeUtc)"
    Write-TestLog "  Diff:   ${writeDiffUtc}ms"

    # Compare attributes
    Write-TestLog "Attributes:"
    Write-TestLog "  Source: $($SourceFile.Attributes)"
    Write-TestLog "  Dest:   $($DestFile.Attributes)"

    # Identify potential issues
    Write-TestLog ""
    Write-TestLog "=== POTENTIAL ISSUES ==="

    if ([Math]::Abs($writeDiff) -gt 2000) {
        Write-TestLog "⚠️  Write time difference > 2 seconds (${writeDiff}ms) - Timezone issue?"
    }

    if ([Math]::Abs($writeDiff) -gt 0 -and [Math]::Abs($writeDiff) -le 2000) {
        Write-TestLog "⚠️  Small write time difference (${writeDiff}ms) - Precision issue?"
    }

    if ($sizeDiff -ne 0) {
        Write-TestLog "⚠️  Size difference detected - File corruption?"
    }

    if ($SourceFile.Attributes -ne $DestFile.Attributes) {
        Write-TestLog "⚠️  Attribute mismatch - Samba attribute handling issue?"
    }
}

function Test-RobocopyBehavior {
    param([string]$Source, [string]$Dest)

    Write-TestLog "=== ROBOCOPY TEST ==="

    $tempLog = [System.IO.Path]::GetTempFileName()

    # Run robocopy with verbose output
    $robocopyArgs = @(
        "`"$Source`"",
        "`"$Dest`"",
        "/E",
        "/DCOPY:T",
        "/V",
        "/L",  # List only (don't actually copy)
        "/LOG:`"$tempLog`""
    )

    Write-TestLog "Running: robocopy.exe $robocopyArgs"
    $result = & robocopy.exe $robocopyArgs
    $exitCode = $LASTEXITCODE

    Write-TestLog "Robocopy exit code: $exitCode"

    # Read and display log
    if (Test-Path $tempLog) {
        $logContent = Get-Content $tempLog
        Write-TestLog "Robocopy output:"
        $logContent | ForEach-Object { Write-TestLog "  $_" }
#        Remove-Item $tempLog -Force
    }
}`

# Main test execution
Write-TestLog "Starting Samba timestamp diagnostic..."
Write-TestLog "Test directory: $TestDir"
Write-TestLog "SMB path: $SmbPath"

# Clean up and create fresh test directory
if (Test-Path $TestDir) {
#    Remove-Item $TestDir -Recurse -Force
}
New-Item -ItemType Directory -Path $TestDir -Force | Out-Null

# Create test file in isolated directory
$TestFile = Join-Path $TestDir "test-file.txt"
Write-TestLog "Creating test file: $TestFile"
"Test content created at $(Get-Date)" | Set-Content $TestFile

# Ensure SMB destination exists and is empty
if (Test-Path $SmbPath) {
#    Remove-Item $SmbPath -Recurse -Force
}
New-Item -ItemType Directory -Path $SmbPath -Force | Out-Null

# Get initial source file details
$sourceDetails = Get-FileDetails $TestFile
Write-TestLog "Source file created"

# Copy file to SMB share using robocopy
$fileName = Split-Path $TestFile -Leaf
$destPath = Join-Path $SmbPath $fileName

Write-TestLog "Copying to SMB share..."
$robocopyArgs = @(
    "`"$TestDir`"",
    "`"$SmbPath`"",
    $fileName,
    "/DCOPY:T"
)

& robocopy.exe $robocopyArgs | Out-Null

# Wait a moment for file system to settle
Start-Sleep -Seconds 2

# Get destination file details
if (Test-Path $destPath) {
    $destDetails = Get-FileDetails $destPath
    Compare-FileTimestamps $sourceDetails $destDetails
} else {
    Write-TestLog "❌ Destination file not found: $destPath"
}

# Test what robocopy would do on next run
Write-TestLog ""
Test-RobocopyBehavior $TestDir $SmbPath

Write-TestLog ""
Write-TestLog "=== RECOMMENDATIONS ==="
Write-TestLog "• If write times differ by hours: Check timezone settings on Samba server"
Write-TestLog "• If write times differ by seconds: Try /FFT (assume FAT file times) robocopy option"
Write-TestLog "• If attributes differ: Check Samba 'map archive', 'map hidden', 'map system' settings"
Write-TestLog "• If precision issues: Samba may not preserve NTFS timestamp precision"
