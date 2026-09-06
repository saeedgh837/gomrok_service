"""Site-2 (epl.irica.ir) worker.

Jaryan:
  1) profile-ra az panel claim mikonad (busy + runner_id + heartbeat).
  2) Chrome-ra baz mikonad; banner-e sabz ba dokme-ye 'edame' neshan midahad.
  3) Karbar be safhe-ye kod-e payamaki miravad va 'edame'-ra mizanad.
  4) Banner be halat-e 'vorud-e kod' switch mishavad: karbar kod-e VAGHE'i-ye
     resideh be gushi-ash-ra vared mikonad; robot AN-ra YEK-BAR sabt mikonad.
     - robot HICH kodi khodash tolid NEMIKONAD (brute-force nist).
     - agar 'kod eshtebah' bud -> payam-e khata neshan dade mishavad va montazer-e
       kod-e BA'DI mimanad ke karbar bedahad.
     - agar safhe avaz shod (marker-e vaghe'i) -> movaffagh; HTML-e safhe-ye ba'd
       dump mishavad ta marhale-haye ba'di rooyash sakhte shavand.
  5) dar khorooj/khata profile release mishavad.
"""
import argparse
import atexit
import os
import sys
import threading
import time

import captcha_collector as cc
import system_debug

URL = "https://epl.irica.ir/login?2"

_HERE = os.path.dirname(os.path.abspath(__file__))
_DBG_AFTER_CODE = os.path.join(_HERE, "_dbg_site2_after_code.html")
_DBG_DIR = os.path.join(_HERE, "_dbg_site2")

# --- wicketpath-haye safhe-ye tayid-e shomare/kod (az lab/tests/page.html) ---
WP_CODE_FIELD = "form_confirmePanel_confirmForm_code"
WP_CONFIRM_BTN = "form_confirmePanel_confirmForm_confirmeCode"
WP_CODE_ALERT = "form_confirmePanel_confirmForm_alert"
WP_SENT_MSG = "form_alertSuccess"  # "کد امنیتی به شماره تلفن شما ارسال شد ..."

# jomle-ye khata-ye 'kod eshtebah' (baraye tashkhis-e sarih; har alert-e digar ham
# be onvan-e khata be karbar neshan dade mishavad).
BAD_CODE_TEXT = "کد وارد شده با کد ارسالی مطابقت ندارد"

# Plan-e sabet-e in kar: kod-haye 5000 ta 5050 (51 kod), yeki-yeki (dar file baked
# shode; az noar gerefte NEMISHAVAD). loop be te'dad-e hamin kod-ha ejra mishavad:
# har talash yek kod migozarad va dokme-ra mizanad. ba --codes/--code override mishavad.
DEFAULT_CODE_PLAN = ",".join(str(c) for c in range(1000, 9999))

def ensure_utf8_stdio():
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# hamahangi ba panel: claim / heartbeat / release
# ---------------------------------------------------------------------------
_CLAIMED_PROFILE_ID = ""
_CLAIMED_RUNNER_ID = ""
_CLAIMED_NATIONAL = ""


def _claim_panel_profile(profile_id=None):
    try:
        import panel
        return panel.claim_profile(profile_id)
    except Exception as e:
        print(f">>> (claim-e profile anjam nashod: {e})")
        return None


def _release_panel_profile(profile_id, runner_id):
    if not profile_id or not runner_id:
        return False
    try:
        import panel
        return panel.release_profile(profile_id, runner_id)
    except Exception as e:
        print(f">>> (release-e profile anjam nashod: {e})")
        return False


def _start_profile_heartbeat(profile_id, runner_id):
    def beat():
        while True:
            try:
                import panel
                if not panel.heartbeat_profile(profile_id, runner_id):
                    return
            except Exception:
                pass
            time.sleep(30)

    thread = threading.Thread(target=beat, name="gomrok2-heartbeat", daemon=True)
    thread.start()
    return thread


def _read_captured_national(driver):
    """Kod-e melli ke karbar dar safhe dasti vared karde (banner an-ra dar
    window.__gomrok2_national sabt mikonad) ra barmigardanad."""
    try:
        return str(driver.execute_script("return window.__gomrok2_national||'';") or "").strip()
    except Exception:
        return ""


def _notify_site2_success(national):
    """Movaffaghiat-e vorud: yek log + notif dar panel-e site2 sabt mishavad
    (gomrok2 --no-panel ham hamin panel-ra seda mizanad)."""
    try:
        import panel
        panel.log_site2_success(national)
    except Exception as e:
        print(f">>> (notif/log-e site2 anjam nashod: {e})")
    print(f">>> [gomrok2] MOVAFfagh: vorud-e movaffagh be hesab-e site2 | "
          f"national={national or '-'}", flush=True)


