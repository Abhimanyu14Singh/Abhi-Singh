import sys, json
t = open(sys.argv[1]).read()
i = t.index('{\n "model"')
j = t.index('\n}\n', i) + 2
d = json.loads(t[i:j])
for r in d['rows']:
    print(r)
print(d['pick'])
print(d['box'])
print('errs', d['errs'])
print({k: d['gstats'].get(k) for k in ('calls', 'triangles', 'instances', 'cpu_ms', 'overlay_ms', 'lastError')})
print(t[j:][:800])
