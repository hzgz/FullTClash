import asyncio
import socket
import sys

import async_timeout

from ..._types import ProxyType
from ..._helpers import parse_proxy_url
from ..._errors import ProxyConnectionError, ProxyTimeoutError, ProxyError
from ._stream import AsyncioSocketStream
from ._resolver import Resolver

from ..._protocols.errors import ReplyError
from ..._connectors.factory_async import create_connector

from ._connect import connect_tcp
from ... import _abc as abc

DEFAULT_TIMEOUT = 60


class AsyncioProxy(abc.AsyncProxy):
    def __init__(
        self,
        proxy_type: ProxyType,
        host: str,
        port: int,
        username: str = None,
        password: str = None,
        rdns: bool = None,
        loop: asyncio.AbstractEventLoop = None,
    ):
        if loop is None:
            loop = asyncio.get_event_loop()

        self._loop = loop

        self._proxy_type = proxy_type
        self._proxy_host = host
        self._proxy_port = port
        self._password = password
        self._username = username
        self._rdns = rdns

        self._resolver = Resolver(loop=loop)

    async def connect(
        self,
        dest_host: str,
        dest_port: int,
        timeout: float = None,
        _socket=None,
    ) -> socket.socket:
        if timeout is None:
            timeout = DEFAULT_TIMEOUT

        try:
            async with async_timeout.timeout(timeout):
                return await self._connect(
                    dest_host=dest_host,
                    dest_port=dest_port,
                    _socket=_socket,
                )
        except asyncio.TimeoutError as e:
            raise ProxyTimeoutError(f'Proxy connection timed out: {timeout}') from e

    async def _connect(self, dest_host, dest_port, _socket=None) -> socket.socket:
        if _socket is None:
            try:
                _socket = await connect_tcp(
                    host=self._proxy_host,
                    port=self._proxy_port,
                    loop=self._loop,
                )
            except OSError as e:
                msg = 'Could not connect to proxy {}:{} [{}]'.format(
                    self._proxy_host,
                    self._proxy_port,
                    e.strerror,
                )
                raise ProxyConnectionError(e.errno, msg) from e

        stream = AsyncioSocketStream(sock=_socket, loop=self._loop)

        try:
            connector = create_connector(
                proxy_type=self._proxy_type,
                username=self._username,
                password=self._password,
                rdns=self._rdns,
                resolver=self._resolver,
            )
            await connector.connect(
                stream=stream,
                host=dest_host,
                port=dest_port,
            )

            return _socket
        except asyncio.CancelledError:  # pragma: no cover
            # https://bugs.python.org/issue30064
            # https://bugs.python.org/issue34795
            if self._can_be_closed_safely():
                await stream.close()
            raise
        except ReplyError as e:
            await stream.close()
            raise ProxyError(e, error_code=e.error_code)
        except Exception:  # pragma: no cover
            await stream.close()
            raise

    def _can_be_closed_safely(self):  # pragma: no cover
        def is_proactor_event_loop():
            try:
                from asyncio import ProactorEventLoop  # noqa
            except ImportError:
                return False
            return isinstance(self._loop, ProactorEventLoop)

        def is_uvloop_event_loop():
            try:
                from uvloop import Loop  # noqa
            except ImportError:
                return False
            return isinstance(self._loop, Loop)

        return sys.version_info[:2] >= (3, 8) or is_proactor_event_loop() or is_uvloop_event_loop()

    @property
    def proxy_host(self):
        return self._proxy_host

    @property
    def proxy_port(self):
        return self._proxy_port

    @classmethod
    def create(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    @classmethod
    def from_url(cls, url: str, **kwargs):
        url_args = parse_proxy_url(url)
        return cls(*url_args, **kwargs)
