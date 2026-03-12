import unittest


class TestToolSelection(unittest.TestCase):
    def test_shortlist_prefers_filesystem_cluster_for_code_search(self):
        from core.tool_defs import build_tool_definitions, shortlist_tool_definitions

        tools = build_tool_definitions(enabled_skills=["browser"])
        shortlisted = shortlist_tool_definitions(
            tools, "find verify_auth in the codebase and read the file"
        )
        names = {tool["function"]["name"] for tool in shortlisted}

        self.assertIn("search_files", names)
        self.assertIn("read_file", names)
        self.assertIn("list_dir", names)
        self.assertNotIn("google_search", names)

    def test_shortlist_prefers_browser_cluster_for_urls(self):
        from core.tool_defs import build_tool_definitions, shortlist_tool_definitions

        tools = build_tool_definitions(enabled_skills=["browser"])
        shortlisted = shortlist_tool_definitions(
            tools, "open https://example.com and inspect the page"
        )
        names = {tool["function"]["name"] for tool in shortlisted}

        self.assertIn("browser_navigate", names)
        self.assertIn("browser_snapshot", names)
        self.assertIn("browser_click", names)
        self.assertNotIn("read_file", names)

    def test_agent_normalizes_common_tool_aliases(self):
        from core.loop import AgentLoop
        from core.metrics import MetricsCollector

        agent = object.__new__(AgentLoop)
        agent.metrics = MetricsCollector()
        agent._filesystem_alias_actions = {
            "list": "list_dir",
            "read": "read_file",
            "write": "write_file",
            "delete": "delete_file",
            "find": "search_files",
            "search": "search_files",
        }
        agent._tool_name_aliases = {
            "ls": "list_dir",
            "dir": "list_dir",
            "list_files": "list_dir",
            "cat": "read_file",
            "open_file": "read_file",
            "show_file": "read_file",
            "grep": "search_files",
            "rg": "search_files",
            "ripgrep": "search_files",
            "find_files": "search_files",
            "shell": "run_command",
            "terminal": "run_command",
            "exec": "run_command",
            "bash": "run_command",
            "powershell": "run_command",
            "cmd": "run_command",
        }

        name, args = agent._normalize_tool_alias("grep", {"pattern": "TODO"}, "web:test")
        self.assertEqual(name, "search_files")
        self.assertEqual(args["query"], "TODO")

        name, args = agent._normalize_tool_alias("cat", {"file": "README.md"}, "web:test")
        self.assertEqual(name, "read_file")
        self.assertEqual(args["path"], "README.md")

        name, args = agent._normalize_tool_alias(
            "powershell", {"script": "git status"}, "web:test"
        )
        self.assertEqual(name, "run_command")
        self.assertEqual(args["command"], "git status")
