"""Check-e sabok-e dastresi be site-e irica. Yek khat chap mikonad: UP ya DOWN."""
import sys, ssl, time, urllib.request

URL = "https://epl.irica.gov.ir/ImeiRegister"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    t0 = time.time()
    r = urllib.request.urlopen(URL, timeout=12, context=ctx)
    body = r.read()
    dt = time.time() - t0
    if r.status == 200 and len(body) > 2000:
        print(f"UP status={r.status} len={len(body)} {dt:.1f}s")
        sys.exit(0)
    print(f"DOWN status={r.status} len={len(body)}")
    sys.exit(1)
except Exception as e:
    print(f"DOWN {type(e).__name__}: {str(e)[:80]}")
    sys.exit(1)
