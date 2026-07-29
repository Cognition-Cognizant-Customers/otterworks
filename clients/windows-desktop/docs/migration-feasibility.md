# OtterWorks.Desktop — Migration Feasibility Assessment

**Scope:** `clients/windows-desktop` only.
**Assessment date:** 2026-07-29
**Verdict:** Migration to WPF on modern .NET (.NET 8) is **feasible and low-risk**. There are no hard blockers. The work is dominated by build-system conversion, not code rewriting.

---

## 1. Inventory

### 1.1 Project and solution

| Property | Value |
| --- | --- |
| Solution | `OtterWorks.Desktop.sln` (VS format 12.00, VS 17 metadata), single project |
| Project file | `OtterWorks.Desktop/OtterWorks.Desktop.csproj` — **classic non-SDK-style**, `ToolsVersion="15.0"` |
| Imports | `Microsoft.Common.props`, `Microsoft.CSharp.targets` only — no custom targets |
| `OutputType` | `WinExe` |
| `TargetFrameworkVersion` | `v4.8` |
| `LangVersion` | `7.3` |
| `AutoGenerateBindingRedirects` | `true` |
| Configurations | `Debug|Any CPU`, `Release|Any CPU` |
| Custom build steps | **None** — no `<Target>`, `AfterBuild`, or `PostBuildEvent` |
| ClickOnce / publish / deployment | **None** |
| `app.manifest` | **None** (no source manifest) |
| `.resx` resources | **None** |
| Source enumeration | Every `.cs` and `.xaml` is listed explicitly via `<Compile>` / `<Page>` / `<ApplicationDefinition>` items |

### 1.2 NuGet dependencies

Uses **`packages.config`**, not `PackageReference`.

| Package | Version | TFM in packages.config | Consumed via |
| --- | --- | --- | --- |
| `Newtonsoft.Json` | `13.0.4` | `net48` | `<Reference>` + `HintPath` to `..\packages\Newtonsoft.Json.13.0.4\lib\net45\Newtonsoft.Json.dll` |

That is the **only** NuGet package. Everything else is a framework `<Reference>`: `System`, `System.Configuration`, `System.Core`, `System.Net.Http`, `System.Security`, `System.Xml`, `System.Xml.Linq`, `System.Data`, `System.Data.DataSetExtensions`, `System.Net`, `Microsoft.CSharp`, plus the WPF set (`WindowsBase`, `PresentationCore`, `PresentationFramework`, `System.Xaml`).

### 1.3 Configuration and content

- `app.config` — only a `<supportedRuntime>` element for v4.0/net48 and one `AppContextSwitchOverrides` entry (`Switch.System.Net.DontEnableSystemDefaultTlsVersions=false`). **No custom configuration sections, no `ConfigurationManager` usage anywhere in source.**
- `appsettings.json` — app configuration (`apiBaseUrl`, `persistTokens`), copied to output with `PreserveNewest`; read manually via `File.ReadAllText` + Newtonsoft.Json in `Services/AppSettings.cs`.

### 1.4 WPF features in use

| Feature | Where |
| --- | --- |
| `ApplicationDefinition` / `App.xaml` resource dictionary | `App.xaml:7-56` |
| Built-in + custom `IValueConverter`s (`BooleanToVisibility`, `StringToVisibility`, `InverseBoolean`) | `App.xaml:9-11`, `Mvvm/StringToVisibilityConverter.cs` |
| Implicit view-model → view `DataTemplate` navigation | `App.xaml:14-22`, `ViewModels/MainViewModel.cs:31-65` |
| Shared brushes + `Button` style with custom `ControlTemplate`, `TemplateBinding`, mouse-over/disabled `Trigger`s | `App.xaml:24-54` |
| `Window` + three `UserControl` views, `ContentControl` shell host | `Views/*.xaml` |
| Data binding, `ItemsSource`, inline item `DataTemplate`, `ListBoxItem` style | `Views/DocumentsView.xaml:48-75` |
| `INotifyPropertyChanged` base, `ObservableCollection<T>` | `Mvvm/ObservableObject.cs`, `ViewModels/DocumentsViewModel.cs:35-37` |
| `ICommand` (sync + async) using `CommandManager.RequerySuggested` / `InvalidateRequerySuggested` | `Mvvm/RelayCommand.cs:21-22,62-70` |
| `PasswordBox` + `PasswordChanged` code-behind (password is not a bindable DP) | `Views/LoginView.xaml.cs:13-18`, `Views/RegisterView.xaml.cs:13-18` |
| `ThemeInfo` attribute | `Properties/AssemblyInfo.cs:15` |

No custom `Control`/`FrameworkElement` subclasses. **No `WindowsFormsHost`, no WinForms interop, no `DesignerProperties`.**

### 1.5 Windows-only / .NET Framework-only API surface

**Present:**

