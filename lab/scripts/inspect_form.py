"""
inspect_form.py
Site-e irica ra baz mikonad (haman collector), SABR mikonad ta captcha-ye
VAGHE'I (na kadr-e khali) load shavad — chon ta captcha-ye salem nayad form
naghes ast — sepas SAKHTAR-E FORM ra dump mikonad dar form_dump.json.

Faghat MOSHAHEDE mikonad — hich chiz submit ya por nemishavad.

    python inspect_form.py
"""
import json
import time

import captcha_collector as cc


JS = r"""
function info(el){
  const labelFor = (el.id) ? document.querySelector('label[for="'+el.id+'"]') : null;
  let near = '';
  let p = el.closest('div,td,li,tr,fieldset');
  if (p) near = (p.innerText||'').trim().slice(0,80);
  const opts = [];
  if (el.tagName.toLowerCase()==='select'){
    for (const o of el.options) opts.push({value:o.value, text:(o.text||'').trim()});
  }
  return {
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type')||'',
    id: el.id||'',
    name: el.getAttribute('name')||'',
    wicketpath: el.getAttribute('wicketpath')||'',
    placeholder: el.getAttribute('placeholder')||'',
    maxlength: el.getAttribute('maxlength')||'',
    value: el.value||'',
    required: el.required||false,
    visible: !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
    label: labelFor ? (labelFor.innerText||'').trim() : '',
    near: near,
    options: opts
  };
}
const els = [...document.querySelectorAll('input,select,textarea,button,a[wicketpath]')];
return JSON.stringify(els.map(info));
"""


def dump_form(driver, tag):
    data = json.loads(driver.execute_script(JS))
    out = f"form_dump_{tag}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # khode-ye field-ha ra ham (bedun-e chap-e farsi dar console) shomaresh kon
    n_vis = sum(1 for e in data if e["visible"])
    print(f">>> [{tag}] {len(data)} element ({n_vis} visible) -> {out}")
    return data


def main():
    ua = cc.USER_AGENTS[0]
    print(">>> Baz kardan-e site ...")
    driver, wait = cc.open_site(ua)

    # SABR ta captcha-ye VAGHE'I amade shavad (reload-e safhe agar khali bood).
    # ta captcha-ye salem nayad, form naghes ast.
    print(">>> Sabr baraye captcha-ye salem (ensure_real_captcha) ...")
    captcha = cc.ensure_real_captcha(driver, wait, 0.8)
    # motmaen shim ke vaghe'an por ast (na khali)
    empty = cc.is_captcha_empty(driver, captcha)
    print(f">>> captcha empty? {empty}")

    # kami sabr-e ezafe ta Wicket kamel field-ha ra render konad
    time.sleep(2)
    data = dump_form(driver, "ready")

    print(">>> Panjere 25 saniye baz mimanad baraye moshahede-ye cheshmi ...")
    time.sleep(25)
    driver.quit()


if __name__ == "__main__":
    main()
