param([switch]$Force)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public static class LauncherIconResources {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHDefExtractIconW(string path, int index, uint flags, out IntPtr large, IntPtr small, uint size);
    [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
    public static Bitmap Load(string path, int index) {
        IntPtr handle;
        int result = SHDefExtractIconW(path, index, 0, out handle, IntPtr.Zero, 256);
        if (result != 0 || handle == IntPtr.Zero) return null;
        try { using (Icon icon = Icon.FromHandle(handle)) return icon.ToBitmap(); }
        finally { DestroyIcon(handle); }
    }
}
'@
$configFolder=Join-Path $env:APPDATA 'DesktopDock'
$config=Get-Content -LiteralPath (Join-Path $configFolder 'config.json') -Raw|ConvertFrom-Json
$iconsFolder=Join-Path $configFolder 'icons'
New-Item -ItemType Directory -Path $iconsFolder -Force|Out-Null
$items=@($config.Items)+[pscustomobject]@{Id='files';Name='File Explorer';IconPath=(Join-Path $env:WINDIR 'explorer.exe');Target=(Join-Path $env:WINDIR 'explorer.exe')}
foreach($pin in $items){
    if($pin.Id -notmatch '^[A-Za-z0-9_-]{1,80}$'){throw 'Invalid shortcut cache ID.'}
    $image=$null;$icon=$null
    try{
        $source=if($pin.IconPath){$pin.IconPath}else{$pin.Target}
        $sourceExists=$source -and ($source -match '^[A-Za-z]:\\|^\\\\') -and (Test-Path -LiteralPath $source -PathType Leaf -ErrorAction SilentlyContinue)
        if($sourceExists -and [IO.Path]::GetExtension($source) -match '^\.(png|jpg|jpeg|bmp)$'){$image=[Drawing.Image]::FromFile($source)}
        elseif($sourceExists){$index=if($null -ne $pin.IconIndex){[int]$pin.IconIndex}else{0};$image=[LauncherIconResources]::Load($source,$index);if(-not $image){$icon=[Drawing.Icon]::ExtractAssociatedIcon($source);if($icon){$image=$icon.ToBitmap()}}}
        if(-not $image){
            $cached=Join-Path $iconsFolder ($pin.Id+'-112.png')
            if(Test-Path -LiteralPath $cached){$previous=[Drawing.Image]::FromFile($cached);try{$image=New-Object Drawing.Bitmap($previous)}finally{$previous.Dispose()}}
        }
        if(-not $image){
            $image=New-Object Drawing.Bitmap(64,64);$graphics=[Drawing.Graphics]::FromImage($image);$graphics.Clear([Drawing.Color]::FromArgb(35,105,127));$font=New-Object Drawing.Font('Segoe UI',20,[Drawing.FontStyle]::Bold);$text=($pin.Name -replace '[^a-zA-Z0-9]','');$text=$text.Substring(0,[Math]::Min(2,$text.Length));$graphics.DrawString($text,$font,[Drawing.Brushes]::White,6,14);$graphics.Dispose();$font.Dispose()
        }
        foreach($size in @(32,40,48,50,56,60,64,70,72,80,84,90,96,112)){
            $output=Join-Path $iconsFolder ($pin.Id+'-'+$size+'.png')
            if((-not $Force) -and (Test-Path -LiteralPath $output)){continue}
            $bitmap=New-Object Drawing.Bitmap($size,$size);$graphics=[Drawing.Graphics]::FromImage($bitmap);$graphics.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic;$graphics.SmoothingMode=[Drawing.Drawing2D.SmoothingMode]::HighQuality;$graphics.DrawImage($image,0,0,$size,$size);$bitmap.Save($output,[Drawing.Imaging.ImageFormat]::Png);$graphics.Dispose();$bitmap.Dispose()
        }
    }finally{if($image){$image.Dispose()};if($icon){$icon.Dispose()}}
}
$brandPath=Join-Path $configFolder 'DesktopDock.ico'
if(-not(Test-Path -LiteralPath $brandPath)){
    $bundledIcon=Join-Path $PSScriptRoot 'atria-app.ico'
    if(-not(Test-Path -LiteralPath $bundledIcon)){throw 'The bundled Atria application icon is missing.'}
    Copy-Item -LiteralPath $bundledIcon -Destination $brandPath
}
Write-Output ('Prepared icon cache for '+$items.Count+' shortcuts.')
