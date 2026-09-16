"""Source-checkout entry point for external tool diagnostics."""

from apex_injector.tool_status import print_tool_status, validate_all_tools

__all__ = ["print_tool_status", "validate_all_tools"]

if __name__ == "__main__":
    print_tool_status()
