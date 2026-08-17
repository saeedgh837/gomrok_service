"""Live browser check for profile device inquiry UI without spawning robots."""
import json
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8763
URL = f"http://127.0.0.1:{PORT}/site-1?check={time.time()}"


def driver(width, height):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={width},{height}")
    return webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)


def check(width, height):
    d = driver(width, height)
    try:
        d.get(URL)
        WebDriverWait(d, 20).until(lambda x: x.execute_script("return document.readyState") == "complete")
        d.execute_script("editProfile('idle1'); showProfileSection('inquiries');")
        WebDriverWait(d, 10).until(lambda x: x.find_element("id", "device-inquiry-card"))
        d.execute_script("""
          window.__inquiryCall=null;
          var original=window.fetch;
          window.fetch=function(url,options){
            if(String(url)==='/profile-device-inquiry'){
              window.__inquiryCall={url:String(url),body:JSON.parse(options.body)};
              return Promise.resolve(new Response(JSON.stringify({ok:true,pid:1234,service:window.__inquiryCall.body.service}),{status:200,headers:{'Content-Type':'application/json'}}));
            }
            if(String(url)==='/state') return new Promise(function(){});
            return original.apply(this,arguments);
          };
        """)
        initial = d.execute_script("""
          var c=document.getElementById('device-inquiry-card');
          return {buttons:Array.from(c.querySelectorAll('.inquiry-picker button')).map(b=>b.textContent.trim()),
                  active:c.querySelector('.inquiry-picker .active').textContent.trim(),
                  overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,
                  results:c.querySelectorAll('.device-result').length};
        """)
        assert initial["buttons"] == ["استعلام همتا", "استعلام گمرک", "استعلام گمرک و همتا"], initial
        assert initial["active"] == "استعلام همتا" and initial["results"] >= 1, initial
        assert not initial["overflow"], initial
        d.execute_script("selectProfileInquiry('all'); document.getElementById('profile-inquiry-start').click();")
        WebDriverWait(d, 5).until(lambda x: x.execute_script("return window.__inquiryCall!==null"))
        call = d.execute_script("return window.__inquiryCall")
        assert call == {"url": "/profile-device-inquiry", "body": {"id": "idle1", "service": "all"}}, call
        active = d.execute_script("return document.querySelector('.inquiry-picker .active').textContent.trim()")
        assert active == "استعلام گمرک و همتا", active
        return {"viewport": [width, height], "buttons": initial["buttons"], "payload": call,
                "horizontal_overflow": initial["overflow"]}
    finally:
        d.quit()


print(json.dumps([check(1440, 1000), check(430, 900)], ensure_ascii=False))
