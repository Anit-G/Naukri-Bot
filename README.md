# Naukri-automatic-job-apply-bot
Automation that applies to jobs on Naukri.com easily for fatster job hunting and it uses Selenium package for browser automation.

## Steps to run the Mozilla file

- Open/Download Mozilla browser
- Open a new tab and type about:profiles
- Create a new profile and launch the new profile
- Login into Naukri.com into the new profile.
- goto about:profiles and Copy the path of the Root Directory
- Open Naukri-auto-apply-bot python file and paste the path in profiles variable
- Add your Firstname, Lastname, keywords(Job roles) and location(optional)
- Run the Naukri-autoapply bot by python Naukri-Mozilla.py
- Please note this script was built in selenium version 3

## Steps to run the Edge file
- Download Edge driver and include its filepath in the naukri-Edge file
- Add your Firstname, Lastname, keywords(Job roles) and location(optional)
- Run the Naukri-autoapply bot by python Naukri-Edge.py
- Please note this script was built in selenium version 4


## Pacing / delay controls
Both scripts now support delay controls to mimic human timing and debug pacing decisions.

Example:
- `python Naukri-Mozilla.py --min-delay 1.5 --max-delay 3.5 --cooldown-every 10 --cooldown-min-delay 20 --cooldown-max-delay 45`
- `python Naukri-Edge.py --min-delay 1.5 --max-delay 3.5 --cooldown-every 10 --cooldown-min-delay 20 --cooldown-max-delay 45`

Arguments:
- `--min-delay` / `--max-delay`: random delay range used before apply clicks, submit actions, and between jobs/pages.
- `--cooldown-every`: apply an optional longer cool-down every N successful applications (`0` disables).
- `--cooldown-min-delay` / `--cooldown-max-delay`: random delay range for the periodic cool-down.

Each delay is logged to stdout with reason and selected duration.
