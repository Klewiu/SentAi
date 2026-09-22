param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutputFile,
    [Parameter(Mandatory = $true)][ValidateSet('pl', 'en')][string]$Language
)

$ErrorActionPreference = 'Stop'
$text = Get-Content -LiteralPath $TextFile -Raw -Encoding UTF8
$speaker = New-Object -ComObject SAPI.SpVoice
$voicePattern = if ($Language -eq 'pl') { '*Paulina*' } else { '*Zira*' }
$selectedVoice = $speaker.GetVoices() | Where-Object { $_.GetDescription() -like $voicePattern } | Select-Object -First 1
if (-not $selectedVoice) {
    throw "No installed $Language narration voice was found."
}
$speaker.Voice = $selectedVoice
$speaker.Rate = if ($Language -eq 'pl') { -1 } else { 0 }
$speaker.Volume = 100

$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type = 22
$stream.Open($OutputFile, 3, $false)
try {
    $speaker.AudioOutputStream = $stream
    [void]$speaker.Speak($text)
}
finally {
    $stream.Close()
}