# ---------------------------------------------------------------------------
# banner: 3 halat -> handoff | code | done
# ---------------------------------------------------------------------------
class ManualHandoff:
    _BANNER_JS = r"""
    (function(){
      function bar(){
        var b=document.getElementById('__gomrok2_bar');
        if(!b){
          b=document.createElement('div'); b.id='__gomrok2_bar';
          b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483647;'+
            'direction:rtl;font-family:Tahoma,sans-serif;background:#102a22;color:#eefbf6;'+
            'border-bottom:2px solid #24c887;padding:10px 16px;display:flex;gap:12px;'+
            'align-items:center;flex-wrap:wrap;box-shadow:0 5px 20px #0008';
          document.body.appendChild(b);
        }
        return b;
      }
      function pad(b){ if(document.body) document.body.style.paddingTop=(b.offsetHeight+6)+'px'; }
      // MOHEM: agar innerHTML-ra bedun-e taghyir dobare benevisim, MutationObserver-e
      // paeen dobare render mikonad -> halghe-ye bi-payan va HANG-e safhe.
      function setHTML(el,html){ if(el && el.innerHTML!==html) el.innerHTML=html; }
      var TITLE=window.__gomrok2_title||'سایت دوم — مدیریت دستی';

      function renderHandoff(){
        var b=bar();
        if(b.getAttribute('data-mode')!=='handoff'){
          b.setAttribute('data-mode','handoff'); b.innerHTML='';
          var t=document.createElement('div');
          t.style.cssText='flex:1;font-size:14px;min-width:200px';
          t.innerHTML='<b style="color:#39df9c">'+TITLE+'</b> | به صفحه‌ی کد برو، سپس «ادامه» را بزن.';
          var btn=document.createElement('button'); btn.id='__gomrok2_btn'; btn.textContent='ادامه ▶';
          btn.style.cssText='border:0;border-radius:9px;padding:9px 22px;background:#24b979;'+
            'color:white;font:bold 14px Tahoma;cursor:pointer';
          btn.onclick=function(){window.__gomrok2_continue=true;btn.disabled=true;
            btn.style.opacity=.55;btn.textContent='در حال شروع...';};
          b.appendChild(t); b.appendChild(btn);
        }
        pad(b);
      }

      function renderCode(){
        var b=bar();
        if(b.getAttribute('data-mode')!=='code'){
          b.setAttribute('data-mode','code'); b.innerHTML='';
          var t=document.createElement('div');
          t.style.cssText='font-size:14px;min-width:150px';
          t.innerHTML='<b style="color:#39df9c">'+TITLE+'</b> — کد پیامک:';
          var inp=document.createElement('input'); inp.id='__gomrok2_code_input';
          inp.type='text'; inp.inputMode='numeric'; inp.autocomplete='off'; inp.placeholder='مثلاً 1234';
          inp.style.cssText='direction:ltr;text-align:center;font:bold 16px Tahoma;width:120px;'+
            'padding:8px 10px;border-radius:9px;border:1px solid #24c887;background:#0d1729;color:#eefbf6';
          var btn=document.createElement('button'); btn.id='__gomrok2_code_btn'; btn.textContent='ثبت کد ▶';
          btn.style.cssText='border:0;border-radius:9px;padding:9px 20px;background:#24b979;'+
            'color:white;font:bold 14px Tahoma;cursor:pointer';
          function submit(){
            var v=(inp.value||'').trim(); if(!v) return;
            window.__gomrok2_code=v; window.__gomrok2_code_submit=true;
            inp.disabled=true; btn.disabled=true; btn.style.opacity=.55; btn.textContent='در حال ثبت...';
          }
          btn.onclick=submit;
          inp.onkeydown=function(e){ if(e.keyCode===13){ e.preventDefault(); submit(); } };
          var st=document.createElement('div'); st.id='__gomrok2_code_status';
          st.style.cssText='flex:1;font-size:13px;min-width:200px;color:#cfe';
          b.appendChild(t); b.appendChild(inp); b.appendChild(btn); b.appendChild(st);
          try{ inp.focus(); }catch(e){}
        }
        var st=document.getElementById('__gomrok2_code_status');
        if(st) setHTML(st, window.__gomrok2_status||'');
        // az-tarafe python: agar ghofl baz shod -> control-ha faal + khali beshan
        var inp=document.getElementById('__gomrok2_code_input');
        var btn=document.getElementById('__gomrok2_code_btn');
        if(inp&&btn&&!window.__gomrok2_code_locked&&inp.disabled){
          inp.disabled=false; btn.disabled=false; btn.style.opacity=1;
          btn.textContent='ثبت کد ▶'; inp.value=''; try{ inp.focus(); }catch(e){}
        }
        pad(b);
      }

      function renderCapture(){
        var b=bar();
        if(b.getAttribute('data-mode')!=='capture'){
          b.setAttribute('data-mode','capture'); b.innerHTML='';
          var t=document.createElement('div');
          t.style.cssText='font-size:14px;min-width:180px';
          t.innerHTML='<b style="color:#39df9c">'+TITLE+'</b> — حالت دیباگ';
          var btn=document.createElement('button'); btn.id='__gomrok2_cap_btn';
          btn.textContent=window.__gomrok2_cap_label||'ادامه / ثبت صفحه ▶';
          btn.style.cssText='border:0;border-radius:9px;padding:9px 22px;background:#24b979;'+
            'color:white;font:bold 14px Tahoma;cursor:pointer';
          btn.onclick=function(){
            window.__gomrok2_capture=true; btn.disabled=true; btn.style.opacity=.55;
            btn.textContent='در حال ثبت...';
          };
          var note=document.createElement('input'); note.id='__gomrok2_cap_note';
          note.type='text'; note.autocomplete='off'; note.placeholder='برچسب این مرحله (اختیاری)';
          note.style.cssText='direction:rtl;font:13px Tahoma;width:190px;padding:8px 10px;'+
            'border-radius:9px;border:1px solid #24c887;background:#0d1729;color:#eefbf6';
          var st=document.createElement('div'); st.id='__gomrok2_cap_status';
          st.style.cssText='flex:1;font-size:13px;min-width:180px;color:#cfe';
          b.appendChild(t); b.appendChild(note); b.appendChild(btn); b.appendChild(st);
        }
        var st=document.getElementById('__gomrok2_cap_status');
        if(st) setHTML(st, window.__gomrok2_status||'');
        var btn=document.getElementById('__gomrok2_cap_btn');
        if(btn&&btn.disabled&&window.__gomrok2_capture!==true){
          btn.disabled=false; btn.style.opacity=1;
          btn.textContent=window.__gomrok2_cap_label||'ادامه / ثبت صفحه ▶';
        }
        pad(b);
      }

      function renderDone(){        var b=bar();
        if(b.getAttribute('data-mode')!=='done'){
          b.setAttribute('data-mode','done'); b.innerHTML='';
          b.style.background='#0f2f22'; b.style.borderBottomColor='#39df9c';
          var t=document.createElement('div'); t.style.cssText='flex:1;font-size:14px';
          t.innerHTML='<b style="color:#7dffc0">✓ '+TITLE+'</b> — '+
            (window.__gomrok2_status||'کد پذیرفته شد؛ صفحه عوض شد.');
          b.appendChild(t);
        } else {
          var t=b.firstChild;
          if(t) setHTML(t, '<b style="color:#7dffc0">✓ '+TITLE+'</b> — '+
            (window.__gomrok2_status||'کد پذیرفته شد؛ صفحه عوض شد.'));
        }
        pad(b);
      }

      function fa2en(s){
        s=(s||'').replace(/[۰-۹]/g,function(c){return '۰۱۲۳۴۵۶۷۸۹'.indexOf(c);});
        return s.replace(/[٠-٩]/g,function(c){return '٠١٢٣٤٥٦٧٨٩'.indexOf(c);});
      }
      function captureNational(){
        if(window.__gomrok2_national) return;
        try{
          var els=document.querySelectorAll('input');
          for(var i=0;i<els.length;i++){
            var el=els[i];
            if(el.closest && el.closest('#__gomrok2_bar')) continue;
            var v=fa2en(el.value||'').replace(/\D/g,'');
            if(v.length===10){ window.__gomrok2_national=v; return; }
          }
        }catch(e){}
      }
      function render(){
        captureNational();
        var ph=window.__gomrok2_phase||'handoff';
        if(ph==='code') renderCode();
        else if(ph==='capture') renderCapture();
        else if(ph==='done') renderDone();
        else renderHandoff();
      }
      render();
      if(!window.__gomrok2_observer){
        // faghat baraye taghyirat-e KHAREJ az bar render mikonim; vagarna
        // taghyirat-e khod-e bar bazam observer-ra shellik mikonad -> hang.
        window.__gomrok2_observer=new MutationObserver(function(ms){
          for(var i=0;i<ms.length;i++){
            var t=ms[i].target;
            if(t && t.closest && t.closest('#__gomrok2_bar')) continue;
            render();
            return;
          }
        });
        window.__gomrok2_observer.observe(document.documentElement,{childList:true,subtree:true});
      }
      window.__gomrok2_watch=window.__gomrok2_watch||setInterval(render,700);
    })();
    """

    def __init__(self, driver, title="", poll_interval=0.35):
        self.driver = driver
        self.title = title or "سایت دوم — مدیریت دستی"
        self.poll_interval = poll_interval

    def _ensure(self):
        try:
            self.driver.execute_script("window.__gomrok2_title=arguments[0];", self.title)
            self.driver.execute_script(self._BANNER_JS)
        except Exception:
            pass

    def set_phase(self, phase):
        try:
            self.driver.execute_script("window.__gomrok2_phase=arguments[0];", phase)
        except Exception:
            pass

    def set_status(self, text, locked=None):
        try:
            self.driver.execute_script("window.__gomrok2_status=arguments[0];", text or "")
            if locked is not None:
                self.driver.execute_script("window.__gomrok2_code_locked=arguments[0];", bool(locked))
        except Exception:
            pass

    def wait_continue(self):
        print(">>> [gomrok2] be safhe-ye kod boro va dokme-ye 'edame'-ra bezan.")
        while True:
            self._ensure()
            try:
                if self.driver.execute_script("return window.__gomrok2_continue===true;"):
                    return
            except Exception:
                pass
            time.sleep(self.poll_interval)


# ---------------------------------------------------------------------------
# JS-haye vorud-e kod
# ---------------------------------------------------------------------------
# por-kardan-e field-e kod + click-e dokme-ye tayid ba zanjire-ye kamel-e
# touch/pointer/mouse (mesl-e gomrok.py; click-e sade rooye in site javab nemidahad).
_FIRE_JS = r"""
  var f=document.querySelector('[wicketpath="%s"]');
  var b=document.querySelector('[wicketpath="%s"]');
  if(!f||!b) return false;
  f.value=arguments[0];
  f.dispatchEvent(new Event('input',{bubbles:true}));
  f.dispatchEvent(new Event('change',{bubbles:true}));
  var r=b.getBoundingClientRect();
  var cx=r.left+r.width/2, cy=r.top+r.height/2;
  function ev(type,C){ var e=new C(type,{bubbles:true,cancelable:true,
    clientX:cx,clientY:cy,view:window}); b.dispatchEvent(e); }
  try{ var t=new Touch({identifier:1,target:b,clientX:cx,clientY:cy});
    b.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,cancelable:true,
      touches:[t],targetTouches:[t],changedTouches:[t]}));
    b.dispatchEvent(new TouchEvent('touchend',{bubbles:true,cancelable:true,
      touches:[],targetTouches:[],changedTouches:[t]}));
  }catch(e){}
  try{ ev('pointerdown',PointerEvent); ev('pointerup',PointerEvent); }catch(e){}
  ev('mousedown',MouseEvent); ev('mouseup',MouseEvent); ev('click',MouseEvent);
  return true;
""" % (WP_CODE_FIELD, WP_CONFIRM_BTN)

