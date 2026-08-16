"""
Config-e site-e epl.irica.gov.ir (safhe-ye sabt-e IMEI).

Har site yek file-e config-e joda dar poushe-ye `sites/` darad.
Meghdar-ha inja tarif mishavand va captcha_collector.py an-ha ra load mikonad.
"""

# Address-e safhe-i ke captcha roo-ye an ast
URL = "https://epl.irica.gov.ir/ImeiRegister?1"

# Poushe-ye khorooji baraye zakhire-ye aks-ha-ye in site
# (joda az hamta ta aks-ha ghati nashavand)
OUTPUT_DIR = "captchas_irica"

# Selector-e CSS-e tasvir-e captcha
#   <img class="captchaImage" wicketpath="..._capchaImage" ...>
CAPTCHA_CSS = "img.captchaImage"

# Selector-e CSS-e dokme/link-e refresh-e captcha.
# In site az class-e sabet baraye link-e refresh estefade nemikonad, pas az
# attribute-e paydar-e `wicketpath` estefade mikonim:
#   <a wicketpath="form_imeiStep_captchaPanel_wmc_refreshCapcha" href="javascript:;">
REFRESH_CSS = 'a[wicketpath="form_imeiStep_captchaPanel_wmc_refreshCapcha"]'

# --- Tashkhis-e captcha-ye KHALI (in site ba avvalin voroud/ba'zi reload-ha
#     captcha ra namayesh nemidahad va faghat yek kadr-e sefid ya aks-e shekaste
#     neshan midahad) ---
# Meyar: nesbat-e pixel-ha-ye TIRE (matn-e captcha) dar tasvir-e boride-shode.
#   - captcha-ye VAGHE'I: ~0.19 ta 0.24 (kadr por az harf va khat-e ghermez ast)
#   - kadr-e KHALI       : ~0.12 (faghat hashiye-ye nazok)
#   - aks-e shekaste     : ~0.07
# Har chizi ba nesbat-e KAMTAR az in astane "khali" telaghi mishavad -> refresh.
MIN_DARK_RATIO = 0.15
