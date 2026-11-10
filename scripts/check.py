"""Run all adapter contracts with real SDK, no network and no skipped tests."""
from pathlib import Path
from types import ModuleType
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TEST_ENDPOINTS = set()


def network_guard(event, args):
    if event == 'socket.connect':
        if args[1] in LOCAL_TEST_ENDPOINTS:
            return
        # Windows asyncio's own socketpair needs a narrowly scoped loopback connection.
        caller = sys._getframe(1)
        address = args[1]
        if (caller.f_code.co_name == '_fallback_socketpair'
                and caller.f_globals.get('__name__') == 'socket'
                and caller.f_locals.get('csock') is args[0]
                and address == (caller.f_locals.get('addr'), caller.f_locals.get('port'))):
            return
        raise RuntimeError('External network disabled in adapter contracts')


class ResultGate:
    @pytest.fixture(autouse=True)
    def local_onebot_fixture(self, request):
        # Only the upstream synthetic WebSocket test's allocated loopback port.
        # Keep external sockets and all other loopback services inaccessible.
        endpoint = None
        if request.node.name == 'test_real_websocket_handshake_receive_send_and_disconnect':
            endpoint = ('127.0.0.1', request.getfixturevalue('unused_tcp_port'))
            LOCAL_TEST_ENDPOINTS.add(endpoint)
        try:
            yield
        finally:
            if endpoint is not None:
                LOCAL_TEST_ENDPOINTS.discard(endpoint)

    def __init__(self):
        self.collected = set()
        self.passed = set()
        self.skipped = False

    def pytest_collection_finish(self, session):
        self.collected = {item.nodeid for item in session.items}

    def pytest_runtest_logreport(self, report):
        self.skipped |= report.skipped
        if report.when == 'call' and report.passed:
            self.passed.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if not self.collected or self.skipped or self.passed != self.collected:
            session.exitstatus = 1


if __name__ == '__main__':
    import os
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    sys.addaudithook(network_guard)
    # Upstream test imports use an installation-directory name; preserve the tests.
    plugins = ModuleType('plugins')
    plugins.__path__ = []
    sys.modules['plugins'] = plugins
    package = ModuleType('plugins.MaiBot-SnowLuma-Adapter-main')
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    setattr(plugins, 'MaiBot-SnowLuma-Adapter-main', package)
    raise SystemExit(pytest.main([
        '-p', 'pytest_asyncio.plugin', '-o', 'asyncio_mode=auto', str(ROOT / 'tests'), '-q',
    ], plugins=[ResultGate()]))
