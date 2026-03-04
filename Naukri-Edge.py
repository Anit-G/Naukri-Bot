import pandas as pd
import time
import json
import os
import re
from selenium import webdriver
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys



firstname=''                        #Add your LastName
lastname=''                         #Add your FirstName
joblink=[]                          #Initialized list to store links
maxcount=100                         #Max daily apply quota for Naukri
keywords=['']                    #Add you list of role you want to apply comma seperated
location = ''                       #Add your location/city name for within India or remote
applied =0                          #Count of jobs applied sucessfully
failed = 0                          #Count of Jobs failed
applied_list={
    'passed':[],
    'failed':[]
}                                   #Saved list of applied and failed job links for manual review
answers_store_path = 'answers_store.json'
edgedriverfile = r'''filepath'''  #Please add your filepath here
yournaukriemail = ''
yournaukripass = ''


def normalize_question_text(text):
    return re.sub(r'\s+', ' ', (text or '')).strip().lower()


def load_answers(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_answers(path, data):
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _extract_question_text(question_block):
    text = question_block.text.strip()
    if text:
        return text.split('\n')[0].strip()
    return ''


def _extract_radio_options(question_block):
    options = {}
    radios = question_block.find_elements(By.XPATH, ".//input[@type='radio']")
    for radio in radios:
        label_text = ''
        clickable_element = radio
        radio_id = radio.get_attribute('id')
        if radio_id:
            labels = question_block.find_elements(By.XPATH, f".//label[@for='{radio_id}']")
            if labels:
                clickable_element = labels[0]
                label_text = labels[0].text.strip()
        if not label_text:
            parent_labels = radio.find_elements(By.XPATH, "./ancestor::label[1]")
            if parent_labels:
                clickable_element = parent_labels[0]
                label_text = parent_labels[0].text.strip()
        if not label_text:
            sibling_labels = radio.find_elements(By.XPATH, "./following-sibling::label[1]")
            if sibling_labels:
                clickable_element = sibling_labels[0]
                label_text = sibling_labels[0].text.strip()
        if not label_text:
            label_text = (radio.get_attribute('value') or '').strip()
        if label_text:
            options[normalize_question_text(label_text)] = {
                'label': label_text,
                'element': clickable_element
            }
    return options


def _get_answer_for_question(answers_data, question_text, options_map):
    normalized_question = normalize_question_text(question_text)
    saved = answers_data.get(normalized_question)
    if saved:
        saved_answer = saved.get('answer', '').strip()
        if options_map:
            if normalize_question_text(saved_answer) in options_map:
                print(f"Using saved answer for question: {question_text}")
                return saved_answer
            print(f"Saved answer '{saved_answer}' does not match available options for: {question_text}")
        else:
            print(f"Using saved answer for question: {question_text}")
            return saved_answer

    while True:
        if options_map:
            print(f"\nQuestion: {question_text}")
            print('Available options:', ', '.join(option['label'] for option in options_map.values()))
        typed_answer = input(f"Enter answer for '{question_text}': ").strip()
        if not typed_answer:
            print('Answer cannot be empty. Please try again.')
            continue
        if options_map and normalize_question_text(typed_answer) not in options_map:
            print('Typed answer does not match available options. Please try again.')
            continue
        answers_data[normalized_question] = {
            'question': question_text,
            'answer': typed_answer
        }
        save_answers(answers_store_path, answers_data)
        return typed_answer


def fill_screening_questions(driver, answers_data):
    question_blocks = driver.find_elements(
        By.XPATH,
        "//*[contains(@class,'question') or contains(@class,'Question') or contains(@class,'ques')]"
    )
    handled_questions = set()
    for question_block in question_blocks:
        question_text = _extract_question_text(question_block)
        normalized_question = normalize_question_text(question_text)
        if not normalized_question or normalized_question in handled_questions:
            continue
        handled_questions.add(normalized_question)
        radio_options = _extract_radio_options(question_block)
        answer = _get_answer_for_question(answers_data, question_text, radio_options)
        if radio_options:
            radio_options[normalize_question_text(answer)]['element'].click()
            continue
        text_inputs = question_block.find_elements(
            By.XPATH,
            ".//textarea | .//input[not(@type='hidden') and not(@type='radio') and not(@type='checkbox')]"
        )
        if text_inputs:
            text_inputs[0].clear()
            text_inputs[0].send_keys(answer)


answers_data = load_answers(answers_store_path)

try:

    driver = webdriver.Edge(edgedriverfile)
    driver.get('https://login.naukri.com/')
    uname=driver.find_element(By.ID, 'usernameField')
    uname.send_keys(yournaukriemail)
    passwd=driver.find_element(By.ID, 'passwordField')
    passwd.send_keys(yournaukripass)
    passwd.send_keys(Keys.ENTER)

except Exception as e:
    print('Webdriver exception')
time.sleep(10)
for k in keywords:
    for i in range(2):
        if location=='':
            url = "https://www.naukri.com/"+k.lower().replace(' ','-')+"-"+str(i+1)
        else: url = "https://www.naukri.com/"+k.lower().replace(' ','-')+"-jobs-in-"+location.lower().replace(' ','-')+"-"+str(i+1)
        driver.get(url)
        print(url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source,'html5lib')
        results = soup.find(class_='list')
        job_elems = results.find_all('article',class_='jobTuple bgWhite br4 mb-8')
        for job_elem in job_elems:
            joblink.append(job_elem.find('a',class_='title fw500 ellipsis').get('href'))


for i in joblink:
    time.sleep(3)
    driver.get(i)   
    if applied <=maxcount:
        try:
            time.sleep(3)
            driver.find_element_by_xpath("//*[text()='Apply']").click()
            time.sleep(2)
            fill_screening_questions(driver, answers_data)
            applied +=1
            applied_list['passed'].append(i)
            print('Applied for ',i, " Count", applied)

        except Exception as e: 
            failed+=1
            applied_list['failed'].append(i)
            print(e, "Failed " ,failed)
        try:    
            if driver.find_element_by_xpath("//*[text()='Your daily quota has been expired.']"):
                print('MAX Limit reached closing browser')
                driver.close()
                break
            if driver.find_element_by_xpath("//*[text()=' 1. First Name']"):
                driver.find_element_by_xpath("//input[@id='CUSTOM-FIRSTNAME']").send_keys(firstname)
            if driver.find_element_by_xpath("//*[text()=' 2. Last Name']"):
                driver.find_element_by_xpath("//input[@id='CUSTOM-LASTNAME']").send_keys(lastname);
            if driver.find_element_by_xpath("//*[text()='Submit and Apply']"):
                driver.find_element_by_xpath("//*[text()='Submit and Apply']").click()
        except:
            pass
            
    else:
        driver.close()
        break
print('Completed applying closing browser saving in applied jobs csv')
try:
    driver.close()
except:pass
csv_file = "naukriapplied.csv"
final_dict= dict ([(k, pd.Series(v)) for k,v in applied_list.items()])
df = pd.DataFrame.from_dict(final_dict)
df.to_csv(csv_file, index = False)
