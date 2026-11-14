import json, sys, time, urllib.request
url='http://127.0.0.1:31000/v1/chat/completions'
payload={"model":"qwen38-flash-next","messages":[{"role":"user","content":"Solve 2+2. End your response with the final integer 4."}],"temperature":0,"max_tokens":64,"stream":False}
req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
t0=time.time()
with urllib.request.urlopen(req,timeout=120) as r:
    raw=r.read()
print(json.dumps({"elapsed_s":time.time()-t0,"response":json.loads(raw)},ensure_ascii=False))
