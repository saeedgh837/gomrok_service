import json, os, re, subprocess, sys, tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "gomrok.py")
src = open(SRC, encoding="utf-8").read()
js = re.search(r'_SCS_FAST_JS = r?"""(.*?)"""', src, re.S).group(1)

harness = r"""
const FAKE=require('fs').readFileSync(process.argv[2],'utf8');
let clock=0; let timers=[],intervals=[],seq=0;
global.setTimeout=(fn,ms)=>{const t={id:++seq,fn,at:clock+ms};timers.push(t);return t.id;};
global.setInterval=(fn,ms)=>{const t={id:++seq,fn,at:clock+ms,iv:ms};intervals.push(t);return t.id;};
global.clearTimeout=(id)=>{timers=timers.filter(t=>t.id!==id);};
global.clearInterval=(id)=>{intervals=intervals.filter(t=>t.id!==id);};
let DONE_RESULT=null;
const done=(o)=>{DONE_RESULT=o;};
const SCEN=JSON.parse(process.argv[3]);
const els={};
const mkQ=(wp)=>els[wp]||(els[wp]={wp,disabled:false,className:'',value:'',focus(){},dispatchEvent(){}});
global.q=(wp)=>mkQ(wp);
global.clearErr=()=>{};
global.MutationObserver=class{constructor(fn){this.fn=fn;} observe(){}};
global.document={documentElement:{},
  querySelector:(wp)=>{const k=(wp||'').split('"')[1]||wp; return mkQ(k);},
  querySelectorAll:()=>[]};
global.busy=()=>SCEN.loaderOn;
global.endGone=()=>false;
global.readVerdict=()=>{if(SCEN.codeOkAt!==null && clock>=SCEN.codeOkAt)return {v:2}; if(SCEN.errAt!==null && clock>=SCEN.errAt)return {v:1,other:'bad'}; return {v:0};};
// the real JS declares its own readVerdict/busy; neutralize both
patched_extra='readVerdict=()=>{if(SCEN.codeOkAt!==null && clock>=SCEN.codeOkAt)return {v:2}; if(SCEN.errAt!==null && clock>=SCEN.errAt)return {v:1,other:\'bad\'}; return {v:0};};busy=()=>SCEN.loaderOn;';
global.pressButton=(b)=>{clock+=SCEN.clickMs; if(SCEN.onClick)SCEN.onClick(clock);};
global.pressEnter=()=>{clock+=SCEN.enterMs; if(SCEN.onEnter)SCEN.onEnter(clock); return true;};
let patched=FAKE
  .replace(/var i=-1[\s\S]*?function fire\(\)\{[\s\S]*?\n  \}/,
    `var i=-1,n=0,others=[],settled=false,obs=null,tmr=null,pulse=null,cur=null;
     function finish(o){settled=true;if(tmr)clearTimeout(tmr);if(pulse)clearInterval(pulse);o.tried=n;o.others=others;done(o);}
     function advance(v){if(!cur||cur.done)return;cur.done=true;if(tmr)clearTimeout(tmr);if(pulse)clearInterval(pulse);if(v&&v.other)others.push({code:cur.code,msg:v.other});fire();}
     function fire(){
       i++; if(i>=CODES.length){finish({result:'exhausted'});return;}
       var b=q(END_WP); if(!b){finish({result:'success',index:i,code:CODES[i]});return;}
       var f=q(CHECK_WP); if(!f){finish({result:'stuck',index:i,code:CODES[i]});return;}
       clearErr(); n++; var code=CODES[i]; cur={code:code,done:false,started:clock,entered:false,resent:false};
       pressButton(b);
       if(tmr)clearTimeout(tmr); if(pulse)clearInterval(pulse);
       pulse=setInterval(onPulse,150); tmr=setTimeout(onTimeout,2500);
     }`);
patched=patched.replace('CODES=arguments[0]','CODES='+JSON.stringify(SCEN.codes))
               .replace('CHECK_WP=arguments[2]','CHECK_WP="check"')
               .replace('END_WP=arguments[3]','END_WP="end"');
patched=patched.replace(/\n  fire\(\);\s*$/, '\n');
patched=patched.replace(/function readVerdict\(\)\{[\s\S]*?\n  \}/, patched_extra);
patched=patched.replace(/function busy\(\)\{[\s\S]*?\n  \}/, '');
eval(patched);
fire();
console.error('DBG timers='+timers.length+' intervals='+intervals.length+' clock='+clock);
let guard=0,dbglog=[];
while(!DONE_RESULT && guard++<5000){
  const allT=[...timers.map(t=>t.at),...intervals.map(t=>t.at)];
  const next=allT.sort((a,b)=>a-b)[0];
  if(next===undefined)break;
  clock=next;
  dbglog.push('t='+clock);
  timers.filter(t=>t.at<=clock).forEach(t=>{timers=timers.filter(x=>x.id!==t.id);t.fn();});
  intervals.filter(t=>t.at<=clock).forEach(t=>{t.at+=t.iv;t.fn();});
}
console.error('DBG2 '+dbglog.slice(0,8).join(','));
console.log(JSON.stringify({result:DONE_RESULT,clock}));
"""

def run(name, scen):
    # serialize scenario: replace lambdas with plain flags
    serial = {
        "codes": scen["codes"],
        "loaderOn": bool(scen.get("loader_on")),
        "errAt": scen.get("err_at"),
        "codeOkAt": scen.get("code_ok_at"),
        "clickMs": scen.get("click_ms", 50),
        "enterMs": scen.get("enter_ms", 50),
    }
    t = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    t.write(harness); t.close()
    h = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    h.write(js); h.close()
    try:
        r = subprocess.run(["node", t.name, h.name, json.dumps(serial)],
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip() + (" | ERR: " + r.stderr.strip()[:200] if r.stderr.strip() else "")
        print(name, out[:300])
    finally:
        os.unlink(t.name); os.unlink(h.name)

run("fast-err", {"codes": ["1111"], "loader_on": False, "err_at": 100})
run("loader-stuck-enter", {"codes": ["2222"], "loader_on": True})
run("success-on-code", {"codes": ["3333"], "loader_on": False, "code_ok_at": 100})
run("silent-reclick", {"codes": ["4444"], "loader_on": False})
