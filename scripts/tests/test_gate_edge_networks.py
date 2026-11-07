"""Every compose stack the release checks run side by side needs its own edge network range.

compose.yaml gives the edge network a fixed subnet so the front doors keep fixed addresses, and Docker refuses a
second network on a range already in use ("Pool overlaps with other one on this address space"). The journey and
smoke run at the same time in the release gate, and either may run on a computer with a real install on the default.
"""

import ipaddress
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

from scripts import release_checks

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('TICO_EDGE_SUBNET', 'TICO_EDGE_DYNAMIC_RANGE', 'TICO_CADDY_ADDRESS', 'TICO_CLOUDFLARED_ADDRESS')
OVERLAP = 'Error response from daemon: invalid pool request: Pool overlaps with other one on this address space'


def test_each_stack_writes_its_own_edge_range_from_the_generator():
    compose = (ROOT / 'compose.yaml').read_text()
    assert all(f'${{{name}:-' in compose for name in NAMES)  # the generator writes what compose.yaml reads
    default = ipaddress.ip_network(re.search(r'subnet: \$\{TICO_EDGE_SUBNET:-([0-9./]+)\}', compose).group(1))
    taken = {'compose.yaml default': default}
    for name, prefix in release_checks.EDGE.items():
        edge = release_checks.edge_settings(prefix)  # the real docker/edge-env.sh output
        assert sorted(edge) == sorted(NAMES), name
        subnet, dynamic = (ipaddress.ip_network(edge[key]) for key in NAMES[:2])
        assert dynamic.subnet_of(subnet), name
        for key in NAMES[2:]:
            assert ipaddress.ip_address(edge[key]) in subnet and ipaddress.ip_address(edge[key]) not in dynamic, name
        for other, range_ in taken.items():
            assert not subnet.overlaps(range_), f'{name} edge range overlaps {other}'
        taken[name] = subnet
    # Each stack appends that whole output to the env file its compose project reads; the gate passes its range.
    for path, variable, name, env_file in (
        ('docker/smoke.sh', 'TICO_SMOKE_EDGE', 'smoke', '"$env_file"'),
        ('scripts/journey-test.sh', 'TICO_JOURNEY_EDGE', 'journey', '"$DIR/.env"'),
        ('docker/backup-test.sh', 'TICO_BACKUP_EDGE', 'backup-test', '"$work/env"'),
    ):
        writes = f'docker/edge-env.sh "${{{variable}:-{release_checks.EDGE[name]}}}" >> {env_file}'
        lines = [line.strip() for line in (ROOT / path).read_text().splitlines()]
        assert any(line.startswith(writes) for line in lines), path  # a live command, not a comment
    assert subprocess.run(['bash', 'docker/edge-env.sh', '172.30'], cwd=ROOT, capture_output=True).returncode == 2


class FakeDocker:
    """Docker's network IPAM as the gate meets it: a create on a range that overlaps a held one is refused."""

    PIPE = STDOUT = None

    def __init__(self, held=(), refuses_overlap=True):
        self.networks = dict(held)  # name -> subnet
        self.refuses_overlap, self.in_flight, self.most_at_once = refuses_overlap, 0, 0

    def run(self, command, **kwargs):
        if command[0] != 'docker':
            return subprocess.run(command, **kwargs)  # docker/edge-env.sh runs for real
        if command[:3] == ['docker', 'network', 'rm']:
            for name in command[3:]:
                self.networks.pop(name, None)
            return SimpleNamespace(returncode=0, stdout='')
        if command[:3] == ['docker', 'network', 'ls']:
            return SimpleNamespace(returncode=0, stdout='\n'.join(f'id-{name}' for name in self.networks))
        if command[:3] == ['docker', 'network', 'inspect']:
            return SimpleNamespace(
                returncode=0, stdout=''.join(f'{name} {subnet} \n' for name, subnet in self.networks.items())
            )
        raise AssertionError(command)

    def Popen(self, command, **kwargs):
        assert command[:3] == ['docker', 'network', 'create']
        subnet, name = command[command.index('--subnet') + 1], command[-1]
        clash = self.refuses_overlap and any(
            ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network(held)) for held in self.networks.values()
        )
        if not clash:
            self.networks[name] = subnet
        self.in_flight += 1
        self.most_at_once = max(self.most_at_once, self.in_flight)
        docker = self

        class Process:
            returncode = 1 if clash else 0

            def communicate(self):
                docker.in_flight -= 1
                return (OVERLAP if clash else f'{name}-id'), None

        return Process()


def test_the_gate_proves_its_stacks_hold_edge_networks_at_once_and_a_shared_range_fails(monkeypatch):
    def check(docker, ranges=release_checks.EDGE):
        monkeypatch.setattr(release_checks, 'subprocess', docker)
        return release_checks.edge_networks(ranges)

    docker = FakeDocker(
        held={
            'tico-rc-edgecheck-smoke': '172.30.253.0/28',  # left by an interrupted check
            'bridge': '172.17.0.0/16',
            'tico_edge': '172.30.254.0/28',
        }
    )
    ok, report = check(docker)
    assert ok, report
    assert docker.most_at_once == len(release_checks.EDGE)  # every stack's network was being created at once
    assert set(docker.networks) == {'bridge', 'tico_edge'}  # probes removed, the install's network untouched
    assert 'journey 172.30.252.0/28, smoke 172.30.253.0/28' in report and 'refused as expected' in report

    ok, report = check(FakeDocker(), {'journey': '172.30.252', 'smoke': '172.30.252'})  # the v0.3.53 failure, forced
    assert not ok and 'could not create smoke' in report and 'Pool overlaps' in report

    ok, report = check(FakeDocker(held={'tico-journey_edge': '172.30.253.0/28', 'wide': '172.30.0.0/16'}))
    assert not ok and 'tico-journey_edge 172.30.253.0/28' in report and 'wide 172.30.0.0/16' in report

    ok, report = check(FakeDocker(refuses_overlap=False))  # a host where a shared range would go unnoticed
    assert not ok and 'proves nothing' in report
