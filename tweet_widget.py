############################################################################
## Django ORM Standalone
############################################################################

# Turn off bytecode generation
import sys
sys.dont_write_bytecode = True

# Django specific settings
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
import django
django.setup()

# Import your models for use in your script
from db.models import *

############################################################################
# TWITTER WIDGET V5
# by chaos_a

import praw
import prawcore
import configparser
import logging
import tweepy
from datetime import datetime, timedelta
import time
import sys

script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBot.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

def main():
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")

    r = redditlogin(botconfig)
    tAuth = tweepy.OAuthHandler(botconfig.get("twitter", "APIKey"), botconfig.get("twitter", "APISecret"))
    tAuth.set_access_token(botconfig.get("twitter", "AccessToken"), botconfig.get("twitter", "TokenSecret"))
    tApi = tweepy.API(tAuth)
    Subreddit.r = r
    Subreddit.tApi = tApi

    while True:
        for sub in Subreddit.objects.all():
            try:
                sub.loadConfig()
                
                if datetime.utcnow().timestamp() > sub.nextImageUploadTimestamp or sub.configChanged:
                    logging.info(f"Uploading images to subreddit {sub.subname}")
                    sub.loadWidgetMembers() # force reload the widget members
                    sub.uploadImages()
                    sub.bugFixImageUpload = True
                    sub.nextImageUploadTimestamp = (datetime.utcnow() + timedelta(days=3)).timestamp()
                    logging.info(f"Done uploading images to {sub.subname}, next upload is scheduled for: {datetime.utcfromtimestamp(sub.nextImageUploadTimestamp)}")
                sub.updateWidget()
            except prawcore.exceptions.Forbidden as ef:
                # if tweet widget gets removed, this handles removing that subreddit
                try:
                    if "tweet_widget" not in sub.subreddit.moderator():
                        logging.warning(f"{sub.subname}: Deleting  subreddit record. No longer a moderator.")
                        Subreddit.objects.filter(subname=sub.subname).delete()
                    else:
                        logging.error(f"{sub.subname}: 403 Forbidden: {ef}")
                except prawcore.exceptions.Forbidden as e:
                    logging.warning(f"{sub.subname}: Deleting private subreddit record. (Recieved 403 when trying to access mod list)")
                    Subreddit.objects.filter(subname=sub.subname).delete()
                
            except Exception as e:
                logging.error(f"{sub.subname}: Unhandled error: {e}")
        logging.info("Done with widgets, waiting 5 mins")
        #time.sleep(15)
        time.sleep(300)


def redditlogin(botconfig):
    # reddit login
    try:
        r = praw.Reddit(client_id=botconfig.get("reddit", "clientID"),
                        client_secret=botconfig.get("reddit", "clientSecret"),
                        password=botconfig.get("reddit", "password"),
                        user_agent=botconfig.get("reddit", "useragent"),
                        username=botconfig.get("reddit", "username"))
        return r  # return reddit instance
    except Exception as e:  # reddit is down
        logging.error("Reddit/PRAW Issue, site may be down")
        time.sleep(120)

if __name__ == "__main__":
    main()