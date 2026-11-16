# CampusNet traffic counter reader.
# IMPORTANT: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 files
# using the system ANSI code page (GBK on Chinese Windows), so UTF-8 Chinese
# text here would be mis-decoded and break the parser.
param([switch]$PreferSystem)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

$result = [pscustomobject]@{
  ok        = $false
  source    = ''
  timestamp = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
  adapters  = @()
  system    = $null
}

if (-not $PreferSystem) {
  try {
    $list = @()
    $stats = Get-NetAdapterStatistics -ErrorAction Stop
    foreach ($s in $stats) {
      $adapter = $null
      try { $adapter = Get-NetAdapter -InterfaceIndex $s.InterfaceIndex -ErrorAction Stop } catch { }
      $list += [pscustomobject]@{
        name      = if ($adapter) { $adapter.Name } else { "if$($s.InterfaceIndex)" }
        desc      = if ($adapter) { $adapter.InterfaceDescription } else { '' }
        status    = if ($adapter) { [string]$adapter.Status } else { '' }
        rx        = [int64]$s.ReceivedBytes
        tx        = [int64]$s.SentBytes
        rxPackets = [int64]$s.ReceivedUnicastPackets
        txPackets = [int64]$s.SentUnicastPackets
      }
    }
    if ($list.Count -gt 0) {
      $result.ok = $true
      $result.source = 'Get-NetAdapterStatistics'
      $result.adapters = $list
    }
  } catch { }
}

# Fallback: netstat -e gives whole-system totals and needs no elevation.
if (-not $result.ok -or $PreferSystem) {
  try {
    $lines = & netstat.exe -e 2>$null
    $nums = @()
    foreach ($l in $lines) {
      if ($l -match '^\s*Bytes\s+(\d+)\s+(\d+)') { $nums += , @([int64]$Matches[1], [int64]$Matches[2]) }
    }
    if ($nums.Count -gt 0) {
      $rx = 0; $tx = 0
      foreach ($n in $nums) { $rx += $n[0]; $tx += $n[1] }
      $result.ok = $true
      $result.source = 'netstat -e'
      $result.system = [pscustomobject]@{ name = 'system-total'; rx = $rx; tx = $tx }
    }
  } catch { }
}

$result