# AJAX dar jaryan? (dokme disabled ya indicator namayan). ta busy ast nabayad
# result-ra bekhanim ya kod-e ba'di-ra bezanim.
_BUSY_JS = r"""
  var b=document.querySelector('[wicketpath="%s"]');
  if(b && (b.disabled || (' '+(b.className||'')+' ').indexOf(' disabled ')>=0)) return true;
  var ind=document.querySelectorAll('.wicket-ajax-indicator');
  for(var j=0;j<ind.length;j++){ if(ind[j].offsetParent!==null) return true; }
  return false;
""" % (WP_CONFIRM_BTN)

# vaziat-ra bekhan: alert (matn-e khata), field-e kod hanooz hast?, payam-e
# 'ersal shod' hanooz hast?, va URL-e fe'li.
_READ_JS = r"""
  var a=document.querySelector('[wicketpath="%s"]');
  var f=document.querySelector('[wicketpath="%s"]');
  var s=document.querySelector('[wicketpath="%s"]');
  return {
    alert: a?(a.innerText||'').trim():'',
    formGone: !f,
    sentGone: !s,
    url: location.href
  };
""" % (WP_CODE_ALERT, WP_CODE_FIELD, WP_SENT_MSG)

_CLEAR_ALERT_JS = r"""
  var a=document.querySelector('[wicketpath="%s"]');
  if(a) a.innerHTML='';
  return true;
""" % (WP_CODE_ALERT)

# ---------------------------------------------------------------------------
# Kamtarin-taakhir: TAMAM-e halghe-ye retry darun-e morurgar ejra mishavad.
# Loading-e shenasaei-shode = <span id="confirmeCode--ajax-indicator"
# class="wicket-ajax-indicator"> ke display: none <-> inline mishavad.
# Sigan-haye ghati'e natije (ba wicketpath-e paydar, na id-haye motaghayer-e Wicket):
#   * ghalat  -> HAR taghyir dar cell-e alert (WP_CODE_ALERT), che BAD bashad
#                che payam-e digar; ta zamani ke alert taghyir nakonad, settle
#                nemishavad ta feshar-e ajax rad shavad (guard 300ms)
#   * movaffagh-> dokme-ye tayid (WP_CONFIRM_BTN) az DOM mirvad (panel avaz shod)
#   * ajax tamam shod -> button dobare faal shod (guard 500ms)
# be mahz-e didan-e hame-ye in sigan-ha, HAMAN event-loop dobare fill+click
# mikonad -> bedun-e hich round-trip-e Python. GAP=0 => shellik-e bi-fasele;
# GAP>0 => fasele-ye ekhtiari (ms) baraye halati ke server niaz be forsat-e
# propagate-shodan darad.
# safety-net: har talash 2.5s (tmr) — agar hich sigan-i nayamad, onTimeout
# natije-ra settle mikonad va kod-e ba'di amadeh mishavad (na 6s-e ghabli).
_FAST_RETRY_JS = r"""
  var CODES=arguments[0], MAXATT=arguments[1], GAP=arguments[2], BAD=arguments[3];
  var DONE=arguments[arguments.length-1];
  if(!(CODES&&CODES.length)){ CODES=['']; } if(!MAXATT){ MAXATT=CODES.length; }
  var WP_F='%s', WP_B='%s', WP_A='%s';
  function q(wp){ return document.querySelector('[wicketpath="'+wp+'"]'); }
  function alertText(){ var a=q(WP_A); return a?((a.innerText||a.textContent||'').trim()):''; }
  function formGone(){ return !q(WP_B); }
  function clearAlert(){ var a=q(WP_A); if(a) a.innerHTML=''; }
  function btnReady(){ var b=q(WP_B); return b && !b.disabled; }

  var attempts=[], n=0, settled=false, obs=null, tmr=null;
  function cur(){ return attempts.length ? attempts[attempts.length-1] : null; }

  function finish(result){
    if(settled) return; settled=true;
    if(obs){ try{ obs.disconnect(); }catch(e){} }
    if(tmr){ clearTimeout(tmr); tmr=null; }
    DONE({result:result, total:n, attempts:attempts});
  }

  function setStatus(t){ try{ window.__gomrok2_status=t; }catch(e){} }

  function settleAttempt(rec, result, atext){
    if(rec.done) return; rec.done=true;
    rec.t1=performance.now(); rec.ms=Math.round(rec.t1-rec.t0);
    rec.result=result; rec.alert=atext||'';
    if(tmr){ clearTimeout(tmr); tmr=null; }
    if(result==='success'){ finish('success'); return; }
    if(n>=MAXATT){ finish('failed'); return; }
    if(GAP>0){ setTimeout(fire, GAP); } else { fire(); }
  }

  function onTimeout(rec){
    if(settled||rec.done) return;
    if(formGone()){ settleAttempt(rec,'success',''); return; }
    var at=alertText();
    if(at && at.indexOf(BAD)>=0){ settleAttempt(rec,'wrong',at); return; }
    settleAttempt(rec,'timeout',at);
  }

  function fire(){
    n++;
    var inp=q(WP_F), btn=q(WP_B);
    if(!inp||!btn){ finish('notfound'); return; }
    var code=CODES[Math.min(n-1, CODES.length-1)];
    clearAlert();
    inp.value=code;
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
    var rec={n:n, code:code, t0:performance.now(), done:false, alertBefore:alertText()};
    attempts.push(rec);
    setStatus('تلاش '+n+' از '+MAXATT+': کد '+code+' ...');
    var r=btn.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
    function ev(type,C){ try{ btn.dispatchEvent(new C(type,{bubbles:true,cancelable:true,
      clientX:cx,clientY:cy,view:window})); }catch(e){} }
    try{ var t=new Touch({identifier:1,target:btn,clientX:cx,clientY:cy});
      btn.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,cancelable:true,
        touches:[t],targetTouches:[t],changedTouches:[t]}));
      btn.dispatchEvent(new TouchEvent('touchend',{bubbles:true,cancelable:true,
        touches:[],targetTouches:[],changedTouches:[t]}));
    }catch(e){}
    ev('pointerdown',PointerEvent); ev('pointerup',PointerEvent);
    ev('mousedown',MouseEvent); ev('mouseup',MouseEvent); ev('click',MouseEvent);
    if(tmr){ clearTimeout(tmr); }
    tmr=setTimeout(function(){ onTimeout(rec); }, 2500);
  }

  obs=new MutationObserver(function(){
    if(settled) return;
    var rec=cur(); if(!rec||rec.done) return;
    if(formGone()){ settleAttempt(rec,'success',''); return; }
    var at=alertText();
    if(at && at.indexOf(BAD)>=0){ settleAttempt(rec,'wrong',at); return; }
    // har taghyir-e dar alert (mesl-e ghalat-e motafavet) -> settle kon.
    // motmaen shav ke least 300ms az click gozashte baad ta server response beresad
    // va hamchenin alertBefore taghyir karde.
    if(at && at!==rec.alertBefore && performance.now()-rec.t0 > 300){
      settleAttempt(rec,'wrong',at); return;
    }
    // if button re-enabled after at least 500ms -> AJAX completed
    if(btnReady() && performance.now()-rec.t0 > 500){
      var a=alertText();
      if(formGone()) settleAttempt(rec,'success',a);
      else settleAttempt(rec, a ? 'wrong' : 'timeout', a);
      return;
    }
  });
  obs.observe(document.documentElement,{childList:true,subtree:true,
    characterData:true,attributes:true,attributeFilter:['style','class','disabled']});

  fire();
""" % (WP_CODE_FIELD, WP_CONFIRM_BTN, WP_CODE_ALERT)


