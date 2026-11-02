"""Bounded MCP protocol ingress; never serialize validation exceptions or raw inputs."""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager

import anyio
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage


def validated_message(body: bytes):
    if len(body) > 131072:
        raise ValueError("Message too large")
    message = types.JSONRPCMessage.model_validate_json(body)
    root = message.root
    if isinstance(root, types.JSONRPCRequest):
        types.ClientRequest.model_validate(root.model_dump(by_alias=True, exclude_none=True))
    elif isinstance(root, types.JSONRPCNotification):
        types.ClientNotification.model_validate(root.model_dump(by_alias=True, exclude_none=True))
    return message


@asynccontextmanager
async def safe_stdio_server():
    """Use official SDK message streams with bounded reads and constant error messages."""
    inbound_tx, inbound = anyio.create_memory_object_stream[SessionMessage](0)
    outbound, outbound_rx = anyio.create_memory_object_stream[SessionMessage](0)
    output_lock = anyio.Lock()
    silence_sdk_input_logs()

    async def emit(value):
        line = (json.dumps(value, ensure_ascii=False) + "\n").encode()
        async with output_lock:
            await anyio.to_thread.run_sync(sys.stdout.buffer.write, line)
            await anyio.to_thread.run_sync(sys.stdout.buffer.flush)

    async def read():
        async with inbound_tx:
            while True:
                line = await anyio.to_thread.run_sync(sys.stdin.buffer.readline, 131073)
                if not line:
                    break
                if len(line) > 131072 and not line.endswith(b"\n"):
                    while True:
                        tail = await anyio.to_thread.run_sync(sys.stdin.buffer.readline, 131073)
                        if not tail or tail.endswith(b"\n"):
                            break
                    await emit(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "Invalid MCP message"},
                        }
                    )
                    continue
                try:
                    message = validated_message(line)
                except Exception:
                    await emit(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "Invalid MCP message"},
                        }
                    )
                    continue
                await inbound_tx.send(SessionMessage(message))

    async def write():
        async with outbound_rx:
            async for message in outbound_rx:
                await emit(message.message.model_dump(by_alias=True, exclude_none=True))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(read)
        tasks.start_soon(write)
        try:
            yield inbound, outbound
        finally:
            tasks.cancel_scope.cancel()


def silence_sdk_input_logs():
    # SDK debug/error messages can contain complete protocol messages and validation input.
    # Keep upstream diagnostics off; application errors use constant, safe categories.
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("mcp."):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)


class SafeFastMCP(FastMCP):
    def _setup_handlers(self):
        super()._setup_handlers()
        # Hindsight exposes tools only. Do not inherit input-echoing prompt/resource handlers.
        allowed = {types.PingRequest, types.ListToolsRequest, types.CallToolRequest}
        for request in list(self._mcp_server.request_handlers):
            if request not in allowed:
                del self._mcp_server.request_handlers[request]

    async def call_tool(self, name, arguments):
        try:
            return await super().call_tool(name, arguments)
        except Exception:
            return types.CallToolResult(
                isError=True,
                content=[
                    types.TextContent(
                        type="text",
                        text="Tool request failed. Check arguments, authorization, and revision.",
                    )
                ],
            )

    async def run_stdio_async(self):
        async with safe_stdio_server() as (read, write):
            await self._mcp_server.run(
                read, write, self._mcp_server.create_initialization_options()
            )