1. **WPF itself** — Windows-only on modern .NET too, but fully supported there (`net8.0-windows` + `UseWPF`). Not a blocker, only a platform constraint.
2. **`AppDomain.CurrentDomain.BaseDirectory`** — `Services/AppSettings.cs:26`. Supported on modern .NET; `AppContext.BaseDirectory` is the idiomatic replacement.
3. **Windows DPAPI** — `System.Security.Cryptography.ProtectedData.Protect/Unprotect` with `DataProtectionScope.CurrentUser`, `Services/SessionState.cs:78,109`. Available on modern .NET **only via the `System.Security.Cryptography.ProtectedData` NuGet package**; Windows-only. This is the one package addition the migration requires.
4. **`[ComVisible(false)]`** — `Properties/AssemblyInfo.cs:14`. Metadata only; no COM interfaces, no interop assemblies, no COM activation.
5. **`System.Configuration` framework reference** — `.csproj:42`. Referenced but **unused**; can simply be dropped.

**Confirmed absent** (searched): WCF / `System.ServiceModel`, `System.Web`, .NET Remoting, real COM interop, `System.Drawing`, Registry APIs, `DllImport`/P/Invoke, `BinaryFormatter`, `HttpWebRequest`, `System.Runtime.Serialization` APIs, `CryptoConfig`, `machineKey`, ClickOnce.

### 1.6 CI coverage

**None.** No workflow under `.github/workflows` references `clients/windows-desktop`, `OtterWorks.Desktop.sln`, or `OtterWorks.Desktop.csproj`. The only C# job in `ci.yml` targets `services/audit-service`. This project is currently built by humans only — a gap worth closing regardless of migration (see Phase 0).

---

## 2. Build verification (baseline)

Verified on this machine (Windows Server 2022):