def _wait_not_busy(driver, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not driver.execute_script(_BUSY_JS):
                return True
        except Exception:
            return True
        time.sleep(0.2)
    return False


def _code_page_ready(driver, timeout=20.0):
    """Montazer mimanad ta field-e kod rooye safhe zaher shavad."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script(
                    'return !!document.querySelector(\'[wicketpath="%s"]\');' % WP_CODE_FIELD):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _submit_one_code(driver, code):
    """YEK kod-ra sabt mikonad va natije-ra barmigardanad.

    return yeki az:
      ('wrong', matn-e-khata)   -> kod ghalat/khata; kod-e ba'di-ra begir
      ('success', url)          -> safhe avaz shod (marker-e vaghe'i)
      ('nochange', '')          -> na khata na taghyir dar mohlat; kod-e ba'di
    """
    try:
        driver.execute_script(_CLEAR_ALERT_JS)
    except Exception:
        pass
    ok = driver.execute_script(_FIRE_JS, code)
    if not ok:
        return ("wrong", "فیلد کد یا دکمه‌ی تأیید پیدا نشد؛ مطمئن شو روی صفحه‌ی کد هستی.")
    _wait_not_busy(driver, timeout=10.0)

    deadline = time.time() + 6.0
    last = {}
    while time.time() < deadline:
        try:
            last = driver.execute_script(_READ_JS) or {}
        except Exception:
            last = {}
        alert = (last.get("alert") or "").strip()
        if alert:
            return ("wrong", alert)
        # movaffaghiat: field-e kod raft VA payam-e 'ersal shod' ham raft.
        if last.get("formGone") and last.get("sentGone"):
            return ("success", last.get("url") or "")
        time.sleep(0.3)
    return ("nochange", "")


def run_code_entry(driver, handoff):
    """Halghe-ye vorud-e DASTI-ye kod. robot hich kodi tolid nemikonad."""
    if not _code_page_ready(driver):
        handoff.set_phase("code")
        handoff.set_status("صفحه‌ی کد پیدا نشد. به صفحه‌ی «تأیید شماره تلفن» برو.", locked=False)
        handoff._ensure()
        print(">>> [gomrok2] field-e kod peyda nashod.")
        # bazam edame midahim; shayad karbar navigate konad va kod bezanad.

    handoff.set_phase("code")
    handoff.set_status("کدی که به گوشی‌ات آمد را وارد کن و «ثبت کد» را بزن.", locked=False)
    handoff._ensure()
    print(">>> [gomrok2] amade-ye daryaft-e kod-e dasti hastam.")

    attempts = 0
    while True:
        handoff._ensure()
        try:
            submitted = driver.execute_script("return window.__gomrok2_code_submit===true;")
        except Exception:
            submitted = False
        if not submitted:
            time.sleep(0.3)
            continue

        # kod-ra bardashti; flag-ra reset kon va ghofl bezan ta processing.
        try:
            code = (driver.execute_script("return window.__gomrok2_code||'';") or "").strip()
            driver.execute_script("window.__gomrok2_code_submit=false;")
            driver.execute_script("window.__gomrok2_code_locked=true;")
        except Exception:
            code = ""
        if not code:
            handoff.set_status("کد خالی بود؛ دوباره وارد کن.", locked=False)
            continue

        attempts += 1
        print(f">>> [gomrok2] talash #{attempts}: kod sabt mishavad (yek-bar).")
        verdict, info = _submit_one_code(driver, code)

        if verdict == "success":
            _dump_after_code(driver)
            handoff.set_phase("done")
            handoff.set_status("کد پذیرفتنی بود؛ صفحه عوض شد. مرحله‌ی بعد هنوز تعریف نشده.")
            handoff._ensure()
            print(f">>> [gomrok2] MOVAFFAGH ba'd az {attempts} kod. url={info}")
            print(f">>> [gomrok2] HTML-e safhe-ye ba'd dump shod: {_DBG_AFTER_CODE}")
            return True
        elif verdict == "wrong":
            handoff.set_status("✗ " + info + " — کد بعدی را وارد کن.", locked=False)
            print(f">>> [gomrok2] kod rad shod: {info}")
        else:  # nochange
            handoff.set_status("تغییری در صفحه دیده نشد؛ کد بعدی را وارد کن.", locked=False)
            print(">>> [gomrok2] taghyiri dar safhe nabud (nochange).")


def _dump_after_code(driver):
    try:
        html = driver.page_source
        with open(_DBG_AFTER_CODE, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f">>> (dump-e HTML-e safhe-ye ba'd anjam nashod: {e})")


# ---------------------------------------------------------------------------
# halat-e DEBUG: har bar ke karbar 'edame' bezanad, snapshot-e safhe gerefte
# mishavad (HTML + screenshot + kholase-ye field/dokme-ha).
# ---------------------------------------------------------------------------
_INFO_JS = r"""
  function txt(el){
    var t=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
    return t.length>90 ? t.slice(0,90)+'...' : t;
  }
  function vis(el){
    return !!(el.offsetParent!==null || (el.getClientRects && el.getClientRects().length));
  }
  function mine(el){ return !!(el.id && el.id.indexOf('__gomrok2')===0); }

  var out={url:location.href, title:document.title, fields:[], clicks:[],
           alerts:[], frames:document.querySelectorAll('iframe').length, text:''};

  var f=document.querySelectorAll('input,select,textarea');
  for(var i=0;i<f.length && out.fields.length<150;i++){
    var e=f[i];
    if(mine(e)) continue;
    var opts=[];
    if(e.tagName==='SELECT'){
      for(var k=0;k<e.options.length && k<30;k++){
        opts.push(((e.options[k].text||'').trim())+' => '+(e.options[k].value||''));
      }
    }
    out.fields.push({tag:e.tagName.toLowerCase(), type:(e.type||''),
      wp:e.getAttribute('wicketpath')||'', name:e.name||'', id:e.id||'',
      ph:e.placeholder||'', val:(e.type==='password'?'***':(e.value||'')).slice(0,45),
      opts:opts, vis:vis(e), req:!!e.required, ro:!!(e.readOnly||e.disabled)});
  }

  var c=document.querySelectorAll('button,a,[role=button],input[type=submit],input[type=button]');
  for(var i2=0;i2<c.length && out.clicks.length<120;i2++){
    var e2=c[i2];
    if(mine(e2)) continue;
    var label=txt(e2)||e2.value||e2.getAttribute('title')||e2.getAttribute('aria-label')||'';
    if(!label && !e2.getAttribute('wicketpath')) continue;
    out.clicks.push({tag:e2.tagName.toLowerCase(), wp:e2.getAttribute('wicketpath')||'',
      id:e2.id||'', href:(e2.getAttribute('href')||'').slice(0,80), label:label, vis:vis(e2)});
  }

  var a=document.querySelectorAll('[wicketpath*="alert"],.alert,.feedbackPanel,.feedbackPanelERROR');
  for(var i3=0;i3<a.length && out.alerts.length<20;i3++){
    if(mine(a[i3])) continue;
    var m=txt(a[i3]); if(m) out.alerts.push(m);
  }

  var parts=[];
  for(var i4=0;i4<document.body.children.length;i4++){
    var ch=document.body.children[i4];
    if(ch.id==='__gomrok2_bar') continue;
    parts.push(ch.innerText||'');
  }
  out.text=parts.join('\n').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim().slice(0,7000);
  return out;
"""


def _fmt_snapshot(step, note, info):
    lines = [
        f"=== snapshot #{step} ===",
        f"note : {note or '-'}",
        f"url  : {info.get('url', '')}",
        f"title: {info.get('title', '')}",
        f"iframes: {info.get('frames', 0)}",
        "",
    ]
    alerts = info.get("alerts") or []
    if alerts:
        lines.append("--- alert/feedback ---")
        lines += [f"  ! {x}" for x in alerts]
        lines.append("")

    lines.append("--- fields (input/select/textarea) ---")
    for x in info.get("fields") or []:
        flag = "" if x.get("vis") else " [hidden]"
        flag += " [readonly]" if x.get("ro") else ""
        flag += " [required]" if x.get("req") else ""
        lines.append(
            f"  <{x.get('tag')} type={x.get('type')}> wp={x.get('wp') or '-'} "
            f"id={x.get('id') or '-'} name={x.get('name') or '-'} "
            f"placeholder={x.get('ph') or '-'} value={x.get('val') or '-'}{flag}"
        )
        for opt in x.get("opts") or []:
            lines.append(f"      option: {opt}")
    lines.append("")

    lines.append("--- clickables (button/a) ---")
    for x in info.get("clicks") or []:
        flag = "" if x.get("vis") else " [hidden]"
        lines.append(
            f"  <{x.get('tag')}> wp={x.get('wp') or '-'} id={x.get('id') or '-'} "
            f"href={x.get('href') or '-'} :: {x.get('label')}{flag}"
        )
    lines.append("")

    lines.append("--- visible text ---")
    lines.append(info.get("text") or "")
    return "\n".join(lines)


def _capture_snapshot(driver, step, note=""):
    """HTML + screenshot + kholase-ye safhe-ra zakhire mikonad."""
    os.makedirs(_DBG_DIR, exist_ok=True)
    base = os.path.join(_DBG_DIR, f"step_{step:02d}")
    saved = []

    try:
        info = driver.execute_script(_INFO_JS) or {}
    except Exception as e:
        info = {"url": "", "title": f"(khata dar khandan-e safhe: {e})"}

    try:
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        saved.append(base + ".html")
    except Exception as e:
        print(f">>> (dump-e HTML anjam nashod: {e})")

    try:
        driver.save_screenshot(base + ".png")
        saved.append(base + ".png")
    except Exception as e:
        print(f">>> (screenshot anjam nashod: {e})")

    summary = _fmt_snapshot(step, note, info)
    try:
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(summary)
        saved.append(base + ".txt")
    except Exception as e:
        print(f">>> (zakhire-ye kholase anjam nashod: {e})")

    print(summary)
    print(f">>> [gomrok2] snapshot #{step} zakhire shod: {', '.join(saved)}")
    print("-" * 70, flush=True)
    return info


def run_debug_capture(driver, handoff):
    """Halghe-ye DEBUG: karbar safhe-ra jelo mibarad, har bar 'edame' -> snapshot."""
    print(">>> [gomrok2] halat-e DEBUG. har ja lazem bud dokme-ye 'edame / sabt-e safhe'-ra bezan.")
    print(f">>> [gomrok2] khorooji dar: {_DBG_DIR}", flush=True)
    step = 0
    handoff.set_status("آماده‌ام؛ صفحه را جلو ببر و هر جا خواستی «ادامه» را بزن.")
    while True:
        handoff.set_phase("capture")
        handoff._ensure()
        try:
            pressed = driver.execute_script("return window.__gomrok2_capture===true;")
        except Exception:
            pressed = False
        if not pressed:
            time.sleep(handoff.poll_interval)
            continue

        try:
            note = (driver.execute_script(
                "var n=document.getElementById('__gomrok2_cap_note');"
                "return n?(n.value||''):'';") or "").strip()
        except Exception:
            note = ""

        step += 1
        _capture_snapshot(driver, step, note)
        try:
            driver.execute_script("window.__gomrok2_capture=false;")
            driver.execute_script(
                "var n=document.getElementById('__gomrok2_cap_note'); if(n) n.value='';")
        except Exception:
            pass
        handoff.set_status(f"✓ اطلاعات مرحله‌ی {step} ثبت شد. برو مرحله‌ی بعد.")


# ---------------------------------------------------------------------------
# tashkhis-e element-e LOADING kenar-e dokme-ye tayid + halghe-ye talash-e kod
# ---------------------------------------------------------------------------
# 1) ghabl az click hame-ye element-haye NAMAYAN ba data-g2vis alamat mikhorand.
# 2) ba'd az click har element-e namayan-e BEDUN-e in alamat = tazeh-zaher-shode.
_MARK_VIS_JS = r"""
  var all=document.querySelectorAll('*'), n=0;
  for(var i=0;i<all.length;i++){
    var e=all[i];
    if(e.id && (''+e.id).indexOf('__gomrok2')===0) continue;
    var v=!!(e.offsetParent!==null || (e.getClientRects && e.getClientRects().length));
    if(v){ e.setAttribute('data-g2vis','1'); n++; } else { e.removeAttribute('data-g2vis'); }
  }
  return n;
"""

_NEW_VIS_JS = r"""
  function cls(e){
    var c=e.className;
    if(c && c.baseVal!==undefined) c=c.baseVal;
    return (''+(c||'')).trim();
  }
  var b=document.querySelector('[wicketpath="%s"]') || document.getElementById('confirmeCode');
  var br=b?b.getBoundingClientRect():null;
  var bx=br?br.left+br.width/2:0, by=br?br.top+br.height/2:0;
  var out=[], all=document.querySelectorAll('*');
  for(var i=0;i<all.length && out.length<40;i++){
    var e=all[i];
    if(e.id && (''+e.id).indexOf('__gomrok2')===0) continue;
    if(e.closest && e.closest('#__gomrok2_bar')) continue;
    var v=!!(e.offsetParent!==null || (e.getClientRects && e.getClientRects().length));
    if(!v || e.hasAttribute('data-g2vis')) continue;
    var r=e.getBoundingClientRect();
    out.push({tag:e.tagName.toLowerCase(), id:e.id||'', cls:cls(e),
      wp:e.getAttribute('wicketpath')||'', src:(e.getAttribute('src')||'').slice(0,140),
      txt:((e.innerText||e.textContent||'').replace(/\s+/g,' ').trim()).slice(0,80),
      rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
      dist: br?Math.round(Math.sqrt(Math.pow(r.left+r.width/2-bx,2)+Math.pow(r.top+r.height/2-by,2))):-1,
      html:(e.outerHTML||'').slice(0,300)});
  }
  return out;
""" % (WP_CONFIRM_BTN)

_WATCH_INSTALL_JS = r"""
  function cls(e){
    var c=e.className;
    if(c && c.baseVal!==undefined) c=c.baseVal;
    return (''+(c||'')).trim();
  }
  function ours(n){
    if(!n) return true;
    if(n.id && (''+n.id).indexOf('__gomrok2')===0) return true;
    return !!(n.closest && n.closest('#__gomrok2_bar'));
  }
  window.__g2log=[];
  if(window.__g2mo){ try{ window.__g2mo.disconnect(); }catch(e){} }
  window.__g2mo=new MutationObserver(function(ms){
    for(var i=0;i<ms.length;i++){
      if(window.__g2log.length>500) return;
      var m=ms[i], t=m.target;
      if(ours(t)) continue;
      if(m.type==='attributes'){
        if(m.attributeName==='data-g2vis') continue;
        if(t===document.body && m.attributeName==='style') continue;
        window.__g2log.push({t:'attr', a:m.attributeName, tag:(t.tagName||'').toLowerCase(),
          id:t.id||'', cls:cls(t), wp:(t.getAttribute?t.getAttribute('wicketpath'):'')||'',
          old:(m.oldValue||''+''), now:((t.getAttribute?t.getAttribute(m.attributeName):'')||'').slice(0,120)});
      } else if(m.type==='childList'){
        for(var j=0;j<m.addedNodes.length;j++){
          var n=m.addedNodes[j];
          if(n.nodeType!==1 || ours(n)) continue;
          window.__g2log.push({t:'add', tag:n.tagName.toLowerCase(), id:n.id||'', cls:cls(n),
            wp:n.getAttribute('wicketpath')||'', src:(n.getAttribute('src')||'').slice(0,140),
            html:(n.outerHTML||'').slice(0,300)});
        }
        for(var k=0;k<m.removedNodes.length;k++){
          var d=m.removedNodes[k];
          if(d.nodeType!==1) continue;
          if(d.id && (''+d.id).indexOf('__gomrok2')===0) continue;
          window.__g2log.push({t:'del', tag:d.tagName.toLowerCase(), id:d.id||'', cls:cls(d),
            html:(d.outerHTML||'').slice(0,200)});
        }
      }
    }
  });
  window.__g2mo.observe(document.documentElement, {childList:true, subtree:true,
    attributes:true, attributeOldValue:true,
    attributeFilter:['style','class','src','hidden','disabled']});
  return true;
"""

_WATCH_READ_JS = "return window.__g2log||[];"
_WATCH_STOP_JS = "if(window.__g2mo){try{window.__g2mo.disconnect();}catch(e){}} return true;"

_LOADING_HINT = ("indicat", "load", "spin", "wait", "progress", "ajax", "busy", "throbber")


def _looks_like_loader(item):
    blob = " ".join(str(item.get(k) or "") for k in ("id", "cls", "wp", "src", "html")).lower()
    return any(h in blob for h in _LOADING_HINT)


def _describe(item):
    bits = [f"<{item.get('tag')}>"]
    for key, label in (("id", "id"), ("cls", "class"), ("wp", "wp"), ("src", "src")):
        if item.get(key):
            bits.append(f"{label}={item[key]}")
    if item.get("rect"):
        bits.append(f"rect={item['rect']}")
    if item.get("dist", -1) >= 0:
        bits.append(f"fasele-az-dokme={item['dist']}px")
    if item.get("txt"):
        bits.append(f":: {item['txt']}")
    return "  " + " | ".join(bits)


def _attempt_report(idx, code, verdict, info, loaders, others, mutations, busy_seen, elapsed):
    lines = [
        f"=== talash #{idx} | kod={code} ===",
        f"natije   : {verdict}",
        f"payam    : {info or '-'}",
        f"busy(ajax) didde shod: {busy_seen} | zaman: {elapsed:.2f}s",
        "",
        "--- KANDID-haye element-e LOADING (tazeh-namayan, shabih-e loader) ---",
    ]
    lines += [_describe(x) for x in loaders] or ["  (hich kodam)"]
    lines += ["", "--- baghie-ye element-haye TAZEH-NAMAYAN (nazdik-tarin be dokme aval) ---"]
    lines += [_describe(x) for x in others[:12]] or ["  (hich kodam)"]
    lines += ["", "--- log-e mutation (add/del/attr) ---"]
    for m in mutations[:60]:
        if m.get("t") == "attr":
            lines.append(f"  attr {m.get('a')} <{m.get('tag')}> id={m.get('id') or '-'} "
                         f"cls={m.get('cls') or '-'} wp={m.get('wp') or '-'} -> {m.get('now') or ''}")
        else:
            lines.append(f"  {m.get('t')}  <{m.get('tag')}> id={m.get('id') or '-'} "
                         f"cls={m.get('cls') or '-'} wp={m.get('wp') or '-'} "
                         f"{(m.get('html') or '')[:160]}")
    if len(mutations) > 60:
        lines.append(f"  ... ({len(mutations) - 60} radif-e digar)")
    return "\n".join(lines)


def _submit_code_watched(driver, code, idx):
    """Yek talash: por-kardan + click + rasad-e element-e loading ta payan-e AJAX."""
    for js in (_CLEAR_ALERT_JS, _MARK_VIS_JS, _WATCH_INSTALL_JS):
        try:
            driver.execute_script(js)
        except Exception as e:
            print(f">>> (amade-sazi-ye rasad: {e})")

    started = time.time()
    if not driver.execute_script(_FIRE_JS, code):
        return ("notfound", "field-e kod ya dokme-ye tayid peyda nashod.", [], [], [])

    seen, order = {}, []
    busy_seen = False
    verdict, info = "nochange", ""
    idle_since = None
    deadline = started + 25.0

    while time.time() < deadline:
        try:
            busy = bool(driver.execute_script(_BUSY_JS))
        except Exception:
            busy = False
        busy_seen = busy_seen or busy

        try:
            for item in driver.execute_script(_NEW_VIS_JS) or []:
                key = (item.get("tag"), item.get("id"), item.get("cls"), item.get("src"),
                       tuple(item.get("rect") or []))
                if key not in seen:
                    item["at"] = round(time.time() - started, 3)
                    seen[key] = item
                    order.append(item)
        except Exception:
            pass

        try:
            state = driver.execute_script(_READ_JS) or {}
        except Exception:
            state = {}
        alert = (state.get("alert") or "").strip()
        if alert:
            verdict, info = "wrong", alert
            break
        if state.get("formGone") and state.get("sentGone"):
            verdict, info = "success", state.get("url") or ""
            break

        # ba'd az payan-e AJAX kami sabr mikonim ta alert-e dir-res ham beresad.
        if busy_seen and not busy:
            idle_since = idle_since or time.time()
            if time.time() - idle_since > 2.0:
                break
        time.sleep(0.05)

    elapsed = time.time() - started
    try:
        mutations = driver.execute_script(_WATCH_READ_JS) or []
        driver.execute_script(_WATCH_STOP_JS)
    except Exception:
        mutations = []

    loaders = [x for x in order if _looks_like_loader(x)]
    others = sorted((x for x in order if x not in loaders),
                    key=lambda x: x.get("dist", 10 ** 6) if x.get("dist", -1) >= 0 else 10 ** 6)

    report = _attempt_report(idx, code, verdict, info, loaders, others, mutations, busy_seen, elapsed)
    print(report, flush=True)
    try:
        os.makedirs(_DBG_DIR, exist_ok=True)
        with open(os.path.join(_DBG_DIR, f"code_attempt_{idx:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(report)
    except Exception as e:
        print(f">>> (zakhire-ye gozaresh anjam nashod: {e})")
    return (verdict, info, loaders, others, mutations)


def run_code_attempts(driver, handoff, code, attempts):
    """Ba har feshar-e 'edame': ta `attempts` bar kod-ra mizanad va loading-ra rasad mikonad."""
    handoff.set_phase("capture")
    try:
        driver.execute_script("window.__gomrok2_cap_label=arguments[0];",
                              f"ادامه (ثبت کد {code}) ▶")
    except Exception:
        pass
    handoff.set_status(f"آماده‌ام. با زدن «ادامه» کد {code} تا {attempts} بار ثبت می‌شود.")
    handoff._ensure()
    print(f">>> [gomrok2] montazer-e feshar-e 'edame' hastam (kod={code}, hadaksar {attempts} talash).",
          flush=True)

    while True:
        handoff.set_phase("capture")
        handoff._ensure()
        try:
            pressed = driver.execute_script("return window.__gomrok2_capture===true;")
        except Exception:
            pressed = False
        if not pressed:
            time.sleep(handoff.poll_interval)
            continue
        try:
            driver.execute_script("window.__gomrok2_capture=false;")
        except Exception:
            pass

        for idx in range(1, attempts + 1):
            handoff.set_status(f"تلاش {idx} از {attempts} با کد {code} ...")
            handoff._ensure()
            verdict, info, _loaders, _others, _muts = _submit_code_watched(driver, code, idx)
            if verdict == "success":
                _capture_snapshot(driver, 90 + idx, f"success ba'd az talash {idx}")
                handoff.set_phase("done")
                handoff.set_status(f"کد در تلاش {idx} پذیرفته شد؛ صفحه عوض شد.")
                handoff._ensure()
                print(f">>> [gomrok2] MOVAFFAGH dar talash #{idx}. url={info}", flush=True)
                return "success"
            if verdict == "notfound":
                handoff.set_status("✗ فیلد کد یا دکمه‌ی تأیید پیدا نشد.")
                handoff._ensure()
                print(">>> [gomrok2] field/dokme peyda nashod; motevaghef shodam.", flush=True)
                return "notfound"
            handoff.set_status(f"✗ تلاش {idx}: {info or 'تغییری دیده نشد'}")
            handoff._ensure()
            time.sleep(0.8)

        _capture_snapshot(driver, 99, f"ba'd az {attempts} talash-e nakam")
        handoff.set_status(f"بعد از {attempts} تلاش هنوز خطا هست؛ ربات متوقف شد. مرورگر باز است.")
        handoff._ensure()
        print(f">>> [gomrok2] ba'd az {attempts} talash hanooz khata bud -> ROBOT STOP. "
              "morurgar baz mimanad.", flush=True)
        return "failed"


def _fast_report(code, res, elapsed):
    """Gozaresh-e yek dor-e FAST: natije + zaman-e pasokh va fasele-ye har talash.

    `code` mitavanad yek reshte (tak-kod) ya list-e kod-ha (donbale) bashad.
    """
    res = res or {}
    if isinstance(code, (list, tuple)):
        code_label = "->".join(str(c) for c in code)
    else:
        code_label = str(code)
    lines = [
        f"=== FAST retry | kod={code_label} ===",
        f"natije-ye nahayi : {res.get('result', '?')}",
        f"te'dad-e talash  : {res.get('total', '?')}",
        f"zaman-e kol      : {elapsed:.3f}s",
        "",
        "--- talash-ha (pasokh = zaman ta render-e natije | fasele = az payan-e talash-e ghabli) ---",
    ]
    atts = res.get("attempts") or []
    prev_t1 = None
    for a in atts:
        gap = ""
        if prev_t1 is not None and a.get("t0") is not None:
            gap = f" | fasele-az-ghabli={round(a['t0'] - prev_t1)}ms"
        kod = f"kod={a.get('code')} | " if a.get("code") is not None else ""
        extra = f" | {a.get('alert')}" if a.get("alert") else ""
        lines.append(f"  #{a.get('n')}: {kod}{a.get('result', '?')} | "
                     f"pasokh={a.get('ms', '?')}ms{gap}{extra}")
        prev_t1 = a.get("t1")
    if not atts:
        lines.append("  (hich talashi sabt nashod)")
    return "\n".join(lines)


def _parse_code_sequence(spec):
    """Reshte-ye donbale-ye kod-ha-ra be list-e per-talash tabdil mikonad.

    Format-ha (ba ',' ya faasele joda mishavand):
      "6323,6323,6323,6324,6324"  -> ['6323','6323','6323','6324','6324']
      "6323*3,6324*2"             -> ['6323','6323','6323','6324','6324']
      "6323 6324"                 -> ['6323','6324']
    """
    if not spec:
        return []
    out = []
    for tok in spec.replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        if "*" in tok:
            code, _, cnt = tok.partition("*")
            code = code.strip()
            try:
                repeat = int(cnt.strip())
            except ValueError:
                repeat = 1
            if code and repeat > 0:
                out.extend([code] * repeat)
        else:
            out.append(tok)
    return out


def _choose_codeflow(args):
    """Entekhab-e masir-e kod (yeksan baraye halat-e --attach va halat-e panel).

    barmigardanad: ('diagnose'|'fast'|'manual', codes-list).
    - --manual  -> vorud-e DASTI (run_code_entry): kod az noar gerefte mishavad.
    - --diagnose + kod -> halghe-ye tashkhis (run_code_attempts).
    - --code (tanha) -> fast ba tekrar-e hamon yek kod.
    - pishfarz -> plan-e baked (DEFAULT_CODE_PLAN) ya --codes; NOAR kod nemigirad.
    """
    if getattr(args, "manual", False):
        return ("manual", [])
    if args.diagnose and (args.codes or args.code):
        code = args.code or (_parse_code_sequence(args.codes) or [""])[0]
        return ("diagnose", [code])
    if args.code and not args.codes:
        return ("fast", [args.code] * max(1, args.attempts))
    return ("fast", _parse_code_sequence(args.codes or DEFAULT_CODE_PLAN))


def _run_codeflow(driver, handoff, args):
    """Masir-e entekhab-shode-ra ejra mikonad va outcome barmigardanad
    ('success'|'failed'|'notfound'|'error'|None). None ya'ni masir-e halghe-i
    (manual/diagnose) ke khodash motevaghef nemishavad."""
    mode, codes = _choose_codeflow(args)
    if mode == "manual":
        run_code_entry(driver, handoff)
        return None
    if mode == "diagnose":
        run_code_attempts(driver, handoff, codes[0] if codes else "", args.attempts)
        return None
    return run_code_fast(driver, handoff, codes, gap_ms=args.gap_ms)


def run_code_fast(driver, handoff, codes, attempts=None, gap_ms=0):
    """Ba har 'edame': halghe-ye retry-ye KAMTARIN-TAAKHIR-ra darun-e morurgar ejra
    mikonad. bein-e didan-e khata va shellik-e ba'di HICH round-trip-e Python nist.

    `codes`: list-e kod baraye har talash (masalan ['6323','6323','6323','6324','6324']).
    yek reshte-ye tanha ham qabool ast (barmigardad be tekrar).
    """
    if isinstance(codes, str):
        codes = [codes]
    codes = [str(c).strip() for c in codes if str(c).strip() != ""]
    if not codes:
        print(">>> [gomrok2] FAST: hich kodi dade nashod; motevaghef.", flush=True)
        return "notfound"
    if not attempts or attempts < len(codes):
        attempts = len(codes)
    # agar attempts > te'dad-e kod-ha bud, akharin kod tekrar mishavad (JS ham hamin kar-ra mikonad).
    seq = "->".join(codes)
    handoff.set_phase("capture")
    try:
        # noar kod-ra NEMIGIRAD/NEMINEVISAD; plan darun-e file baked shode.
        driver.execute_script("window.__gomrok2_cap_label=arguments[0];", "ادامه ▶")
    except Exception:
        pass
    handoff.set_status(
        f"آماده‌ام. با «ادامه» پلن ({attempts} تلاش) با کمترین تأخیر اجرا می‌شود.")
    handoff._ensure()
    print(f">>> [gomrok2] FAST: montazer-e feshar-e 'edame' (donbale={seq}, "
          f"{attempts} talash, gap={gap_ms}ms).", flush=True)

    while True:
        handoff.set_phase("capture")
        handoff._ensure()
        try:
            pressed = driver.execute_script("return window.__gomrok2_capture===true;")
        except Exception:
            pressed = False
        if not pressed:
            time.sleep(handoff.poll_interval)
            continue
        try:
            driver.execute_script("window.__gomrok2_capture=false;")
        except Exception:
            pass

        handoff.set_status("در حال اجرای پلن (حالت سریع) ...")
        handoff._ensure()
        # bishtarin zaman: har talash ~2.5s worst-case (tmr timeout) + gap.
        budget = attempts * (2.5 + gap_ms / 1000.0) + 5.0
        try:
            driver.set_script_timeout(budget)
        except Exception:
            pass
        started = time.time()
        try:
            res = driver.execute_async_script(_FAST_RETRY_JS, codes, attempts, int(gap_ms),
                                              BAD_CODE_TEXT)
        except Exception as e:
            handoff.set_status(f"✗ اجرای حالت سریع خطا داد: {type(e).__name__}")
            handoff._ensure()
            print(f">>> [gomrok2] FAST khata: {type(e).__name__}: "
                  f"{str(e).splitlines()[0]}", flush=True)
            return "error"
        elapsed = time.time() - started

        report = _fast_report(codes, res, elapsed)
        print(report, flush=True)
        try:
            os.makedirs(_DBG_DIR, exist_ok=True)
            with open(os.path.join(_DBG_DIR, "code_fast_report.txt"), "w", encoding="utf-8") as f:
                f.write(report)
        except Exception as e:
            print(f">>> (zakhire-ye gozaresh anjam nashod: {e})")

        result = (res or {}).get("result")
        if result == "success":
            _capture_snapshot(driver, 90, "success (fast)")
            handoff.set_phase("done")
            handoff.set_status("کد پذیرفته شد؛ صفحه عوض شد.")
            handoff._ensure()
            print(">>> [gomrok2] FAST MOVAFFAGH.", flush=True)
            return "success"
        if result == "notfound":
            handoff.set_status("✗ فیلد کد یا دکمه‌ی تأیید پیدا نشد.")
            handoff._ensure()
            print(">>> [gomrok2] FAST: field/dokme peyda nashod; motevaghef shodam.", flush=True)
            return "notfound"

        _capture_snapshot(driver, 99, f"ba'd az {attempts} talash-e fast")
        handoff.set_status(
            f"بعد از {attempts} تلاش سریع هنوز خطا هست؛ ربات متوقف شد. مرورگر باز است.")
        handoff._ensure()
        print(f">>> [gomrok2] FAST: ba'd az {attempts} talash hanooz khata bud -> ROBOT STOP. "
              "morurgar baz mimanad.", flush=True)
        return "failed"


# ---------------------------------------------------------------------------
def _build_desktop_driver():
    """Driver-e Chrome-e DESKTOP baraye gomrok2 (FAGHAT injaست; captcha_collector va
    gomrok-e site-1 dast-nakhorde va rooye halat-e mobile mimanand).
    hich mobileEmulation-i nadarad; panjere maximize mishavad va UA-ye desktop midahad."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service as ChromeService

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--start-maximized")
    options.add_argument(f"--user-agent={ua}")

    path = cc.resolve_chromedriver()
    if path and os.path.isfile(path):
        return webdriver.Chrome(service=ChromeService(executable_path=path), options=options)
    return webdriver.Chrome(options=options)


# ---------------------------------------------------------------------------
def _attach_driver(address):
    """Be Chrome-e dar-hal-e-ejra vasl mishavad (bedun-e baz/baste kardan-e morurgar)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service as ChromeService

    options = Options()
    options.add_experimental_option("debuggerAddress", address)
    path = cc.resolve_chromedriver()
    if path and os.path.isfile(path):
        return webdriver.Chrome(service=ChromeService(executable_path=path), options=options)
    return webdriver.Chrome(options=options)


def _pick_tab(driver, needle=""):
    """Tab-e vaghe'i (gheyr-e devtools) ra entekhab mikonad; ba needle filter mishavad."""
    chosen = None
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            url, title = driver.current_url or "", driver.title or ""
        except Exception:
            continue
        if url.startswith("devtools://"):
            continue
        chosen = (handle, url, title)
        if needle and (needle in url or needle in title):
            break
    if not chosen:
        raise RuntimeError("hich tab-e ghabel-e estefade-i peyda nashod.")
    driver.switch_to.window(chosen[0])
    print(f">>> [gomrok2] tab entekhab shod: {chosen[2]!r} | {chosen[1]}", flush=True)
    return chosen


def run_after_continue(driver, handoff, debug=False):
    """Hook-e ba'd az 'edame': ya snapshot-e debug ya halghe-ye vorud-e dasti-ye kod."""
    if debug:
        run_debug_capture(driver, handoff)
    else:
        run_code_entry(driver, handoff)


# ---------------------------------------------------------------------------
def main(argv=None):
    global _CLAIMED_PROFILE_ID, _CLAIMED_RUNNER_ID
    ensure_utf8_stdio()
    system_debug.configure("gomrok-site2")
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--debug", action="store_true",
                        help="halghe-ye snapshot: har 'edame' -> zakhire-ye HTML/screenshot/kholase")
    parser.add_argument("--no-panel", action="store_true",
                        help="bedun-e claim-e profile az panel ejra kon (baraye debug)")
    parser.add_argument("--attach", metavar="HOST:PORT",
                        help="be Chrome-e baz vasl sho (debuggerAddress) va morurgar-ra nabann")
    parser.add_argument("--tab", default="",
                        help="bakhshi az URL/onvan-e tab-e hadaf dar halat-e --attach")
    parser.add_argument("--navigate", action="store_true",
                        help="dar halat-e --attach, tab-e entekhab-shode ra be --url bebar")
    parser.add_argument("--code", help="kod-e payamaki-ye moshakhas ke bayad sabt shavad")
    parser.add_argument("--codes",
                        help="donbale-ye kod baraye har talash (FAST). masalan "
                             "'6323*3,6324*2' ya '6323,6323,6323,6324,6324'")
    parser.add_argument("--attempts", type=int, default=5,
                        help="te'dad-e talash ba --code (pishfarz 5)")
    parser.add_argument("--gap-ms", type=int, default=0,
                        help="fasele-ye ekhtiari bein-e talash-ha bar hasb-e ms "
                             "(pishfarz 0 = kamtarin-taakhir)")
    parser.add_argument("--diagnose", action="store_true",
                        help="be jaye halat-e sari', halghe-ye tashkhis-e loading-ra ejra kon")
    parser.add_argument("--manual", action="store_true",
                        help="vorud-e DASTI-ye kod az noar (be jaye plan-e baked)")
    args = parser.parse_args(argv)

    # dar halat-e debug/attach ta zamani ke profile-e moshakhas nadadi, panel-ra dast nemizanim.
    use_panel = not (args.no_panel or args.attach or (args.debug and not args.profile_id))
    title = "سایت دوم"
    if use_panel:
        claimed = _claim_panel_profile(args.profile_id)
        if not claimed:
            print(">>> [gomrok2] profile-e azad baraye ejra vojood nadarad.")
            return
        _CLAIMED_PROFILE_ID = claimed.get("id", "")
        _CLAIMED_RUNNER_ID = claimed.get("runner_id", "")
        _CLAIMED_NATIONAL = str(claimed.get("national") or "").strip()
        title = str(claimed.get("title") or claimed.get("national") or "سایت دوم").strip()
        _start_profile_heartbeat(_CLAIMED_PROFILE_ID, _CLAIMED_RUNNER_ID)
        atexit.register(_release_panel_profile, _CLAIMED_PROFILE_ID, _CLAIMED_RUNNER_ID)
        print(f">>> [gomrok2] profile claim shod: {title!r} | status=BUSY")
    else:
        print(">>> [gomrok2] bedun-e claim-e profile ejra mishavad (--debug/--no-panel).")

    driver = None
    try:
        if args.attach:
            # morurgar-e baz: hich vaght baste/navigate nemishavad.
            driver = _attach_driver(args.attach)
            _pick_tab(driver, args.tab)
            if args.navigate:
                print(f">>> [gomrok2] navigate be: {args.url}", flush=True)
                try:
                    driver.get(args.url)
                except Exception as e:
                    print(f">>> [gomrok2] navigate nashod: {type(e).__name__}: "
                          f"{str(e).splitlines()[0]}", flush=True)
                print(f">>> [gomrok2] URL-e fe'li: {driver.current_url}", flush=True)
            handoff = ManualHandoff(driver, title=title)
            _capture_snapshot(driver, 1, "vaziat-e fe'li-ye tab (attach)")
        else:
            # morurgar-e khod-e ma (panel/standalone): DESKTOP (na mobile). baz mishavad
            # va dar payan baste. site-1/captcha_collector dast-nakhorde mimanand.
            driver = _build_desktop_driver()
            driver.get(args.url)
            print(f">>> [gomrok2] safhe baz shod: {args.url}", flush=True)
            handoff = ManualHandoff(driver, title=title)

        # --- masir-e moshtarak: halat-e --debug snapshot, vagarna entekhab-e codeflow.
        #     panel (bedun-e --attach/--codes) -> plan-e baked mesl-e halat-e attach.
        if args.debug:
            handoff.wait_continue()
            run_after_continue(driver, handoff, debug=True)
            outcome = None
        else:
            outcome = _run_codeflow(driver, handoff, args)

        # --- negahdari-ye morurgar ba'd az payan-e codeflow ---
        # dar HAME-ye halat-ha robot khamoosh mishavad; morurgar hichvaght baste
        # NEMISHAVAD (hich timeout-i nadarad) — user khodash har vaght khast
        # dasti mibandad.
        if outcome in ("failed", "notfound", "error"):
            print(">>> [gomrok2] plan javab nadad; robot khamoosh mishavad, "
                  "morurgar baz mimanad (baste-shodan dasti).", flush=True)
            try:
                handoff.set_status("پلن جواب نداد. مرورگر باز می‌ماند — خودت دستی ببند.")
                handoff._ensure()
            except Exception:
                pass
        else:
            if outcome == "success":
                # movaffaghiat: notif + log dar panel-e site2. morurgar baz mimanad
                # ta banner-e 'done' payda bashad va user dasti bebandad.
                national = _read_captured_national(driver) or _CLAIMED_NATIONAL
                _notify_site2_success(national)
                print(">>> [gomrok2] movaffagh; robot khamoosh mishavad, "
                      "morurgar baz mimanad (baste-shodan dasti).", flush=True)
            else:
                # masir-e halghe-i (manual/diagnose): morurgar baz mimanad.
                print(">>> [gomrok2] robot khamoosh mishavad; morurgar baz "
                      "mimanad (baste-shodan dasti).", flush=True)
    finally:
        # morurgar hichvaght baste NEMISHAVAD — hatta morurgar-e khod-e ma.
        # user khodash dasti mibandad; robot faghat claim-e profile-ra azad mikonad.
        _release_panel_profile(_CLAIMED_PROFILE_ID, _CLAIMED_RUNNER_ID)


if __name__ == "__main__":
    main()
