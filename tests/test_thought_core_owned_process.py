import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

import thought_core.owned_process as owned_process  # noqa: E402
from thought_core.owned_process import (  # noqa: E402
    OwnedProcessCleanupError,
    run_owned_process,
)


class ThoughtCoreOwnedProcessTests(TestCase):
    def test_normal_completion_preserves_result(self) -> None:
        result = run_owned_process(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.stdin.read().upper(), end='')",
            ],
            input_text="owned child",
            timeout_s=3.0,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "OWNED CHILD")
        self.assertEqual(result.stderr, "")

    def test_timeout_stops_descendant_before_late_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="owned-process-test-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(30)"
            )

            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned_process(
                    [
                        sys.executable,
                        "-c",
                        parent_code,
                        child_code,
                        str(marker),
                    ],
                    input_text="",
                    timeout_s=0.3,
                )

            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_owned_tree_cleanup_does_not_stop_foreign_sentinel(self) -> None:
        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned_process(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    input_text="",
                    timeout_s=0.2,
                )
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=3.0)

    def test_stream_start_failure_stops_owned_tree_only(self) -> None:
        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="owned-process-test-"
            ) as temp_dir:
                marker = Path(temp_dir) / "must-not-exist.txt"
                code = (
                    "import pathlib,sys,time; time.sleep(1.0); "
                    "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
                )
                with patch.object(
                    owned_process._CapturedStreams,
                    "start",
                    side_effect=RuntimeError("stream_start_failed"),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "stream_start_failed",
                    ):
                        run_owned_process(
                            [sys.executable, "-c", code, str(marker)],
                            input_text="",
                            timeout_s=3.0,
                        )
                time.sleep(1.2)
                self.assertFalse(marker.exists())
                self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=3.0)

    def test_partial_stream_start_failure_joins_started_thread(self) -> None:
        original_start = threading.Thread.start
        start_calls = {"count": 0}
        actually_started: list[threading.Thread] = []

        def fail_second_start(thread):  # type: ignore[no-untyped-def]
            start_calls["count"] += 1
            if start_calls["count"] == 2:
                raise RuntimeError("partial_stream_start_failed")
            original_start(thread)
            actually_started.append(thread)

        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with patch.object(
                threading.Thread,
                "start",
                autospec=True,
                side_effect=fail_second_start,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "partial_stream_start_failed",
                ):
                    run_owned_process(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        input_text="",
                        timeout_s=3.0,
                    )
            self.assertTrue(actually_started)
            self.assertTrue(all(not thread.is_alive() for thread in actually_started))
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=3.0)

    def test_cleanup_actions_retry_failure_without_skipping_later_owner(self) -> None:
        events: list[str] = []
        process_attempts = {"count": 0}

        def process_close() -> None:
            events.append("process")
            process_attempts["count"] += 1
            if process_attempts["count"] == 1:
                raise OwnedProcessCleanupError("fixed_close_failure")

        def job_close() -> None:
            events.append("job")

        owned_process._run_cleanup_actions([process_close, job_close])

        self.assertEqual(events, ["process", "job", "process"])

    @skipUnless(os.name == "nt", "Windows Job Object contract")
    def test_windows_creation_failure_cannot_run_user_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="owned-process-test-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist.txt"
            code = (
                "import pathlib,sys; "
                "pathlib.Path(sys.argv[1]).write_text('ran', encoding='utf-8')"
            )
            with patch(
                "thought_core.owned_process._create_windows_process",
                side_effect=OSError("fixed_setup_failure"),
            ):
                with self.assertRaises(OSError):
                    run_owned_process(
                        [sys.executable, "-c", code, str(marker)],
                        input_text="",
                        timeout_s=1.0,
                    )
            self.assertFalse(marker.exists())

    @skipUnless(os.name == "nt", "Windows Job Object contract")
    def test_windows_requires_job_empty_proof(self) -> None:
        original_wait_empty = owned_process._WindowsJobObject.wait_empty
        calls = {"count": 0}

        def record_wait_empty(job, timeout_s):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            return original_wait_empty(job, timeout_s)

        with patch.object(
            owned_process._WindowsJobObject,
            "wait_empty",
            autospec=True,
            side_effect=record_wait_empty,
        ):
            result = run_owned_process(
                [sys.executable, "-c", "print('ok')"],
                input_text="",
                timeout_s=3.0,
            )

        self.assertEqual(result.stdout.strip(), "ok")
        self.assertEqual(calls["count"], 1)

    @skipUnless(os.name == "nt", "Windows Job Object contract")
    def test_windows_job_close_is_idempotent(self) -> None:
        job = owned_process._WindowsJobObject(owned_process._configure_kernel32())
        job.close()
        job.close()

    @skipUnless(os.name == "nt", "Windows handle cleanup contract")
    def test_windows_thread_and_child_handles_use_all_attempted_close(self) -> None:
        original_close = owned_process._close_windows_handles
        close_group_sizes: list[int] = []

        def record_close(kernel32, handles):  # type: ignore[no-untyped-def]
            close_group_sizes.append(len(handles))
            return original_close(kernel32, handles)

        with patch(
            "thought_core.owned_process._close_windows_handles",
            side_effect=record_close,
        ):
            result = run_owned_process(
                [sys.executable, "-c", "print('ok')"],
                input_text="",
                timeout_s=3.0,
            )

        self.assertEqual(result.stdout.strip(), "ok")
        self.assertIn(1, close_group_sizes)
        self.assertIn(3, close_group_sizes)

    @skipUnless(os.name == "nt", "Windows exact inheritance contract")
    def test_windows_creation_allowlists_only_three_std_handles_and_private_job(
        self,
    ) -> None:
        real_kernel32 = owned_process._configure_kernel32()
        captured: dict[int, list[int]] = {}

        class KernelProxy:
            def __getattr__(self, name):  # type: ignore[no-untyped-def]
                return getattr(real_kernel32, name)

            def UpdateProcThreadAttribute(  # noqa: N802
                self,
                attribute_list,
                flags,
                attribute,
                value,
                size,
                previous,
                return_size,
            ):
                count = size // owned_process.ctypes.sizeof(
                    owned_process.wintypes.HANDLE
                )
                array_type = owned_process.wintypes.HANDLE * count
                values = owned_process.ctypes.cast(
                    value,
                    owned_process.ctypes.POINTER(array_type),
                ).contents
                captured[int(attribute)] = [
                    int(item) if isinstance(item, int) else int(item.value)
                    for item in values
                ]
                return real_kernel32.UpdateProcThreadAttribute(
                    attribute_list,
                    flags,
                    attribute,
                    value,
                    size,
                    previous,
                    return_size,
                )

        foreign_read, foreign_write = os.pipe()
        try:
            foreign_handle = owned_process.msvcrt.get_osfhandle(foreign_write)
            os.set_inheritable(foreign_write, True)
            with patch(
                "thought_core.owned_process._configure_kernel32",
                return_value=KernelProxy(),
            ):
                result = run_owned_process(
                    [sys.executable, "-c", "print('ok')"],
                    input_text="",
                    timeout_s=3.0,
                )
        finally:
            os.close(foreign_read)
            os.close(foreign_write)

        std_handles = captured[owned_process._PROC_THREAD_ATTRIBUTE_HANDLE_LIST]
        job_handles = captured[owned_process._PROC_THREAD_ATTRIBUTE_JOB_LIST]
        self.assertEqual(len(std_handles), 3)
        self.assertEqual(len(set(std_handles)), 3)
        self.assertNotIn(foreign_handle, std_handles)
        self.assertEqual(len(job_handles), 1)
        self.assertNotIn(job_handles[0], std_handles)
        self.assertNotEqual(job_handles[0], foreign_handle)
        self.assertEqual(result.stdout.strip(), "ok")

    @skipUnless(os.name == "nt", "Windows pipe cleanup contract")
    def test_windows_pipe_setup_failure_retries_both_handles(self) -> None:
        class FakeKernel32:
            def __init__(self) -> None:
                self.close_calls: list[int] = []
                self.close_counts: dict[int, int] = {}

            def CreatePipe(self, read_ptr, write_ptr, attributes, size):  # noqa: N802
                owned_process.ctypes.cast(
                    read_ptr,
                    owned_process.ctypes.POINTER(owned_process.wintypes.HANDLE),
                ).contents.value = 101
                owned_process.ctypes.cast(
                    write_ptr,
                    owned_process.ctypes.POINTER(owned_process.wintypes.HANDLE),
                ).contents.value = 202
                return True

            def SetHandleInformation(self, handle, mask, flags):  # noqa: N802
                return False

            def CloseHandle(self, handle):  # noqa: N802
                value = handle.value if hasattr(handle, "value") else int(handle)
                self.close_calls.append(value)
                self.close_counts[value] = self.close_counts.get(value, 0) + 1
                if self.close_counts[value] == 1:
                    owned_process.ctypes.set_last_error(5)
                    return False
                return True

        kernel32 = FakeKernel32()
        with self.assertRaisesRegex(OSError, "owned_process_pipe_setup_failed"):
            owned_process._create_pipe(kernel32, parent_end="read")

        self.assertEqual(kernel32.close_calls, [101, 202, 101, 202])

    @skipUnless(os.name != "nt", "POSIX process-group contract")
    def test_posix_ignored_sigterm_is_force_killed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="owned-process-test-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist.txt"
            child_code = (
                "import pathlib,signal,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL); time.sleep(30)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned_process(
                    [
                        sys.executable,
                        "-c",
                        parent_code,
                        child_code,
                        str(marker),
                    ],
                    input_text="",
                    timeout_s=0.3,
                )
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_early_parent_exit_still_stops_detached_output_descendant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="owned-process-test-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL)"
            )

            result = run_owned_process(
                [
                    sys.executable,
                    "-c",
                    parent_code,
                    child_code,
                    str(marker),
                ],
                input_text="",
                timeout_s=3.0,
            )

            self.assertEqual(result.returncode, 0)
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_active_check_cancellation_stops_descendant(self) -> None:
        checks = {"count": 0}

        def cancel_after_start() -> None:
            checks["count"] += 1
            if checks["count"] >= 3:
                raise RuntimeError("route_cancelled")

        with tempfile.TemporaryDirectory(prefix="owned-process-test-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist.txt"
            code = (
                "import pathlib,sys,time; time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
            )
            with self.assertRaisesRegex(RuntimeError, "route_cancelled"):
                run_owned_process(
                    [sys.executable, "-c", code, str(marker)],
                    input_text="",
                    timeout_s=3.0,
                    active_check=cancel_after_start,
                    poll_interval_s=0.05,
                )

            time.sleep(1.2)
            self.assertFalse(marker.exists())
