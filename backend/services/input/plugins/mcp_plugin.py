"""MCP Input Plugin — Model Context Protocol integration.

This plugin implements the MCP server side: external MCP tools can
submit requests via JSON-RPC that become DebateInput artifacts.

MCP Protocol: https://spec.modelcontextprotocol.io
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from backend.models.debate_input import DebateInput, InputAttachment
from backend.services.input.base import InputPlugin
from backend.services.input.registry import register_input_plugin

logger = logging.getLogger(__name__)


class MCPPluginConfig(BaseModel):
    """Configuration schema for the MCP Input plugin."""

    server_name: str = Field(description="MCP server name")
    tool_name: str = Field(description="MCP tool name to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    mcp_server_url: str | None = Field(default=None, description="MCP server URL (for client mode)")


class MCPToolCallRequest(BaseModel):
    """MCP tools/call request body (JSON-RPC style)."""

    tool_name: str = Field(..., description="Name of the tool to call")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Request ID for correlation")


class MCPToolCallResponse(BaseModel):
    """MCP tools/call response body."""

    request_id: str
    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = False


@register_input_plugin
class MCPInputPlugin(InputPlugin):
    """MCP Input plugin — receives tool calls from external MCP clients.

    Danwa acts as an MCP Server. External MCP tools send requests
    that become DebateInput artifacts for the debate workflow.
    """

    plugin_key: ClassVar[str] = "mcp"
    plugin_name: ClassVar[str] = "MCP Tool Input"
    config_schema: ClassVar[type[BaseModel]] = MCPPluginConfig

    async def capture(self, config: BaseModel) -> DebateInput:
        """Capture an MCP tool call as a DebateInput.

        The config must contain the tool_name and arguments from the
        external MCP client. The tool's output is mapped to a
        DebateInput topic and attachments.
        """
        if not isinstance(config, MCPPluginConfig):
            raise TypeError(f"Expected MCPPluginConfig, got {type(config)}")

        # Build the debate topic from the tool call
        topic = self._build_topic(config)

        # Build attachments from the arguments
        attachments = self._build_attachments(config)

        # Create the DebateInput
        debate_input = DebateInput(
            source_plugin_key=self.plugin_key,
            source_metadata={
                "server_name": config.server_name,
                "tool_name": config.tool_name,
                "arguments": config.arguments,
            },
            topic=topic,
            attachments=attachments,
            context_overrides={
                "mcp_server_url": config.mcp_server_url,
            },
        )

        logger.info(
            "MCP input captured: server=%s tool=%s request_hash=%s",
            config.server_name,
            config.tool_name,
            debate_input.input_hash[:12],
        )

        return debate_input

    async def validate(self, config: BaseModel) -> bool:
        """Check if the MCP plugin is operational.

        Returns True if the config is valid and the server_name/tool_name
        are provided.
        """
        if not isinstance(config, MCPPluginConfig):
            return False
        return bool(config.server_name and config.tool_name)

    def get_ui_hints(self) -> dict:
        """Return frontend metadata for this plugin."""
        return {
            "requires_microphone": False,
            "supports_streaming": False,
            "is_available": True,
            "coming_soon": False,
        }

    def _build_topic(self, config: MCPPluginConfig) -> str:
        """Build a debate topic from the MCP tool call."""
        tool_name = config.tool_name
        args_summary = json.dumps(config.arguments, default=str, ensure_ascii=False)
        if len(args_summary) > 500:
            args_summary = args_summary[:500] + "..."
        return f"MCP Tool Call: {tool_name}\n\nArguments:\n{args_summary}"

    def _build_attachments(self, config: MCPPluginConfig) -> list[InputAttachment]:
        """Build attachments from the MCP tool arguments."""
        attachments = []

        # If there's a 'content' or 'text' argument, use it as an attachment
        content = config.arguments.get("content") or config.arguments.get("text")
        if content and isinstance(content, str):
            attachments.append(
                InputAttachment(
                    mime_type="text/plain",
                    content_ref=content[:10000],  # Inline content, capped
                    description=f"Content from MCP tool {config.tool_name}",
                )
            )

        # If there's a 'file_path' or 'url' argument, reference it
        file_path = config.arguments.get("file_path") or config.arguments.get("url")
        if file_path and isinstance(file_path, str):
            attachments.append(
                InputAttachment(
                    mime_type="application/octet-stream",
                    content_ref=file_path,
                    description=f"File reference from MCP tool {config.tool_name}",
                )
            )

        return attachments

    @staticmethod
    def process_tool_call(request: MCPToolCallRequest) -> MCPToolCallResponse:
        """Process an MCP tool call request and return a response.

        This is a synchronous helper for the API endpoint.
        """
        try:
            # Build content from the request
            content = [
                {
                    "type": "text",
                    "text": f"Tool {request.tool_name} called with arguments: {json.dumps(request.arguments, default=str)}",
                }
            ]

            return MCPToolCallResponse(
                request_id=request.request_id,
                content=content,
                is_error=False,
            )
        except Exception as exc:
            logger.error("MCP tool call failed: %s", exc)
            return MCPToolCallResponse(
                request_id=request.request_id,
                content=[{"type": "text", "text": f"Error: {exc}"}],
                is_error=True,
            )
