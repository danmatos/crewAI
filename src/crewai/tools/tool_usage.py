from typing import Any, List

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langchain_core.tools import BaseTool

from crewai.agents.tools_handler import ToolsHandler
from crewai.telemtry import Telemetry
from crewai.tools.tool_calling import ToolCalling
from crewai.utilities import I18N, Printer


class ToolUsage:
    """
    Class that represents the usage of a tool by an agent.

    Attributes:
        tools_handler: Tools handler that will manage the tool usage.
        tools: List of tools available for the agent.
        llm: Language model to be used for the tool usage.
    """

    def __init__(
        self, tools_handler: ToolsHandler, tools: List[BaseTool], llm: Any
    ) -> None:
        self._i18n: I18N = I18N()
        self._printer: Printer = Printer()
        self._telemetry: Telemetry = Telemetry()
        self._run_attempts: int = 1
        self._max_parsing_attempts: int = 3
        self.tools_handler = tools_handler
        self.tools = tools
        self.llm = llm

    def use(self, tool_string: str):
        calling = self._tool_calling(tool_string)
        tool = self._select_tool(calling.function_name)
        return self._use(tool=tool, calling=calling)

    def _use(self, tool: BaseTool, calling: ToolCalling) -> None:
        if self._check_tool_repeated_usage(calling=calling):
            result = self._i18n.errors("task_repeated_usage").format(
                tool=calling.function_name, tool_input=calling.arguments
            )
        else:
            self.tools_handler.on_tool_start(calling=calling)

            result = self.tools_handler.cache.read(
                tool=calling.function_name, input=calling.arguments
            )

            if not result:
                result = tool._run(**calling.arguments)
                self.tools_handler.on_tool_end(calling=calling, output=result)

        self._printer.print(content=f"\n\n{result}\n", color="yellow")
        self._telemetry.tool_usage(
            llm=self.llm, tool_name=tool.name, attempts=self._run_attempts
        )
        return result

    def _check_tool_repeated_usage(self, calling: ToolCalling) -> None:
        if last_tool_usage := self.tools_handler.last_used_tool:
            return calling == last_tool_usage

    def _select_tool(self, tool_name: str) -> BaseTool:
        for tool in self.tools:
            if tool.name == tool_name:
                return tool
        raise Exception(f"Tool '{tool_name}' not found.")

    def _render(self) -> str:
        """Render the tool name and description in plain text."""
        descriptions = []
        for tool in self.tools:
            args = {
                k: {k2: v2 for k2, v2 in v.items() if k2 in ["description", "type"]}
                for k, v in tool.args.items()
            }
            descriptions.append(
                "\n".join(
                    [
                        f"Funtion Name: {tool.name}",
                        f"Funtion attributes: {args}",
                        f"Description: {tool.description}",
                    ]
                )
            )
        return "\n--\n".join(descriptions)

    def _tool_calling(self, tool_string: str) -> ToolCalling:
        try:
            parser = PydanticOutputParser(pydantic_object=ToolCalling)
            prompt = PromptTemplate(
                template="Return a valid schema for the one tool you must use with its arguments and values.\n\nTools available:\n\n{available_tools}\n\nUse this text to inform a valid ouput schema:\n{tool_string}\n\n{format_instructions}\n```",
                input_variables=["tool_string"],
                partial_variables={
                    "available_tools": self._render(),
                    "format_instructions": parser.get_format_instructions(),
                },
            )
            chain = prompt | self.llm | parser
            calling = chain.invoke({"tool_string": tool_string})

        except Exception as e:
            self._run_attempts += 1
            if self._run_attempts > self._max_parsing_attempts:
                self._telemetry.tool_usage_error(llm=self.llm)
                raise e
            return self._tool_calling(tool_string)

        return calling