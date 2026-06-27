from util.proxy.ProxyManager import ProxyManager

# sg.nexip.cc should resolve to real IP via DoH
resolved = ProxyManager._resolve_proxy_host('socks5://test:pass@sg.nexip.cc:443')
print(f'socks5 auth: {resolved}')
assert '198.18' not in resolved, f"Got fake-IP: {resolved}"
assert resolved != 'socks5://test:pass@sg.nexip.cc:443', "Did not resolve"
print(f'  OK - resolved to real IP, auth preserved: {"@107." in resolved or "@45." in resolved or "@" in resolved}')

# http same
resolved2 = ProxyManager._resolve_proxy_host('http://test:pass@sg.nexip.cc:3010')
print(f'http auth: {resolved2}')
assert '198.18' not in resolved2

# none -> none
assert ProxyManager._resolve_proxy_host('none') == 'none'
print('none: OK')

# IP already -> unchanged
ip_res = ProxyManager._resolve_proxy_host('socks5://user:pass@1.2.3.4:1080')
assert ip_res == 'socks5://user:pass@1.2.3.4:1080'
print('ip passthrough: OK')

print('\nAll tests passed!')
