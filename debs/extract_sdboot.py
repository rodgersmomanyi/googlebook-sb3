import io, lzma, os, tarfile, sys
D = os.path.dirname(os.path.abspath(__file__))
deb = os.path.join(D, "systemd-boot-efi_262.1-1_amd64.deb")
d = open(deb,'rb').read(); p=8; ar={}
while p < len(d):
    h=d[p:p+60]; nm=h[:16].decode().strip().rstrip('/'); sz=int(h[48:58]); p+=60
    ar[nm]=d[p:p+sz]; p+=sz+(sz&1)
print("members:", list(ar.keys()))
dm=[k for k in ar if k.startswith('data.tar')][0]
raw=ar[dm]
if dm.endswith('.xz'): raw=lzma.decompress(raw)
elif dm.endswith('.zst'):
    import zstandard; raw=zstandard.ZstdDecompressor().decompress(raw)
tf=tarfile.open(fileobj=io.BytesIO(raw))
names=[n for n in tf.getnames() if n.endswith('.efi')]
print("efi files:", names)
want=[n for n in names if 'systemd-bootx64.efi' in n][0]
data=tf.extractfile(want).read()
open(os.path.join(D, "systemd-bootx64.efi"), "wb").write(data)
print("extracted systemd-bootx64.efi:", len(data), "bytes")
