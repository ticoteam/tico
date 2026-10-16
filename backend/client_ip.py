"""Client addresses from explicitly trusted proxies; forwarded headers are otherwise ignored."""

from ipaddress import IPv4Network, ip_address, ip_network

MAPPED = ip_network('::ffff:0:0/96')


def unmapped(ip):
    """An IPv4-mapped IPv6 address (::ffff:a.b.c.d, a dual-stack socket's IPv4 peer) as the IPv4 address it is."""
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def _network(value):
    net = ip_network(value.strip(), strict=False)
    if net.version == 6 and net.prefixlen >= 96 and net.subnet_of(MAPPED):
        net = IPv4Network((int(net.network_address) & 0xFFFFFFFF, net.prefixlen - 96))
    return net


def trusted_proxies(values):
    parts = values.split(',') if isinstance(values, str) else values
    return tuple(str(_network(value)) for value in parts if value.strip())


def client_ip(request, settings):
    peer = request.client.host if request.client else ''
    try:
        address = unmapped(ip_address(peer))
    except ValueError:
        return 'unknown'
    networks = tuple(_network(value) for value in settings.trusted_proxies)
    trusted = lambda ip: any(ip.version == net.version and ip in net for net in networks)
    if not trusted(address):
        return str(address)
    raw = request.headers.get(settings.client_ip_header, '')
    if not raw or len(raw) > 2048:
        return str(address)
    try:
        if settings.client_ip_header == 'cf-connecting-ip':
            return str(unmapped(ip_address(raw.strip())))
        chain = raw.split(',')
        if len(chain) > 32:
            return str(address)
        for part in reversed(chain):
            if not trusted(address):
                break
            address = unmapped(ip_address(part.strip()))
    except ValueError:
        return str(unmapped(ip_address(peer)))
    return str(address)


def ip_bucket(address):
    try:
        ip = unmapped(ip_address(address))
    except ValueError:
        return 'unknown'
    return str(ip_network(f'{ip}/64', strict=False)) if ip.version == 6 else str(ip)
