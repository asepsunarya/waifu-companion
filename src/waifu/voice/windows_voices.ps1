# Cyrene's offline fallback voice through the speech voices installed in Windows. Never plays audio.
#   -List   one JSON object per installed voice on stdout: {"name","language","gender","api"}
#   else    speaks the UTF-8 text on stdin with the voice named in $env:WAIFU_TTS_VOICE
#           (its display name, as -List gives it) and writes the WAV bytes to stdout.
# Errors go to stderr as one plain line with exit code 1; a voice that is not installed exits 3.
# Keep this file ASCII: PowerShell 5.1 reads a script without a BOM in the ANSI code page.
# Every expression that is not assigned to $null would land in stdout and corrupt the WAV.
param([switch]$List)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Stdout([byte[]]$bytes) {
    $out = [Console]::OpenStandardOutput()
    $out.Write($bytes, 0, $bytes.Length)
    $out.Flush()
}

function Read-Stdin {
    # Raw bytes: $input and [Console]::In decode with the OEM code page.
    $buffer = New-Object System.IO.MemoryStream
    [Console]::OpenStandardInput().CopyTo($buffer)
    [Text.Encoding]::UTF8.GetString($buffer.ToArray())
}

function Write-Voices($voices) {
    $lines = foreach ($voice in $voices) { $voice | ConvertTo-Json -Compress }
    Write-Stdout ([Text.Encoding]::UTF8.GetBytes((@($lines) -join "`n") + "`n"))
}

function Use-WinRT {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $script:asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
}

function Wait-Operation($operation, [Type]$resultType) {
    $task = $script:asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $null = $task.Wait()
    $task.Result
}

function Invoke-WinRT {
    $voices = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
    if ($List) {
        Write-Voices ($voices | ForEach-Object {
            [ordered]@{ name = $_.DisplayName; language = $_.Language; gender = [string]$_.Gender; api = 'onecore' }
        })
        return 0
    }
    $chosen = $voices | Where-Object { $_.DisplayName -eq $env:WAIFU_TTS_VOICE } | Select-Object -First 1
    if (-not $chosen) { [Console]::Error.WriteLine("voice not installed: $env:WAIFU_TTS_VOICE"); return 3 }
    $text = Read-Stdin
    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    try {
        $synth.Voice = $chosen
        # Plain text, never SSML: her words are read aloud as written.
        $stream = Wait-Operation $synth.SynthesizeTextToStreamAsync($text) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        try {
            $size = [uint32]$stream.Size
            $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
            $null = Wait-Operation $reader.LoadAsync($size) ([uint32])
            $bytes = New-Object byte[] $size
            $reader.ReadBytes($bytes)
            Write-Stdout $bytes
        } finally { $stream.Dispose() }
    } finally { $synth.Dispose() }
    return 0
}

function Invoke-Sapi {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $voices = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo }
        if ($List) {
            Write-Voices ($voices | ForEach-Object {
                [ordered]@{ name = $_.Name; language = $_.Culture.Name; gender = [string]$_.Gender; api = 'sapi' }
            })
            return 0
        }
        if (-not ($voices | Where-Object { $_.Name -eq $env:WAIFU_TTS_VOICE })) {
            [Console]::Error.WriteLine("voice not installed: $env:WAIFU_TTS_VOICE"); return 3
        }
        $text = Read-Stdin
        $synth.SelectVoice($env:WAIFU_TTS_VOICE)
        $wav = New-Object System.IO.MemoryStream
        $synth.SetOutputToWaveStream($wav)
        $synth.Speak($text)
        $synth.SetOutputToNull()
        Write-Stdout $wav.ToArray()
    } finally { $synth.Dispose() }
    return 0
}

try {
    $winrt = $true
    try { Use-WinRT } catch { $winrt = $false }
    if ($winrt) { exit (Invoke-WinRT) } else { exit (Invoke-Sapi) }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
