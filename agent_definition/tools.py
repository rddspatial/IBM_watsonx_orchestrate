# test_tool.py

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission


@tool(
    name="my_tool",
    description="Greets the user with the provided input.",
    permission=ToolPermission.READ_ONLY
)
def my_tool(input: str) -> str:
    """
    Executes the tool's action based on the provided input.

    Args:
        input (str): The tool's input.

    Returns:
        str: The tool's output.
    """

    return f"Hello, {input}"
