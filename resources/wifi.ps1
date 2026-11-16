# CampusNet network adapter / WiFi reader.
# IMPORTANT: keep this file ASCII-only. Windows PowerShell 5.1 decodes .ps1 files
# with the system ANSI code page (GBK on Chinese Windows), so UTF-8 Chinese text
# here would be mis-decoded and break the parser.
param([string]$Action = 'status')

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

function Get-WifiStatus {
  # Prefer the WiFi profile (works on Chinese and English Windows alike).
  try {
    $profiles = Get-NetConnectionProfile
    $wifi = $profiles | Where-Object { $_.InterfaceType -eq 'Native 802.11' } | Select-Object -First 1
    if (-not $wifi) { $wifi = $profiles | Select-Object -First 1 }
    if ($wifi) {
      return [pscustomobject]@{
        ok       = $true
        ssid     = [string]$wifi.Name
        category = [string]$wifi.NetworkCategory
        ipv4     = [string]$wifi.IPv4Connectivity
        ipv6     = [string]$wifi.IPv6Connectivity
        source   = 'Get-NetConnectionProfile'
      }
    }
  } catch { }

  # Fallback: WinRT WiFi API.
  try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
      $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
    function Await($op, $type) {
      $m = $asTaskGeneric.MakeGenericMethod($type)
      $task = $m.Invoke($null, @($op))
      $task.Wait(3000) | Out-Null
      $task.Result
    }
    [Windows.Devices.WiFi.WiFiAdapter, Windows.Devices.WiFi, ContentType = WindowsRuntime] | Out-Null
    $adapters = Await ([Windows.Devices.WiFi.WiFiAdapter]::FindAllAdaptersAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.WiFi.WiFiAdapter]])
    if ($adapters -and $adapters.Count -gt 0) {
      $conn = $adapters[0].NetworkReport
      if ($conn -and $conn.Length -gt 0) {
        return [pscustomobject]@{
          ok       = $true
          ssid     = [string]$conn[0].ProfileName
          category = 'WinRT'
          ipv4     = ''
          ipv6     = ''
          source   = 'WinRT'
        }
      }
    }
  } catch { }

  return [pscustomobject]@{ ok = $false; ssid = ''; category = ''; ipv4 = ''; ipv6 = ''; source = 'none' }
}

function Get-Adapters {
  $out = @()
  try {
    foreach ($a in Get-NetAdapter) {
      $ip = ''
      try { $ip = [string]($a | Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress } catch { }
      $gw = ''
      try {
        $route = $a | Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($route) { $gw = [string]$route.NextHop }
      } catch { }
      $dns = @()
      try { $dns = @(($a | Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses) } catch { }
      $out += [pscustomobject]@{
        name        = [string]$a.Name
        description = [string]$a.InterfaceDescription
        status      = [string]$a.Status
        linkSpeed   = [string]$a.LinkSpeed
        mac         = [string]$a.MacAddress
        ipv4        = $ip
        gateway     = $gw
        dns         = $dns
      }
    }
  } catch { }
  return $out
}

switch ($Action) {
  'status'   { Get-WifiStatus }
  'adapters' { Get-Adapters }
  'all'      { [pscustomobject]@{ wifi = (Get-WifiStatus); adapters = (Get-Adapters) } }
  default    { Get-WifiStatus }
}
