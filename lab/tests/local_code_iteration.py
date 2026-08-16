from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

PAGE = Path(__file__).with_name("page.html").resolve()
ERROR_TEXT = "کد وارد شده با کد ارسالی مطابقت ندارد."
TEST_CODES = range(1001, 1005)


def install_local_response_simulator(driver):
    """Give the static snapshot deterministic responses; it has no Wicket backend."""
    driver.execute_script(
        """
        const input = document.getElementById('id13');
        const button = document.getElementById('confirmeCode');
        const error = document.getElementById('id14');
        if (!input || !button || !error) {
            throw new Error('Required confirmation-code elements are missing');
        }
        button.onclick = () => {
            error.textContent = input.value === '1004' ? '' : arguments[0];
            window.__iterationResponse = Number(input.value);
        };
        """,
        ERROR_TEXT,
    )


def main():
    if not PAGE.is_file():
        raise FileNotFoundError(PAGE)

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(
        service=Service(r"C:\Users\Saeed\ai_projects\chromedriver.exe"),
        options=options,
    )
    wait = WebDriverWait(driver, 2)

    try:
        driver.get(PAGE.as_uri())
        install_local_response_simulator(driver)

        for code in TEST_CODES:
            field = wait.until(lambda d: d.find_element(By.ID, "id13"))
            field.clear()
            field.send_keys(str(code))
            driver.execute_script("window.__iterationResponse = null")
            driver.find_element(By.ID, "confirmeCode").click()

            wait.until(
                lambda d: d.execute_script("return window.__iterationResponse")
                == code
            )
            try:
                wait.until(
                    lambda d: ERROR_TEXT
                    in d.find_element(By.ID, "id14").text.strip()
                )
                print(f"Code {code} failed.")
            except TimeoutException:
                print(f"Success! Code {code} was accepted.")
                break
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
