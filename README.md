# Naukri-automatic-job-apply-bot
Automation that applies to jobs on Naukri.com easily for faster job hunting and it uses playwright package for browser automation.

## Steps to run the app done only once at the very start
- run the NaurkiLogin.py
- manually login into the naukri login page that has been created by the script 
- press enter in the shell that you used to run the python script

## Steps to take during normal operations
- open naukri_playwright_bot.py and set the values for MAX_INDEX_PAGE, MAX_EXPIERENCE and JOB_POSTING_KEYWORDS
- run naukri_playwright_bot.py
- keep an eye on the shell that is running the script and actively logging everything
- Naukri has a chatbot system that asks you questions I keep a store of said question so you only need to trouble yourself of answering them just once via the shell. The bot will stop the process and wait for you to answer and say that answer for future use.

## Steps to take for application on recommended jobs section
- Run naukri_recommended_apply.py
- enjoy