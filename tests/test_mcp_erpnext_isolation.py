import os
import ast
import unittest

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
mcp_erpnext_path = os.path.join(root_path, "mcp_erpnext")

FORBIDDEN_ROOTS = {"agent", "tools", "workflow", "ai_assistant", "main", "dotenv"}


class TestMCPErpnextIsolation(unittest.TestCase):
    """
    Verifies that the standalone mcp_erpnext package has zero upward dependencies.
    mcp_erpnext never loads .env (load_dotenv is forbidden).
    """

    def test_mcp_erpnext_modules_have_no_forbidden_imports(self):
        """
        Parse every .py file under mcp_erpnext/ with ast and ensure no import
        targets agent, tools, workflow, ai_assistant, main, or dotenv (load_dotenv).
        """
        py_files = [
            os.path.join(mcp_erpnext_path, f)
            for f in os.listdir(mcp_erpnext_path)
            if f.endswith(".py")
        ]
        self.assertTrue(len(py_files) >= 4, "Expected at least 4 python files in mcp_erpnext")

        violations = []
        for file_path in py_files:
            file_name = os.path.basename(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=file_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        if top_pkg in FORBIDDEN_ROOTS:
                            violations.append((file_name, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_pkg = node.module.split(".")[0]
                        if top_pkg in FORBIDDEN_ROOTS:
                            violations.append((file_name, node.lineno, node.module))

        self.assertEqual(
            violations,
            [],
            f"Found forbidden imports in mcp_erpnext targeting disallowed packages: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
