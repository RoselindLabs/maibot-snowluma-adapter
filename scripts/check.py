"""Run all adapter contracts with real SDK, no network and no skipped tests."""
from pathlib import Path
from types import ModuleType
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


def network_guard(event, args):
    if event == 'socket.connect':
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
    raise SystemExit(pytest.main([
        '-p', 'pytest_asyncio.plugin', '-o', 'asyncio_mode=auto', str(ROOT / 'tests'), '-q',
    ], plugins=[ResultGate()]))
