"""Exact ownership and bounded cleanup for one local child process tree.

The owner never scans the process table or terminates by executable name.  On
Windows, the child is placed in a private Job Object atomically by
``CreateProcessW`` through ``PROC_THREAD_ATTRIBUTE_JOB_LIST``.  On POSIX, the
child is the leader of a private session/process group and is observed without
reaping until the final group signal has been sent.

The POSIX proof covers descendants that remain in the inherited process group.
An adversarial descendant that creates a new session is outside this portable
process-group proof and must not be reported as covered by this helper.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import BinaryIO


class OwnedProcessCleanupError(subprocess.SubprocessError):
    """The exact child tree could not be proven stopped and reaped."""


def _run_cleanup_actions(actions: Sequence[Callable[[], None]]) -> None:
    pending = list(actions)
    for _ in range(2):
        failed: list[Callable[[], None]] = []
        for action in pending:
            try:
                action()
            except (OSError, subprocess.SubprocessError):
                failed.append(action)
        if not failed:
            return
        pending = failed
    raise OwnedProcessCleanupError("owned_process_cleanup_incomplete")


class _CapturedStreams:
    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        input_text: str,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self._input = input_text.encode("utf-8")
        self._stdout_chunks: list[bytes] = []
        self._stderr_chunks: list[bytes] = []
        self._threads = [
            threading.Thread(target=self._write, daemon=True),
            threading.Thread(
                target=self._read,
                args=(self._stdout, self._stdout_chunks),
                daemon=True,
            ),
            threading.Thread(
                target=self._read,
                args=(self._stderr, self._stderr_chunks),
                daemon=True,
            ),
        ]
        self._started_threads: list[threading.Thread] = []

    def start(self) -> None:
        for thread in self._threads:
            thread.start()
            self._started_threads.append(thread)

    def finish(self, timeout_s: float) -> tuple[str, str]:
        deadline = time.monotonic() + timeout_s
        for thread in self._started_threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._started_threads):
            self.close()
            raise OwnedProcessCleanupError("owned_process_stream_cleanup_incomplete")
        return (
            b"".join(self._stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(self._stderr_chunks).decode("utf-8", errors="replace"),
        )

    def close(self) -> None:
        for stream in (self._stdin, self._stdout, self._stderr):
            try:
                stream.close()
            except OSError:
                pass

    def _write(self) -> None:
        try:
            if self._input:
                self._stdin.write(self._input)
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                self._stdin.close()
            except OSError:
                pass

    @staticmethod
    def _read(stream: BinaryIO, chunks: list[bytes]) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                chunks.append(chunk)
        except (OSError, ValueError):
            return
        finally:
            try:
                stream.close()
            except OSError:
                pass


class _PosixProcessGroup:
    def __init__(self, process_group_id: int) -> None:
        self._process_group_id = process_group_id
        self._closed = False

    def terminate(self, *, force: bool) -> None:
        if self._closed:
            return
        try:
            os.killpg(
                self._process_group_id,
                signal.SIGKILL if force else signal.SIGTERM,
            )
        except ProcessLookupError:
            return

    def wait_empty(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                os.killpg(self._process_group_id, 0)
            except ProcessLookupError:
                return
            except PermissionError as exc:
                raise OwnedProcessCleanupError(
                    "owned_process_group_cleanup_unverifiable"
                ) from exc
            if time.monotonic() >= deadline:
                raise OwnedProcessCleanupError("owned_process_group_cleanup_incomplete")
            time.sleep(0.02)

    def close(self) -> None:
        self._closed = True


if os.name == "nt":  # pragma: no branch - platform-selected implementation
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _HANDLE_FLAG_INHERIT = 0x00000001
    _INFINITE = 0xFFFFFFFF
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
    _STARTF_USESTDHANDLES = 0x00000100
    _WAIT_FAILED = 0xFFFFFFFF
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _StartupInfoW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _StartupInfoExW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _StartupInfoW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectBasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _configure_kernel32() -> ctypes.WinDLL:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SecurityAttributes),
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.SetHandleInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel32.SetHandleInformation.restype = wintypes.BOOL
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW),
            ctypes.POINTER(_ProcessInformation),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        return kernel32

    class _WindowsJobObject:
        def __init__(self, kernel32: ctypes.WinDLL) -> None:
            self._kernel32 = kernel32
            self._handle = kernel32.CreateJobObjectW(None, None)
            self._closed = False
            if not self._handle:
                raise OSError("owned_process_job_setup_failed")
            info = _JobObjectExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                self._handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                self.close()
                raise OSError("owned_process_job_setup_failed")

        @property
        def handle(self) -> wintypes.HANDLE:
            return self._handle

        def terminate(self) -> None:
            if self._closed or self.active_process_count() == 0:
                return
            if not self._kernel32.TerminateJobObject(self._handle, 1):
                raise OwnedProcessCleanupError("owned_process_job_cleanup_failed")

        def active_process_count(self) -> int:
            if self._closed:
                return 0
            info = _JobObjectBasicAccountingInformation()
            if not self._kernel32.QueryInformationJobObject(
                self._handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            ):
                raise OwnedProcessCleanupError("owned_process_job_query_failed")
            return int(info.ActiveProcesses)

        def wait_empty(self, timeout_s: float) -> None:
            deadline = time.monotonic() + timeout_s
            while self.active_process_count() != 0:
                if time.monotonic() >= deadline:
                    raise OwnedProcessCleanupError(
                        "owned_process_job_cleanup_incomplete"
                    )
                time.sleep(0.02)

        def close(self) -> None:
            if self._closed:
                return
            if self._handle:
                _close_windows_handles(self._kernel32, [self._handle])
                self._handle = None
            self._closed = True

    class _WindowsCreatedProcess:
        def __init__(
            self,
            kernel32: ctypes.WinDLL,
            process_handle: wintypes.HANDLE,
            stdin: BinaryIO,
            stdout: BinaryIO,
            stderr: BinaryIO,
        ) -> None:
            self.kernel32 = kernel32
            self.process_handle = process_handle
            self.stdin = stdin
            self.stdout = stdout
            self.stderr = stderr
            self._closed = False

        def exited(self) -> bool:
            status = self.kernel32.WaitForSingleObject(self.process_handle, 0)
            if status == _WAIT_OBJECT_0:
                return True
            if status == _WAIT_TIMEOUT:
                return False
            raise OwnedProcessCleanupError("owned_process_wait_failed")

        def wait(self, timeout_s: float) -> None:
            timeout_ms = max(1, min(int(timeout_s * 1000), _INFINITE - 1))
            status = self.kernel32.WaitForSingleObject(
                self.process_handle,
                timeout_ms,
            )
            if status != _WAIT_OBJECT_0:
                raise OwnedProcessCleanupError("owned_process_wait_incomplete")

        def returncode(self) -> int:
            value = wintypes.DWORD()
            if not self.kernel32.GetExitCodeProcess(
                self.process_handle,
                ctypes.byref(value),
            ):
                raise OwnedProcessCleanupError("owned_process_exit_code_failed")
            return int(ctypes.c_long(value.value).value)

        def close(self) -> None:
            if self._closed:
                return
            if self.process_handle:
                _close_windows_handles(self.kernel32, [self.process_handle])
                self.process_handle = None
            self._closed = True


def run_owned_process(
    args: Sequence[str],
    *,
    input_text: str,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    active_check: Callable[[], None] | None = None,
    poll_interval_s: float = 0.1,
    cleanup_timeout_s: float = 2.0,
) -> subprocess.CompletedProcess[str]:
    """Run one command and report success only after its owned tree is empty."""

    if timeout_s <= 0:
        raise ValueError("owned_process_timeout_invalid")
    if not args:
        raise ValueError("owned_process_command_missing")
    if os.name == "nt":
        return _run_windows_owned_process(  # type: ignore[name-defined]
            args,
            input_text=input_text,
            timeout_s=timeout_s,
            env=env,
            active_check=active_check,
            poll_interval_s=poll_interval_s,
            cleanup_timeout_s=cleanup_timeout_s,
        )
    return _run_posix_owned_process(
        args,
        input_text=input_text,
        timeout_s=timeout_s,
        env=env,
        active_check=active_check,
        poll_interval_s=poll_interval_s,
        cleanup_timeout_s=cleanup_timeout_s,
    )


def _run_posix_owned_process(
    args: Sequence[str],
    *,
    input_text: str,
    timeout_s: float,
    env: Mapping[str, str] | None,
    active_check: Callable[[], None] | None,
    poll_interval_s: float,
    cleanup_timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        shell=False,
        close_fds=True,
        start_new_session=True,
    )
    controller = _PosixProcessGroup(process.pid)
    streams: _CapturedStreams | None = None
    pending_error: BaseException | None = None
    try:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise OSError("owned_process_pipe_setup_failed")
        streams = _CapturedStreams(
            process.stdin,
            process.stdout,
            process.stderr,
            input_text,
        )
        streams.start()
        deadline = time.monotonic() + timeout_s
        while not _posix_exited_without_reap(process.pid):
            if active_check is not None:
                active_check()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(list(args), timeout_s)
            time.sleep(min(max(0.01, poll_interval_s), 0.1))
    except BaseException as exc:
        pending_error = exc

    try:
        if pending_error is not None:
            controller.terminate(force=False)
            time.sleep(min(0.2, cleanup_timeout_s / 2))
        controller.terminate(force=True)
        process.wait(timeout=max(0.1, cleanup_timeout_s))
        controller.wait_empty(max(0.1, cleanup_timeout_s))
        if streams is not None:
            stdout, stderr = streams.finish(max(0.1, cleanup_timeout_s))
        else:
            _close_popen_streams(process)
            stdout, stderr = "", ""
    except (OSError, subprocess.SubprocessError) as cleanup_exc:
        if streams is not None:
            streams.close()
        else:
            _close_popen_streams(process)
        controller.close()
        raise OwnedProcessCleanupError("owned_process_cleanup_incomplete") from cleanup_exc
    finally:
        if streams is not None:
            streams.close()
        else:
            _close_popen_streams(process)
        controller.close()

    if pending_error is not None:
        raise pending_error
    return subprocess.CompletedProcess(
        list(args),
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _posix_exited_without_reap(pid: int) -> bool:
    status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    return status is not None


def _close_popen_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


if os.name == "nt":

    def _run_windows_owned_process(
        args: Sequence[str],
        *,
        input_text: str,
        timeout_s: float,
        env: Mapping[str, str] | None,
        active_check: Callable[[], None] | None,
        poll_interval_s: float,
        cleanup_timeout_s: float,
    ) -> subprocess.CompletedProcess[str]:
        kernel32 = _configure_kernel32()
        job = _WindowsJobObject(kernel32)
        process: _WindowsCreatedProcess | None = None
        streams: _CapturedStreams | None = None
        pending_error: BaseException | None = None
        try:
            process = _create_windows_process(kernel32, job, args, env)
            streams = _CapturedStreams(
                process.stdin,
                process.stdout,
                process.stderr,
                input_text,
            )
            streams.start()
            deadline = time.monotonic() + timeout_s
            try:
                while not process.exited():
                    if active_check is not None:
                        active_check()
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(list(args), timeout_s)
                    time.sleep(min(max(0.01, poll_interval_s), 0.1))
            except BaseException as exc:
                pending_error = exc

            job.terminate()
            process.wait(max(0.1, cleanup_timeout_s))
            job.wait_empty(max(0.1, cleanup_timeout_s))
            stdout, stderr = streams.finish(max(0.1, cleanup_timeout_s))
            returncode = process.returncode()
        except BaseException as exc:
            try:
                job.terminate()
                if process is not None:
                    process.wait(max(0.1, cleanup_timeout_s))
                job.wait_empty(max(0.1, cleanup_timeout_s))
                if streams is not None:
                    streams.finish(max(0.1, cleanup_timeout_s))
            except (OSError, subprocess.SubprocessError):
                if streams is not None:
                    streams.close()
                raise OwnedProcessCleanupError(
                    "owned_process_cleanup_incomplete"
                ) from exc
            raise
        finally:
            actions: list[Callable[[], None]] = []
            if streams is not None:
                actions.append(streams.close)
            if process is not None:
                actions.append(process.close)
            actions.append(job.close)
            _run_cleanup_actions(actions)

        if pending_error is not None:
            raise pending_error
        return subprocess.CompletedProcess(
            list(args),
            returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _create_windows_process(
        kernel32: ctypes.WinDLL,
        job: _WindowsJobObject,
        args: Sequence[str],
        env: Mapping[str, str] | None,
    ) -> _WindowsCreatedProcess:
        handles: list[wintypes.HANDLE] = []
        attribute_buffer: ctypes.Array[ctypes.c_char] | None = None
        attribute_list: ctypes.c_void_p | None = None
        attribute_initialized = False
        converted_streams: list[BinaryIO] = []
        process_info = _ProcessInformation()
        child_stdin = child_stdout = child_stderr = None
        parent_stdin = parent_stdout = parent_stderr = None
        try:
            child_stdin, parent_stdin = _create_pipe(kernel32, parent_end="write")
            parent_stdout, child_stdout = _create_pipe(kernel32, parent_end="read")
            parent_stderr, child_stderr = _create_pipe(kernel32, parent_end="read")
            handles.extend(
                [
                    child_stdin,
                    parent_stdin,
                    parent_stdout,
                    child_stdout,
                    parent_stderr,
                    child_stderr,
                ]
            )

            size = ctypes.c_size_t()
            kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            attribute_buffer = ctypes.create_string_buffer(size.value)
            attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
            if not kernel32.InitializeProcThreadAttributeList(
                attribute_list,
                2,
                0,
                ctypes.byref(size),
            ):
                raise OSError("owned_process_attribute_setup_failed")
            attribute_initialized = True

            child_handle_array = (wintypes.HANDLE * 3)(
                child_stdin,
                child_stdout,
                child_stderr,
            )
            job_handle_array = (wintypes.HANDLE * 1)(job.handle)
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(child_handle_array, ctypes.c_void_p),
                ctypes.sizeof(child_handle_array),
                None,
                None,
            ):
                raise OSError("owned_process_attribute_setup_failed")
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.cast(job_handle_array, ctypes.c_void_p),
                ctypes.sizeof(job_handle_array),
                None,
                None,
            ):
                raise OSError("owned_process_attribute_setup_failed")

            startup = _StartupInfoExW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = child_stdin
            startup.StartupInfo.hStdOutput = child_stdout
            startup.StartupInfo.hStdError = child_stderr
            startup.lpAttributeList = attribute_list
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
            environment_buffer = _windows_environment_buffer(env)
            created = kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                True,
                _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
                environment_buffer,
                None,
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_info),
            )
            if not created:
                raise OSError("owned_process_create_failed")
            _close_windows_handles(kernel32, [process_info.hThread])
            process_info.hThread = None

            child_handles = [child_stdin, child_stdout, child_stderr]
            _close_windows_handles(kernel32, child_handles)
            for handle in child_handles:
                handles.remove(handle)
            child_stdin = child_stdout = child_stderr = None

            stdin_stream = _windows_handle_stream(parent_stdin, "wb")
            converted_streams.append(stdin_stream)
            handles.remove(parent_stdin)
            parent_stdin = None
            stdout_stream = _windows_handle_stream(parent_stdout, "rb")
            converted_streams.append(stdout_stream)
            handles.remove(parent_stdout)
            parent_stdout = None
            stderr_stream = _windows_handle_stream(parent_stderr, "rb")
            converted_streams.append(stderr_stream)
            handles.remove(parent_stderr)
            parent_stderr = None
            return _WindowsCreatedProcess(
                kernel32,
                process_info.hProcess,
                stdin_stream,
                stdout_stream,
                stderr_stream,
            )
        except BaseException:
            cleanup_actions: list[Callable[[], None]] = [
                stream.close for stream in converted_streams
            ]
            if process_info.hProcess:
                def stop_created_process() -> None:
                    job.terminate()
                    status = kernel32.WaitForSingleObject(
                        process_info.hProcess,
                        2000,
                    )
                    if status != _WAIT_OBJECT_0:
                        raise OwnedProcessCleanupError(
                            "owned_process_wait_incomplete"
                        )

                cleanup_actions.append(stop_created_process)
            process_handles = [
                handle
                for handle in (process_info.hThread, process_info.hProcess)
                if handle
            ]
            if process_handles:
                cleanup_actions.append(
                    lambda: _close_windows_handles(kernel32, process_handles)
                )
            _run_cleanup_actions(cleanup_actions)
            raise
        finally:
            if attribute_initialized and attribute_list is not None:
                kernel32.DeleteProcThreadAttributeList(attribute_list)
            _close_windows_handles(kernel32, handles)

    def _create_pipe(
        kernel32: ctypes.WinDLL,
        *,
        parent_end: str,
    ) -> tuple[wintypes.HANDLE, wintypes.HANDLE]:
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes),
            None,
            True,
        )
        if not kernel32.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(attributes),
            0,
        ):
            raise OSError("owned_process_pipe_setup_failed")
        parent_handle = write_handle if parent_end == "write" else read_handle
        if not kernel32.SetHandleInformation(
            parent_handle,
            _HANDLE_FLAG_INHERIT,
            0,
        ):
            try:
                _close_windows_handles(kernel32, [read_handle, write_handle])
            except OwnedProcessCleanupError as exc:
                raise OwnedProcessCleanupError(
                    "owned_process_pipe_cleanup_incomplete"
                ) from exc
            raise OSError("owned_process_pipe_setup_failed")
        return read_handle, write_handle

    def _windows_handle_stream(handle: wintypes.HANDLE, mode: str) -> BinaryIO:
        flags = os.O_BINARY | (os.O_WRONLY if "w" in mode else os.O_RDONLY)
        handle_value = handle.value if hasattr(handle, "value") else handle
        file_descriptor = msvcrt.open_osfhandle(int(handle_value), flags)
        return os.fdopen(file_descriptor, mode, buffering=0)

    def _close_windows_handles(
        kernel32: ctypes.WinDLL,
        handles: Sequence[wintypes.HANDLE],
    ) -> None:
        pending = [handle for handle in handles if handle]
        for _ in range(2):
            failed: list[wintypes.HANDLE] = []
            for handle in pending:
                if kernel32.CloseHandle(handle):
                    continue
                if ctypes.get_last_error() == 6:
                    continue
                failed.append(handle)
            if not failed:
                return
            pending = failed
        raise OwnedProcessCleanupError("owned_process_handle_close_failed")

    def _windows_environment_buffer(
        env: Mapping[str, str] | None,
    ) -> ctypes.Array[ctypes.c_wchar] | None:
        if env is None:
            return None
        entries = [f"{key}={value}" for key, value in env.items()]
        entries.sort(key=str.upper)
        return ctypes.create_unicode_buffer("\0".join(entries) + "\0\0")
