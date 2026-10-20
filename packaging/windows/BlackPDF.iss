; Instalador de BlackPDF para Windows (Inno Setup 6).
; Compilar: ISCC.exe /DMyAppVersion=1.0.0 /DSourceDir=..\..\build\dist-win\BlackPDF BlackPDF.iss

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist-win\BlackPDF"
#endif
#ifndef OutputDir
  #define OutputDir "..\out"
#endif

#define MyAppName "BlackPDF"
#define MyAppExe "BlackPDF.exe"
#define MyAppPublisher "The Black Robots Foundation"
#define MyAppURL "https://blackrobots.org/blackpdf"
#define MyRepoURL "https://github.com/TheBlackRobotsFoundation/BlackPDF"
#define MyProgId "BlackPDF.Document"

[Setup]
AppId={{6C3B2E1A-8F4D-4B7E-9A51-2D7F0C9B4E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyRepoURL}/issues
AppUpdatesURL={#MyRepoURL}/releases
AppCopyright=Free software under the AGPL-3.0 license
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=BlackPDF-{#MyAppVersion}-Setup
SetupIconFile=blackpdf.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 usa la ICU de Windows (icuuc.dll), disponible desde Windows 10 1903.
MinVersion=10.0.18362
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCompany={#MyAppPublisher}

[Languages]
; El instalador elige el idioma de Windows; la aplicación hace lo mismo al arrancar.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
english.AssocTask=Show BlackPDF in "Open with" for PDF files
spanish.AssocTask=Mostrar BlackPDF en «Abrir con» para los archivos PDF
brazilianportuguese.AssocTask=Mostrar o BlackPDF em "Abrir com" para arquivos PDF
portuguese.AssocTask=Mostrar o BlackPDF em "Abrir com" para ficheiros PDF
french.AssocTask=Afficher BlackPDF dans « Ouvrir avec » pour les fichiers PDF
english.DefaultTask=Open settings to choose BlackPDF as the default PDF reader
spanish.DefaultTask=Abrir la configuración para elegir BlackPDF como lector de PDF predeterminado
brazilianportuguese.DefaultTask=Abrir as configurações para escolher o BlackPDF como leitor de PDF padrão
portuguese.DefaultTask=Abrir as definições para escolher o BlackPDF como leitor de PDF predefinido
french.DefaultTask=Ouvrir les paramètres pour choisir BlackPDF comme lecteur PDF par défaut
english.DocName=PDF document
spanish.DocName=Documento PDF
brazilianportuguese.DocName=Documento PDF
portuguese.DocName=Documento PDF
french.DocName=Document PDF
english.AppDescription=Free offline PDF viewer and editor
spanish.AppDescription=Visor y editor de PDF libre y sin conexión
brazilianportuguese.AppDescription=Visualizador e editor de PDF livre e sem conexão
portuguese.AppDescription=Visualizador e editor de PDF livre e sem ligação à Internet
french.AppDescription=Visionneuse et éditeur PDF libre et hors ligne
english.LaunchApp=Launch BlackPDF
spanish.LaunchApp=Abrir BlackPDF
brazilianportuguese.LaunchApp=Abrir o BlackPDF
portuguese.LaunchApp=Abrir o BlackPDF
french.LaunchApp=Lancer BlackPDF

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "associate"; Description: "{cm:AssocTask}"; GroupDescription: "PDF:"
Name: "makedefault"; Description: "{cm:DefaultTask}"; GroupDescription: "PDF:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; AppUserModelID: "BlackPDF.BlackPDF"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon; AppUserModelID: "BlackPDF.BlackPDF"

[Registry]
; Tipo de documento propio, con icono y verbo «Abrir».
Root: HKA; Subkey: "Software\Classes\{#MyProgId}"; ValueType: string; ValueName: ""; ValueData: "{cm:DocName}"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"",0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Tasks: associate
; Aparecer en «Abrir con» de los PDF.
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#MyProgId}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
; Registro de la aplicación (nombre visible y tipos admitidos).
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""
; Capacidades para «Aplicaciones predeterminadas» de Windows.
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "{cm:AppDescription}"
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\{#MyAppExe}"",0"
Root: HKA; Subkey: "Software\{#MyAppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#MyProgId}"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: "Software\{#MyAppName}\Capabilities"; Flags: uninsdeletevalue
; Ruta de la aplicación para poder lanzarla con Win+R «blackpdf».
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExe}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe}"; Flags: uninsdeletekey

[Run]
Filename: "ms-settings:defaultapps"; Flags: shellexec nowait postinstall skipifsilent; Tasks: makedefault; Description: "{cm:DefaultTask}"
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
