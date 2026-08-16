"""
Config-e site-e hamta.ntsw.ir (safhe-ye login).

Har site yek file-e config-e joda dar poushe-ye `sites/` darad.
Meghdar-ha inja tarif mishavand va captcha_collector.py an-ha ra load mikonad.
"""

# Address-e safhe-i ke captcha roo-ye an ast
URL = "https://hamta.ntsw.ir/Account/Login?ReturnUrl=%2F"

# Poushe-ye khorooji baraye zakhire-ye aks-ha-ye in site
OUTPUT_DIR = "captchas"

# Selector-e CSS-e tasvir-e captcha
#   <img class="captcha-image forgetcaptcha" ...>
CAPTCHA_CSS = "img.captcha-image"

# Selector-e CSS-e dokme/link-e refresh-e captcha
#   <a class="refresh-captcha" href="#">
REFRESH_CSS = "a.refresh-captcha"
