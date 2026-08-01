[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$MaximumWorkerLineBytes = 4096
$PlanPath = [Environment]::GetEnvironmentVariable("SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE", "Process")
if ([string]::IsNullOrWhiteSpace($PlanPath)) { throw "launcher_private_plan_missing" }
$ExpectedPlanSha256 = [Environment]::GetEnvironmentVariable("SWORD_LAUNCHER_N1_PRIVATE_PLAN_SHA256", "Process")
if ([string]::IsNullOrWhiteSpace($ExpectedPlanSha256) -or $ExpectedPlanSha256 -cnotmatch "^[a-f0-9]{64}$") {
    throw "launcher_private_plan_identity_missing"
}
$ExpectedAuthorityLeaseProof = [Environment]::GetEnvironmentVariable("SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF", "Process")
if ([string]::IsNullOrWhiteSpace($ExpectedAuthorityLeaseProof) -or $ExpectedAuthorityLeaseProof -cnotmatch "^lp_[a-f0-9]{64}$") {
    throw "launcher_private_lease_missing"
}

Import-Module (Join-Path $PSScriptRoot "launcher-service-plan.psm1") -Force -ErrorAction Stop
$PlanSet = Read-LauncherPrivateServicePlans -Path $PlanPath -ExpectedPlanSha256 $ExpectedPlanSha256

$nativeSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

internal static class SwordLauncherNativeMethods
{
    internal const uint CREATE_SUSPENDED = 0x00000004;
    internal const uint CREATE_NO_WINDOW = 0x08000000;
    internal const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    internal const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    internal const int JobObjectBasicAccountingInformation = 1;
    internal const int JobObjectExtendedLimitInformation = 9;
    internal const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    internal const uint SYNCHRONIZE = 0x00100000;

    [StructLayout(LayoutKind.Sequential)]
    internal struct STARTUPINFO
    {
        internal uint cb;
        internal IntPtr lpReserved;
        internal IntPtr lpDesktop;
        internal IntPtr lpTitle;
        internal uint dwX;
        internal uint dwY;
        internal uint dwXSize;
        internal uint dwYSize;
        internal uint dwXCountChars;
        internal uint dwFillAttribute;
        internal uint dwFlags;
        internal ushort wShowWindow;
        internal ushort cbReserved2;
        internal IntPtr lpReserved2;
        internal IntPtr hStdInput;
        internal IntPtr hStdOutput;
        internal IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PROCESS_INFORMATION
    {
        internal IntPtr hProcess;
        internal IntPtr hThread;
        internal uint dwProcessId;
        internal uint dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct IO_COUNTERS
    {
        internal ulong ReadOperationCount;
        internal ulong WriteOperationCount;
        internal ulong OtherOperationCount;
        internal ulong ReadTransferCount;
        internal ulong WriteTransferCount;
        internal ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        internal long PerProcessUserTimeLimit;
        internal long PerJobUserTimeLimit;
        internal uint LimitFlags;
        internal UIntPtr MinimumWorkingSetSize;
        internal UIntPtr MaximumWorkingSetSize;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint PriorityClass;
        internal uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        internal JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        internal IO_COUNTERS IoInfo;
        internal UIntPtr ProcessMemoryLimit;
        internal UIntPtr JobMemoryLimit;
        internal UIntPtr PeakProcessMemoryUsed;
        internal UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
    {
        internal long TotalUserTime;
        internal long TotalKernelTime;
        internal long ThisPeriodTotalUserTime;
        internal long ThisPeriodTotalKernelTime;
        internal uint TotalPageFaultCount;
        internal uint TotalProcesses;
        internal uint ActiveProcesses;
        internal uint TotalTerminatedProcesses;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct FILETIME
    {
        internal uint Low;
        internal uint High;
        internal long ToInt64() { return ((long)High << 32) | Low; }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length, IntPtr returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CreateProcessW(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        bool inheritHandles,
        uint creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref STARTUPINFO startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool TerminateJobObject(IntPtr job, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetProcessTimes(IntPtr process, out FILETIME creation, out FILETIME exit, out FILETIME kernel, out FILETIME user);
}

public sealed class SwordLauncherOwnedJob : IDisposable
{
    private IntPtr _job;
    private IntPtr _rootProcess;
    private bool _closed;

    public int ProcessId { get; private set; }
    public long CreationFileTimeUtc { get; private set; }

    private SwordLauncherOwnedJob(IntPtr job, IntPtr rootProcess, int processId, long creationFileTimeUtc)
    {
        _job = job;
        _rootProcess = rootProcess;
        ProcessId = processId;
        CreationFileTimeUtc = creationFileTimeUtc;
    }

    public static SwordLauncherOwnedJob Start(string fileName, string[] arguments, string workingDirectory, string environmentBlock)
    {
        IntPtr job = IntPtr.Zero;
        IntPtr environment = IntPtr.Zero;
        SwordLauncherNativeMethods.PROCESS_INFORMATION processInfo = new SwordLauncherNativeMethods.PROCESS_INFORMATION();
        try
        {
            job = SwordLauncherNativeMethods.CreateJobObjectW(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "job_create_failed");
            SwordLauncherNativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new SwordLauncherNativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = SwordLauncherNativeMethods.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int limitSize = Marshal.SizeOf<SwordLauncherNativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION>();
            IntPtr limitBuffer = Marshal.AllocHGlobal(limitSize);
            try
            {
                Marshal.StructureToPtr(limits, limitBuffer, false);
                if (!SwordLauncherNativeMethods.SetInformationJobObject(job, SwordLauncherNativeMethods.JobObjectExtendedLimitInformation, limitBuffer, (uint)limitSize))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "job_limit_failed");
            }
            finally { Marshal.FreeHGlobal(limitBuffer); }

            string command = Quote(fileName);
            foreach (string argument in arguments ?? Array.Empty<string>()) command += " " + Quote(argument ?? String.Empty);
            StringBuilder mutableCommand = new StringBuilder(command);
            SwordLauncherNativeMethods.STARTUPINFO startup = new SwordLauncherNativeMethods.STARTUPINFO();
            startup.cb = (uint)Marshal.SizeOf<SwordLauncherNativeMethods.STARTUPINFO>();
            environment = Marshal.StringToHGlobalUni(environmentBlock ?? "\0");
            uint flags = SwordLauncherNativeMethods.CREATE_SUSPENDED |
                SwordLauncherNativeMethods.CREATE_NO_WINDOW |
                SwordLauncherNativeMethods.CREATE_UNICODE_ENVIRONMENT;
            if (!SwordLauncherNativeMethods.CreateProcessW(fileName, mutableCommand, IntPtr.Zero, IntPtr.Zero, false, flags, environment, workingDirectory, ref startup, out processInfo))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "process_create_failed");
            if (!SwordLauncherNativeMethods.AssignProcessToJobObject(job, processInfo.hProcess))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "job_assign_failed");
            SwordLauncherNativeMethods.FILETIME creation;
            SwordLauncherNativeMethods.FILETIME exit;
            SwordLauncherNativeMethods.FILETIME kernel;
            SwordLauncherNativeMethods.FILETIME user;
            if (!SwordLauncherNativeMethods.GetProcessTimes(processInfo.hProcess, out creation, out exit, out kernel, out user))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "process_identity_failed");
            if (SwordLauncherNativeMethods.ResumeThread(processInfo.hThread) == UInt32.MaxValue)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "process_resume_failed");
            SwordLauncherNativeMethods.CloseHandle(processInfo.hThread);
            processInfo.hThread = IntPtr.Zero;
            SwordLauncherOwnedJob owned = new SwordLauncherOwnedJob(job, processInfo.hProcess, checked((int)processInfo.dwProcessId), creation.ToInt64());
            job = IntPtr.Zero;
            processInfo.hProcess = IntPtr.Zero;
            return owned;
        }
        catch
        {
            if (processInfo.hProcess != IntPtr.Zero) SwordLauncherNativeMethods.TerminateProcess(processInfo.hProcess, 1);
            if (processInfo.hThread != IntPtr.Zero) SwordLauncherNativeMethods.CloseHandle(processInfo.hThread);
            if (processInfo.hProcess != IntPtr.Zero) SwordLauncherNativeMethods.CloseHandle(processInfo.hProcess);
            if (job != IntPtr.Zero) SwordLauncherNativeMethods.CloseHandle(job);
            throw;
        }
        finally
        {
            if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
        }
    }

    public int ObserveRoot()
    {
        return ObserveProcess(ProcessId, CreationFileTimeUtc);
    }

    public bool ContainsProcess(int processId)
    {
        if (_closed || _job == IntPtr.Zero || processId <= 0) return false;
        IntPtr process = SwordLauncherNativeMethods.OpenProcess(SwordLauncherNativeMethods.PROCESS_QUERY_LIMITED_INFORMATION, false, checked((uint)processId));
        if (process == IntPtr.Zero) return false;
        try
        {
            bool contained;
            return SwordLauncherNativeMethods.IsProcessInJob(process, _job, out contained) && contained;
        }
        finally { SwordLauncherNativeMethods.CloseHandle(process); }
    }

    public int ActiveProcessCount()
    {
        if (_closed || _job == IntPtr.Zero) return 0;
        int size = Marshal.SizeOf<SwordLauncherNativeMethods.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>();
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            if (!SwordLauncherNativeMethods.QueryInformationJobObject(_job, SwordLauncherNativeMethods.JobObjectBasicAccountingInformation, buffer, (uint)size, IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "job_query_failed");
            return checked((int)Marshal.PtrToStructure<SwordLauncherNativeMethods.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>(buffer).ActiveProcesses);
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    public bool TerminateAndClose(int timeoutMilliseconds)
    {
        if (_closed) return true;
        if (timeoutMilliseconds < 0 || timeoutMilliseconds > 300000) return false;
        if (_job != IntPtr.Zero && ActiveProcessCount() > 0 && !SwordLauncherNativeMethods.TerminateJobObject(_job, 1))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "job_stop_failed");
        Stopwatch wait = Stopwatch.StartNew();
        while (_job != IntPtr.Zero && ActiveProcessCount() > 0)
        {
            if (wait.ElapsedMilliseconds >= timeoutMilliseconds) return false;
            Thread.Sleep(25);
        }
        Dispose();
        return true;
    }

    public void Dispose()
    {
        if (_closed) return;
        if (_job != IntPtr.Zero) SwordLauncherNativeMethods.CloseHandle(_job);
        if (_rootProcess != IntPtr.Zero) SwordLauncherNativeMethods.CloseHandle(_rootProcess);
        _job = IntPtr.Zero;
        _rootProcess = IntPtr.Zero;
        _closed = true;
    }

    public static int ObserveProcess(int processId, long expectedCreationFileTimeUtc)
    {
        if (processId <= 0 || expectedCreationFileTimeUtc <= 0) return 3;
        IntPtr process = SwordLauncherNativeMethods.OpenProcess(SwordLauncherNativeMethods.PROCESS_QUERY_LIMITED_INFORMATION | SwordLauncherNativeMethods.SYNCHRONIZE, false, checked((uint)processId));
        if (process == IntPtr.Zero)
        {
            int error = Marshal.GetLastWin32Error();
            return error == 87 || error == 1168 ? 0 : 3;
        }
        try
        {
            SwordLauncherNativeMethods.FILETIME creation;
            SwordLauncherNativeMethods.FILETIME exit;
            SwordLauncherNativeMethods.FILETIME kernel;
            SwordLauncherNativeMethods.FILETIME user;
            if (!SwordLauncherNativeMethods.GetProcessTimes(process, out creation, out exit, out kernel, out user)) return 3;
            return creation.ToInt64() == expectedCreationFileTimeUtc ? 1 : 2;
        }
        finally { SwordLauncherNativeMethods.CloseHandle(process); }
    }

    private static string Quote(string value)
    {
        if (value == null) value = String.Empty;
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0) return value;
        StringBuilder result = new StringBuilder();
        result.Append('"');
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"')
            {
                result.Append('\\', slashes * 2 + 1);
                result.Append('"');
                slashes = 0;
                continue;
            }
            result.Append('\\', slashes);
            slashes = 0;
            result.Append(c);
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }
}
'@

Add-Type -TypeDefinition $nativeSource -Language CSharp -ErrorAction Stop

$Jobs = @{}
$ActiveSupervisorGeneration = $null
$ActiveAuthorityLeaseProof = $null
$SeenDispatches = @{}
$ReservedEnvironmentNames = @(
    "SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE",
    "SWORD_LAUNCHER_N1_PRIVATE_PLAN_SHA256",
    "SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF"
)
$OperationPattern = "^lop_[a-z0-9]{8,64}$"
$NoncePattern = "^lw_[a-z0-9]{16,64}$"
$DispatchPattern = "^ld_[a-z0-9]{16,64}$"
$ShaPattern = "^[a-f0-9]{64}$"
$ServicePattern = "^[a-z][a-z0-9_]{0,63}$"
$ResultClasses = @(
    "accepted", "ready", "optional_absent", "external_ready", "stopped", "spawn_failed", "early_exit",
    "listener_mismatch", "readiness_timeout", "stop_failed", "deadline", "cancelled", "invalid_request", "internal_failure"
)

function New-LauncherWorkerResult {
    param(
        [Parameter(Mandatory = $true)][object]$Request,
        [Parameter(Mandatory = $true)][string]$ResultClass,
        [Parameter(Mandatory = $true)][string]$OwnershipClass,
        [Parameter(Mandatory = $true)][string]$ListenerClass,
        [Parameter(Mandatory = $true)][string]$DescendantClass
    )
    if ($ResultClasses -notcontains $ResultClass) { throw "launcher_worker_result_invalid" }
    return [ordered]@{
        schema_version = "launcher_worker.v2"
        message_type = "result"
        operation_id = [string]$Request.operation_id
        supervisor_generation = [long]$Request.supervisor_generation
        authority_lease_proof = [string]$Request.authority_lease_proof
        dispatch_id = [string]$Request.dispatch_id
        service_id = [string]$Request.service_id
        action = [string]$Request.action
        expected_revision = [long]$Request.expected_revision
        worker_nonce = [string]$Request.worker_nonce
        result_class = $ResultClass
        ownership_class = $OwnershipClass
        listener_class = $ListenerClass
        descendant_class = $DescendantClass
    }
}

function Write-LauncherWorkerResult {
    param([Parameter(Mandatory = $true)][object]$Result)
    $json = $Result | ConvertTo-Json -Compress -Depth 8
    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt $MaximumWorkerLineBytes) { throw "launcher_worker_result_oversized" }
    [Console]::Out.WriteLine($json)
    [Console]::Out.Flush()
}

function Test-LauncherWorkerRequest {
    param([Parameter(Mandatory = $true)][object]$Request)
    $expected = @(
        "schema_version", "message_type", "operation_id", "supervisor_generation", "authority_lease_proof", "dispatch_id", "graph_sha256", "binding_sha256",
        "service_id", "action", "adapter_class", "expected_revision", "deadline_ms", "worker_nonce"
    ) | Sort-Object
    $actual = @($Request.PSObject.Properties.Name | Sort-Object)
    if (($actual -join "`n") -cne ($expected -join "`n")) { return $false }
    if ($Request.schema_version -cne "launcher_worker.v2" -or $Request.message_type -cne "request") { return $false }
    if ([string]$Request.operation_id -cnotmatch $OperationPattern -or [string]$Request.graph_sha256 -cnotmatch $ShaPattern -or
        [string]$Request.binding_sha256 -cnotmatch $ShaPattern -or [string]$Request.service_id -cnotmatch $ServicePattern -or
        [string]$Request.worker_nonce -cnotmatch $NoncePattern -or [string]$Request.dispatch_id -cnotmatch $DispatchPattern) { return $false }
    if ($Request.supervisor_generation -isnot [int] -and $Request.supervisor_generation -isnot [long]) { return $false }
    if ([long]$Request.supervisor_generation -lt 1 -or [long]$Request.supervisor_generation -gt 9007199254740991) { return $false }
    if ([string]$Request.authority_lease_proof -cne $ExpectedAuthorityLeaseProof) { return $false }
    if (@("start", "probe", "stop") -notcontains [string]$Request.action) { return $false }
    if (@("job_worker_service", "job_worker_job_close", "external_probe_only", "external_noop") -notcontains [string]$Request.adapter_class) { return $false }
    if ($Request.expected_revision -isnot [int] -and $Request.expected_revision -isnot [long]) { return $false }
    if ([long]$Request.expected_revision -lt 0 -or [long]$Request.expected_revision -gt 9007199254740991) { return $false }
    if ($Request.deadline_ms -isnot [int] -and $Request.deadline_ms -isnot [long]) { return $false }
    if ([long]$Request.deadline_ms -lt 0 -or [long]$Request.deadline_ms -gt 300000) { return $false }
    return $true
}

function Test-LauncherResolvedAdapter {
    param([Parameter(Mandatory = $true)][object]$Request, [Parameter(Mandatory = $true)][object]$Plan)
    $external = [string]$Plan.Ownership -ceq "external"
    $allowedAdapter = switch ([string]$Request.action) {
        "start" { if ($external) { return $false } else { "job_worker_service" } }
        "stop" { if ($external) { "external_noop" } else { "job_worker_job_close" } }
        "probe" { if ($external) { "external_probe_only" } else { "job_worker_service" } }
    }
    return [string]$Request.adapter_class -ceq $allowedAdapter
}

function ConvertTo-LauncherEnvironmentBlock {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $environment = @{}
    foreach ($name in $Plan.Environment.Keys) { $environment[[string]$name] = [string]$Plan.Environment[$name] }
    foreach ($name in $ReservedEnvironmentNames) { $environment.Remove($name) }
    $pairs = @($environment.Keys | Sort-Object | ForEach-Object { "$_=$($environment[$_])" })
    return ($pairs -join [char]0) + [char]0 + [char]0
}

function Test-LauncherPlanPath {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$Directory)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
    if ($Directory) { return [bool]$item.PSIsContainer }
    return -not $item.PSIsContainer
}

function Get-LauncherListenerObservation {
    param([Parameter(Mandatory = $true)][object]$Record)
    if ([int]$Record.Plan.ListenerPort -eq 0) {
        return [pscustomobject]@{ State = "matched"; Class = "matched" }
    }
    if ($null -eq (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{ State = "unknown"; Class = "unknown" }
    }
    $listeners = @(Get-NetTCPConnection -LocalPort ([int]$Record.Plan.ListenerPort) -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listeners.Count -eq 0) { return [pscustomobject]@{ State = "pending"; Class = "unknown" } }
    foreach ($listenerPid in $listeners) {
        if (-not $Record.Native.ContainsProcess([int]$listenerPid)) {
            return [pscustomobject]@{ State = "mismatch"; Class = "mismatch" }
        }
    }
    return [pscustomobject]@{ State = "matched"; Class = "matched" }
}

function Get-LauncherForeignListenerPresent {
    param([Parameter(Mandatory = $true)][int]$Port)
    if ($Port -eq 0) { return $false }
    if ($null -eq (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $true }
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -gt 0
}

function Wait-LauncherOwnedReady {
    param([Parameter(Mandatory = $true)][object]$Request, [Parameter(Mandatory = $true)][object]$Record)
    $deadline = [DateTimeOffset]::UtcNow.AddMilliseconds([long]$Request.deadline_ms)
    do {
        $identity = [int]$Record.Native.ObserveRoot()
        if ($identity -eq 2) { return New-LauncherWorkerResult $Request "listener_mismatch" "mismatch" "unknown" "unknown" }
        if ($identity -eq 0 -or $Record.Native.ActiveProcessCount() -eq 0) {
            return New-LauncherWorkerResult $Request "early_exit" "matched" "unknown" "owned_clear"
        }
        if ($identity -ne 1) { return New-LauncherWorkerResult $Request "internal_failure" "unknown" "unknown" "unknown" }
        $listener = Get-LauncherListenerObservation -Record $Record
        if ($listener.State -eq "mismatch") { return New-LauncherWorkerResult $Request "listener_mismatch" "matched" "mismatch" "foreign" }
        if ($listener.State -eq "matched") { return New-LauncherWorkerResult $Request "ready" "matched" $listener.Class "owned_active" }
        Start-Sleep -Milliseconds 50
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    return New-LauncherWorkerResult $Request "readiness_timeout" "matched" "unknown" "owned_active"
}

function Invoke-LauncherStart {
    param([Parameter(Mandatory = $true)][object]$Request, [Parameter(Mandatory = $true)][AllowNull()][object]$Plan)
    if ($null -eq $Plan) { return New-LauncherWorkerResult $Request "spawn_failed" "not_applicable" "not_applicable" "owned_clear" }
    if ($Jobs.ContainsKey([string]$Request.service_id)) {
        $record = $Jobs[[string]$Request.service_id]
        $identity = [int]$record.Native.ObserveRoot()
        if ($identity -eq 2) { return New-LauncherWorkerResult $Request "listener_mismatch" "mismatch" "unknown" "unknown" }
        if ($identity -eq 0 -or $record.Native.ActiveProcessCount() -eq 0) {
            return New-LauncherWorkerResult $Request "early_exit" "matched" "unknown" "owned_clear"
        }
        if ($identity -ne 1) { return New-LauncherWorkerResult $Request "internal_failure" "unknown" "unknown" "unknown" }
        return New-LauncherWorkerResult $Request "accepted" "matched" "not_applicable" "owned_active"
    }
    if (-not (Test-LauncherPlanPath -Path $Plan.FilePath) -or -not (Test-LauncherPlanPath -Path $Plan.WorkingDirectory -Directory)) {
        return New-LauncherWorkerResult $Request "spawn_failed" "unknown" "not_applicable" "owned_clear"
    }
    if (Get-LauncherForeignListenerPresent -Port ([int]$Plan.ListenerPort)) {
        return New-LauncherWorkerResult $Request "listener_mismatch" "mismatch" "mismatch" "foreign"
    }
    try {
        $native = [SwordLauncherOwnedJob]::Start(
            [string]$Plan.FilePath,
            [string[]]$Plan.Arguments,
            [string]$Plan.WorkingDirectory,
            (ConvertTo-LauncherEnvironmentBlock -Plan $Plan)
        )
        $record = [pscustomobject]@{ Native = $native; Plan = $Plan }
        $Jobs[[string]$Request.service_id] = $record
        if ([int]$native.ObserveRoot() -ne 1 -or $native.ActiveProcessCount() -eq 0) {
            return New-LauncherWorkerResult $Request "early_exit" "matched" "unknown" "owned_clear"
        }
        return New-LauncherWorkerResult $Request "accepted" "matched" "not_applicable" "owned_active"
    }
    catch {
        return New-LauncherWorkerResult $Request "spawn_failed" "unknown" "not_applicable" "unknown"
    }
}

function Invoke-LauncherProbe {
    param([Parameter(Mandatory = $true)][object]$Request, [Parameter(Mandatory = $true)][AllowNull()][object]$Plan)
    if ($null -eq $Plan) {
        return New-LauncherWorkerResult $Request "optional_absent" "not_applicable" "not_applicable" "owned_clear"
    }
    if ($Plan.Ownership -eq "external") {
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync("127.0.0.1", [int]$Plan.ListenerPort)
            if (-not $task.Wait([Math]::Max(1, [int]$Request.deadline_ms)) -or -not $client.Connected) {
                return New-LauncherWorkerResult $Request "readiness_timeout" "not_applicable" "not_applicable" "not_applicable"
            }
            return New-LauncherWorkerResult $Request "external_ready" "not_applicable" "matched" "not_applicable"
        }
        catch { return New-LauncherWorkerResult $Request "readiness_timeout" "not_applicable" "not_applicable" "not_applicable" }
        finally { $client.Dispose() }
    }
    if (-not $Jobs.ContainsKey([string]$Request.service_id)) {
        return New-LauncherWorkerResult $Request "early_exit" "unknown" "unknown" "owned_clear"
    }
    return Wait-LauncherOwnedReady -Request $Request -Record $Jobs[[string]$Request.service_id]
}

function Invoke-LauncherStop {
    param([Parameter(Mandatory = $true)][object]$Request, [Parameter(Mandatory = $true)][AllowNull()][object]$Plan)
    if ($null -eq $Plan) {
        return New-LauncherWorkerResult $Request "stopped" "matched" "not_applicable" "owned_clear"
    }
    if ($Plan.Ownership -eq "external") {
        return New-LauncherWorkerResult $Request "stopped" "not_applicable" "not_applicable" "not_applicable"
    }
    if (-not $Jobs.ContainsKey([string]$Request.service_id)) {
        return New-LauncherWorkerResult $Request "stop_failed" "unknown" "unknown" "unknown"
    }
    $record = $Jobs[[string]$Request.service_id]
    $identity = [int]$record.Native.ObserveRoot()
    if ($identity -eq 2) { return New-LauncherWorkerResult $Request "stop_failed" "unknown" "unknown" "unknown" }
    if ($identity -eq 3) { return New-LauncherWorkerResult $Request "stop_failed" "unknown" "unknown" "unknown" }
    $listener = Get-LauncherListenerObservation -Record $record
    if ($listener.State -eq "mismatch") { return New-LauncherWorkerResult $Request "stop_failed" "matched" "unknown" "unknown" }
    try {
        if (-not $record.Native.TerminateAndClose([int]$Request.deadline_ms)) {
            return New-LauncherWorkerResult $Request "stop_failed" "matched" $listener.Class "owned_active"
        }
        $Jobs.Remove([string]$Request.service_id)
        return New-LauncherWorkerResult $Request "stopped" "matched" $listener.Class "owned_clear"
    }
    catch { return New-LauncherWorkerResult $Request "stop_failed" "matched" $listener.Class "unknown" }
}

function New-LauncherInvalidRequestResult {
    param([Parameter(Mandatory = $true)][object]$Request)
    try {
        if ([string]$Request.operation_id -notmatch $OperationPattern -or [string]$Request.service_id -notmatch $ServicePattern -or
            [string]$Request.action -notin @("start", "probe", "stop") -or [string]$Request.worker_nonce -notmatch $NoncePattern -or
            [string]$Request.dispatch_id -notmatch $DispatchPattern -or
            ($Request.supervisor_generation -isnot [int] -and $Request.supervisor_generation -isnot [long]) -or
            ($Request.expected_revision -isnot [int] -and $Request.expected_revision -isnot [long])) { return $null }
        return New-LauncherWorkerResult $Request "invalid_request" "unknown" "unknown" "unknown"
    }
    catch { return $null }
}

try {
    while ($true) {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line) { break }
        if ([Text.Encoding]::UTF8.GetByteCount($line) -gt $MaximumWorkerLineBytes) { continue }
        try { $request = $line | ConvertFrom-Json -Depth 16 -ErrorAction Stop } catch { continue }
        if (-not (Test-LauncherWorkerRequest -Request $request)) {
            $invalid = New-LauncherInvalidRequestResult -Request $request
            if ($null -ne $invalid) { Write-LauncherWorkerResult -Result $invalid }
            continue
        }
        try {
            $plan = Resolve-LauncherServicePlan `
                -PlanSet $PlanSet `
                -ServiceId ([string]$request.service_id) `
                -GraphSha256 ([string]$request.graph_sha256) `
                -BindingSha256 ([string]$request.binding_sha256)
        }
        catch {
            Write-LauncherWorkerResult -Result (New-LauncherWorkerResult $request "invalid_request" "unknown" "unknown" "unknown")
            continue
        }
        if (-not (Test-LauncherResolvedAdapter -Request $request -Plan $plan)) {
            Write-LauncherWorkerResult -Result (New-LauncherWorkerResult $request "invalid_request" "unknown" "unknown" "unknown")
            continue
        }
        if ($null -eq $ActiveSupervisorGeneration) {
            $ActiveSupervisorGeneration = [long]$request.supervisor_generation
            $ActiveAuthorityLeaseProof = [string]$request.authority_lease_proof
        }
        if ([long]$request.supervisor_generation -ne [long]$ActiveSupervisorGeneration -or
            [string]$request.authority_lease_proof -cne [string]$ActiveAuthorityLeaseProof -or
            $SeenDispatches.ContainsKey([string]$request.dispatch_id)) {
            Write-LauncherWorkerResult -Result (New-LauncherWorkerResult $request "invalid_request" "unknown" "unknown" "unknown")
            continue
        }
        $SeenDispatches[[string]$request.dispatch_id] = $true
        try {
            $result = switch ([string]$request.action) {
                "start" { Invoke-LauncherStart -Request $request -Plan $plan }
                "probe" { Invoke-LauncherProbe -Request $request -Plan $plan }
                "stop" { Invoke-LauncherStop -Request $request -Plan $plan }
            }
        }
        catch {
            $result = New-LauncherWorkerResult $request "internal_failure" "unknown" "unknown" "unknown"
        }
        Write-LauncherWorkerResult -Result $result
    }
}
finally {
    foreach ($record in @($Jobs.Values)) {
        try { $record.Native.Dispose() } catch {}
    }
    $Jobs.Clear()
}
