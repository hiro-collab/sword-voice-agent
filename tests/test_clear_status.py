from contextlib import contextmanager
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.apps.clear_status import run


class ClearStatusTest(TestCase):
    def test_refuses_without_confirmation(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.append_event("test", source="test", payload={})

            with redirect_stdout(io.StringIO()):
                code = run(SimpleNamespace(status_dir=tmp, yes=False))

            self.assertEqual(code, 2)
            self.assertTrue(store.events_path.exists())

    def test_clears_status_files_with_confirmation(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.append_event("test", source="test", payload={})

            with redirect_stdout(io.StringIO()):
                code = run(SimpleNamespace(status_dir=tmp, yes=True))

            self.assertEqual(code, 0)
            self.assertFalse(store.events_path.exists())


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