| Tool | Status |
| --- | --- |
| Visual Studio Build Tools 2022 (17.14.37516.0) | present, `isComplete: true` |
| MSBuild 17.14.51 | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe` |
| .NET Framework 4.8 reference assemblies | present |
| `nuget.exe` 7.6.0.59 | present |
| .NET SDKs 8.0.423 / 9.0.316 | present |

```powershell
nuget restore OtterWorks.Desktop.sln
MSBuild.exe OtterWorks.Desktop.sln /t:Build /p:Configuration=Release /p:Platform="Any CPU"
# => Build succeeded. 0 Warning(s) 0 Error(s)
```

`dotnet build` on the current project **fails by design** (CS5001 no entry point, CS0103 `InitializeComponent`) because the .NET CLI does not run the classic WPF markup compiler on a non-SDK project. This is expected and is itself one of the strongest arguments for the SDK-style conversion in Phase 2.

---

## 3. Feasibility assessment: WPF on .NET 8

### 3.1 What converts mechanically

- **Project file** → a ~15-line SDK-style `.csproj` with `<TargetFramework>net8.0-windows</TargetFramework>`, `<UseWPF>true</UseWPF>`, `<OutputType>WinExe</OutputType>`. All 15 explicit `<Compile>` items and all `<Page>`/`<ApplicationDefinition>` items are handled automatically by the WPF SDK's default globs and can be deleted.
- **All framework `<Reference>` elements** are deleted — the SDK reference set supersedes them.
- **All XAML** — templates, styles, triggers, converters, implicit `DataTemplate` navigation, `PasswordBox` code-behind — is unchanged. WPF's XAML dialect and control set are the same on .NET 8.
- **All MVVM code** — `INotifyPropertyChanged`, `ObservableCollection<T>`, `ICommand`, `CommandManager` — is unchanged and fully supported.
- **`HttpClient` API client** — unchanged.
- **`AssemblyInfo.cs`** — replaced by SDK-generated assembly attributes (`<AssemblyTitle>` etc.); keep only `ThemeInfo` in a trimmed file, or set `<GenerateAssemblyInfo>false</GenerateAssemblyInfo>` to keep the existing file verbatim (the lower-risk option for Phase 2).
- **`app.config`** — deleted. `<supportedRuntime>` is meaningless on modern .NET, and the TLS `AppContextSwitchOverrides` switch is unnecessary because modern .NET already defaults to system TLS versions. `AutoGenerateBindingRedirects` also becomes obsolete.
- **`appsettings.json` copy-to-output** — replaced by `<Content Include="appsettings.json" CopyToOutputDirectory="PreserveNewest" />`, or left as-is.

### 3.2 What needs an explicit change (small, well-understood)

| Item | Change |
| --- | --- |
| `ProtectedData` (DPAPI), `SessionState.cs:78,109` | Add `<PackageReference Include="System.Security.Cryptography.ProtectedData" />`. API is identical; still Windows-only, which is acceptable for a WPF app. Alternatively re-implement token storage with AES + a machine/user-scoped key if cross-platform ever matters. |
| `System.Configuration` reference | Delete — unused. |
| `AppDomain.CurrentDomain.BaseDirectory` | Works as-is; optionally modernize to `AppContext.BaseDirectory`. |
| `Newtonsoft.Json` 13.0.4 | Fully supports `net8.0`; **no replacement needed**. Optional follow-up: migrate to `System.Text.Json`, but the models use explicit `[JsonProperty]` names mixing camelCase and snake_case (`Models/*.cs`), so this is a deliberate, separately-tested change — *not* part of the framework migration. |
| `LangVersion 7.3` | Delete the property. `net8.0` defaults to C# 12, which is strictly a superset; no source changes required. |

### 3.3 Blockers

**There are no hard blockers.** Specifically, none of the usual .NET Framework migration killers are present: no WCF, no `System.Web`, no Remoting, no ClickOnce, no COM interop, no `BinaryFormatter`, no P/Invoke, no custom MSBuild targets, no third-party UI control suites, no `.resx`/designer-generated code, and only one NuGet package.

Residual risks, all low:

- **Runtime distribution.** .NET Framework 4.8 is present on every supported Windows; .NET 8 is not. Requires either a self-contained/single-file publish or a runtime prerequisite in the installer. This is a *deployment* decision, not a code one.
- **Behavioural deltas.** Minor WPF rendering/font-fallback differences and stricter default `HttpClient`/TLS behaviour. Mitigated by running the existing manual verification flow (register → login → create document → logout → persistence, per `README.md:129-155`).
- **Tooling handoff.** Once SDK-style, the build moves from full MSBuild to `dotnet build`, which changes local developer instructions and unblocks CI on `windows-latest`.

### 3.4 Effort estimate

| Phase | Effort | Risk |
| --- | --- | --- |
| 0 — CI build gate (net48, full MSBuild) | 0.5 day | very low |
| 1 — `packages.config` → `PackageReference` (still net48) | 0.5 day | very low |
| 2 — SDK-style `.csproj`, still `net48` | 1 day | low |
| 3 — Multi-target `net48;net8.0-windows` | 1 day | low |
| 4 — Drop `net48`, .NET 8 only + publish/deploy story | 1–2 days | low–medium (deployment, not code) |
| **Total** | **~4–5 engineer-days** | **low** |

Estimate excludes the optional `System.Text.Json` swap (~1 day + test) and any installer/packaging rework driven by the runtime-distribution decision.

### 3.5 Why .NET 8 rather than .NET 10

Recommend **.NET 8 (LTS)** as the target. .NET 10 offers nothing this app needs, and .NET 8 has the deeper ecosystem/tooling track record. Because the app has a single dependency and no framework-specific coupling, moving 8 → 10 later is a one-line `<TargetFramework>` change.

---

## 4. Recommended phased plan

Each phase ends with a green build and is independently shippable/revertible.

**Phase 0 — Establish a build gate (do this first).**
Add a `windows-latest` CI job that runs `nuget restore` + full MSBuild Release on `OtterWorks.Desktop.sln`. Today nothing verifies this project automatically, so every later phase would be unverified. This is the highest-value single change in the whole plan and is independent of migration.

**Phase 1 — `packages.config` → `PackageReference`, still targeting net48.**
Removes `packages/`, the `HintPath`, and the `packages.config` `None` item. Only one package to move. Fully reversible; validates that restore/build still works under the new dependency model before touching the project format.

**Phase 2 — Convert to SDK-style `.csproj`, still targeting net48.**
The core of the work, deliberately decoupled from the framework change so any breakage is unambiguously attributable to the project-format conversion. Delete explicit `Compile`/`Reference` items, add `UseWPF`, keep `<TargetFramework>net48</TargetFramework>`, keep `GenerateAssemblyInfo=false` initially. Validate that `dotnet build` now succeeds — this is the point where the CLI stops failing on `InitializeComponent`.

**Phase 3 — Multi-target `net48;net8.0-windows`.**
Add the `ProtectedData` package (conditioned on the modern TFM or unconditional — it works on both). Both TFMs build and both binaries are manually smoke-tested against a local backend. This de-risks the cutover: the net48 build remains the shipping artifact while the .NET 8 build is validated in parallel.

**Phase 4 — Drop `net48`; ship .NET 8 only.**
Remove `LangVersion`, delete `app.config`, decide self-contained vs. framework-dependent publish, update `README.md` build instructions and the CI job to `dotnet publish`, and re-run the full manual verification flow.

**Optional follow-up (not part of the migration).** Replace Newtonsoft.Json with `System.Text.Json`; modernize `AppDomain.CurrentDomain.BaseDirectory` → `AppContext.BaseDirectory`; consider `Microsoft.Extensions.Configuration` for `appsettings.json` and DI for view-model composition (`App.xaml.cs:11-24`).

---

## 5. Changes made alongside this assessment

Deliberately minimal, and build-verified with full MSBuild (0 warnings, 0 errors):

- **`Newtonsoft.Json` 13.0.3 → 13.0.4** in `packages.config` and the `.csproj` `HintPath`. Verified `13.0.4` ships `lib\net45`, and the Release output carries `FileVersion 13.0.4.30916`.

**`LangVersion` was intentionally left at 7.3.** C# 7.3 is the last language version officially supported on .NET Framework / non-SDK projects; newer versions compile only partially there and depend on types the framework does not ship. Raising it would be exactly the kind of unverifiable change this assessment is meant to avoid — the correct place to gain modern C# is Phase 3/4, where `net8.0-windows` brings C# 12 for free.
