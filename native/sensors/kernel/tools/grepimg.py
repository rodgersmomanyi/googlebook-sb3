import sys
keys=[k.encode() for k in sys.argv[2:]]
f=open(sys.argv[1],"rb"); off=0; tail=b""; hits={}
while True:
    b=f.read(64<<20)
    if not b: break
    buf=tail+b
    for k in keys:
        i=buf.find(k)
        while i!=-1:
            hits.setdefault(k,[]).append(off-len(tail)+i); i=buf.find(k,i+1)
    tail=buf[-256:]; off+=len(b)
for k,v in hits.items(): print(k.decode(), len(v), [hex(x) for x in sorted(set(v))[:8]])
print("magic", open(sys.argv[1],"rb").read(2048)[1024:1040].hex())
