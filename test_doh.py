import json, urllib.request

req = urllib.request.Request('https://dns.google/resolve?name=sg.nexip.cc&type=A')
resp = urllib.request.urlopen(req, timeout=5)
data = json.loads(resp.read().decode())
for a in data.get('Answer', []):
    atype = a['type']
    adata = a['data']
    print(f'type={atype} data={adata}')
