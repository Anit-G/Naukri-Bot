import argparse
import pandas as pd
from selenium import webdriver
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from delay_utils import human_delay, maybe_cooldown


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
edgedriverfile = r'''filepath'''  #Please add your filepath here
yournaukriemail = ''
yournaukripass = ''

parser = argparse.ArgumentParser(description='Naukri Edge auto-apply with human-like delays.')
parser.add_argument('--min-delay', type=float, default=2.0, help='Minimum random delay in seconds.')
parser.add_argument('--max-delay', type=float, default=4.0, help='Maximum random delay in seconds.')
parser.add_argument('--cooldown-every', type=int, default=0, help='Take a longer break every N successful applies (0 disables).')
parser.add_argument('--cooldown-min-delay', type=float, default=15.0, help='Minimum cooldown delay in seconds.')
parser.add_argument('--cooldown-max-delay', type=float, default=30.0, help='Maximum cooldown delay in seconds.')
args = parser.parse_args()

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

human_delay(args.min_delay, args.max_delay, 'initial login warm-up')
for k in keywords:
    for i in range(2):
        human_delay(args.min_delay, args.max_delay, 'between result pages')
        if location=='':
            url = "https://www.naukri.com/"+k.lower().replace(' ','-')+"-"+str(i+1)
        else:
            url = "https://www.naukri.com/"+k.lower().replace(' ','-')+"-jobs-in-"+location.lower().replace(' ','-')+"-"+str(i+1)
        driver.get(url)
        print(url)
        human_delay(args.min_delay, args.max_delay, 'allow results page to load')
        soup = BeautifulSoup(driver.page_source,'html5lib')
        results = soup.find(class_='list')
        job_elems = results.find_all('article',class_='jobTuple bgWhite br4 mb-8')
        for job_elem in job_elems:
            joblink.append(job_elem.find('a',class_='title fw500 ellipsis').get('href'))


for i in joblink:
    human_delay(args.min_delay, args.max_delay, 'between jobs')
    driver.get(i)
    if applied <=maxcount:
        try:
            human_delay(args.min_delay, args.max_delay, 'before clicking Apply')
            driver.find_element_by_xpath("//*[text()='Apply']").click()
            human_delay(args.min_delay, args.max_delay, 'post apply click settle')
            applied +=1
            applied_list['passed'].append(i)
            print('Applied for ',i, " Count", applied)
            maybe_cooldown(applied, args.cooldown_every, args.cooldown_min_delay, args.cooldown_max_delay)

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
                driver.find_element_by_xpath("//input[@id='CUSTOM-LASTNAME']").send_keys(lastname)
            if driver.find_element_by_xpath("//*[text()='Submit and Apply']"):
                human_delay(args.min_delay, args.max_delay, 'before submitting additional answers')
                driver.find_element_by_xpath("//*[text()='Submit and Apply']").click()
        except:
            pass

    else:
        driver.close()
        break
print('Completed applying closing browser saving in applied jobs csv')
try:
    driver.close()
except:
    pass
csv_file = "naukriapplied.csv"
final_dict= dict ([(k, pd.Series(v)) for k,v in applied_list.items()])
df = pd.DataFrame.from_dict(final_dict)
df.to_csv(csv_file, index = False)
