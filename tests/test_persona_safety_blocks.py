import unittest
from pathlib import Path
from types import SimpleNamespace
from core.bus import MessageBus
from core.tools import Toolbox
from core.paths import PERSONA_DIR

class TestPersonaSafetyBlocks(unittest.IsolatedAsyncioTestCase):
    async def test_write_file_blocks_persona_dir(self):
        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        bus = MessageBus()
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

        # Attempt to write to a path inside persona/
        test_path = PERSONA_DIR / "test_blocked_write.md"
        result = await toolbox.write_file(str(test_path), "should fail")

        self.assertIn("Direct modification of state-managed files under 'persona/' is blocked", result)
        self.assertFalse(test_path.exists())

    async def test_delete_file_blocks_persona_dir(self):
        config = SimpleNamespace(skills=SimpleNamespace(enabled=[]))
        bus = MessageBus()
        toolbox = Toolbox(allowed_paths=[str(Path.cwd())], bus=bus, config=config)

        # Attempt to delete a path inside persona/
        test_path = PERSONA_DIR / "test_blocked_delete.md"
        result = await toolbox.delete_file(str(test_path))

        self.assertIn("Direct deletion of state-managed files under 'persona/' is blocked", result)
